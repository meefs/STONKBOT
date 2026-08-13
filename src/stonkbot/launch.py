"""Launch orchestration — the user's agent wallet is both creator and signer.

Flow, with the safety property each step provides:

  guard          rate limit / circuit breaker / daily budget
  idempotency    one launch per command, so a retried mention cannot pay twice
  wallet lookup  scoped to the requesting handle only
  prepare        quote the launch (charges nothing)
  txguard        verify the payment transaction before signing it
  balance        check against the *quoted* cost, not a hardcoded guess
  sign + submit  the only step that spends money
  confirm        poll to a terminal state before reporting success
  fees           STONKBOT's own fee, split with a referrer when there is one
"""

from __future__ import annotations

import logging
import time

from .config import get_settings
from .fees import mark_failed, mark_paid, record_expected
from .idempotency import LaunchAlreadyRunning, claim, release, resolve
from .models import LaunchCost, LaunchRequest, LaunchResult
from .responses import dry_run as resp_dry
from .responses import error as resp_error
from .responses import success as resp_success
from .security import guard
from .solana_pay import get_balance_sol, transfer_sol
from .stonkfun_client import StonkFunClient, StonkFunError
from .txguard import UnsafeTransaction, inspect_payment_transaction
from .vault import get as get_agent
from .vault import sign_transaction_b64

log = logging.getLogger("stonkbot.launch")

# How long to wait for StonkFun to move a launch from 'processing' to a
# terminal state before we report it as still in flight.
_POLL_ATTEMPTS = 12
_POLL_INTERVAL_SECONDS = 5.0

STONKFUN_TOKEN_URL = "https://www.stonkfun.xyz/token/{mint}"


def preview(req: LaunchRequest, quote_symbol: str | None = None) -> LaunchResult:
    """Describe a launch without touching the chain."""
    settings = get_settings()
    with StonkFunClient() as client:
        try:
            pair = client.find_pair(req.quote_mint)
        except StonkFunError as e:
            return LaunchResult(status="failed", message=resp_error(str(e)))

    if not pair:
        return LaunchResult(
            status="failed", message=resp_error(f"quote not launchable: {req.quote_mint}")
        )

    return LaunchResult(
        status="dry_run",
        message=resp_dry(req.symbol, quote_symbol or pair.symbol),
        service_fee_sol=settings.service_fee_sol,
        raw={"request": req.model_dump()},
    )


def _pay_service_fee(
    x_handle: str, mint: str | None, ref_handle: str | None
) -> tuple[bool, str | None, dict]:
    """Charge STONKBOT's fee and rebate the referrer.

    Runs only after a launch has succeeded. A failure here is recorded as an
    unsettled debt rather than being swallowed — it never invalidates the
    user's launch, which is already on chain.
    """
    settings = get_settings()

    ref_recipient = None
    if ref_handle:
        referrer = get_agent(ref_handle)
        ref_recipient = referrer.pubkey if referrer else None

    if settings.dry_run:
        # Recording here would write debt to the ledger that is never settled.
        return False, None, {}

    owed = record_expected(
        x_handle, mint, ref_handle=ref_handle, ref_recipient=ref_recipient
    )

    platform_signature: str | None = None
    platform_paid = False

    try:
        platform_signature = transfer_sol(
            x_handle, owed["platform_recipient"], owed["platform_sol"]
        )
        mark_paid(owed["platform_id"], platform_signature)
        platform_paid = True
    except Exception as e:
        log.error("service fee transfer failed for @%s: %s", x_handle, e)
        mark_failed(owed["platform_id"])

    if owed["referrer_id"] and owed["referrer_recipient"]:
        try:
            ref_signature = transfer_sol(
                x_handle, owed["referrer_recipient"], owed["referrer_sol"]
            )
            mark_paid(owed["referrer_id"], ref_signature)
        except Exception as e:
            log.error("referral rebate failed for @%s: %s", ref_handle, e)
            mark_failed(owed["referrer_id"])

    return platform_paid, platform_signature, owed


def run_launch(
    req: LaunchRequest,
    x_handle: str,
    *,
    ref_handle: str | None = None,
    idempotency_key: str | None = None,
) -> LaunchResult:
    """Execute a launch on behalf of ``x_handle``.

    ``idempotency_key`` should identify the originating command (for X, the
    tweet id). It ensures a mention replayed by the timeline cannot produce a
    second paid launch — StonkFun's docs are explicit that a second payment
    creates a second token.
    """
    settings = get_settings()

    agent = get_agent(x_handle)
    if not agent:
        return LaunchResult(status="failed", message=resp_error("register first"))

    # --- idempotency ------------------------------------------------------
    # Claimed before the rate-limit check so replaying a completed launch is
    # free: a replay does no work, and must not burn the user's allowance.
    key = idempotency_key or f"{x_handle}:{req.symbol}:{req.quote_mint}"
    try:
        previous = claim(key)
    except LaunchAlreadyRunning:
        return LaunchResult(
            status="failed",
            message=resp_error("that launch is already in progress"),
        )
    if previous is not None:
        # Already completed once — replay the original outcome, never re-pay.
        log.info("replaying completed launch for key=%s", key)
        return LaunchResult(**previous)

    ok, reason = guard.can_launch(x_handle)
    if not ok:
        # Nothing was attempted, so the key is free for a genuine retry.
        release(key)
        return LaunchResult(status="failed", message=resp_error(reason))

    try:
        result = _run_launch_inner(req, x_handle, agent.pubkey, ref_handle, settings)
    except Exception as e:
        # We cannot know whether the payment landed, so the key is deliberately
        # NOT released: a retry under the same key could pay twice.
        guard.on_failure()
        log.exception("unexpected launch failure")
        return LaunchResult(status="failed", message=resp_error(str(e)))

    if result.status in ("completed", "processing"):
        # Record the terminal outcome so a replay returns it instead of paying.
        resolve(key, result.model_dump(mode="json"))
    elif result.raw.get("safe_to_retry"):
        # Only released on paths that provably charged nothing — everything
        # before submit, plus a submit that StonkFun confirmed was not charged.
        # A `conflict` (payment landed) or an ambiguous failure keeps the key,
        # because releasing it would re-arm the double-payment this guards.
        release(key)
    return result


def _run_launch_inner(
    req: LaunchRequest,
    x_handle: str,
    agent_pubkey: str,
    ref_handle: str | None,
    settings,
) -> LaunchResult:
    with StonkFunClient() as client:
        pair = client.find_pair(req.quote_mint)
        if not pair:
            return LaunchResult(
                status="failed",
                message=resp_error(f"quote not launchable: {req.quote_mint}"),
                raw={"safe_to_retry": True},
            )
        quote_symbol = pair.symbol
        # Always submit the canonical mint, never a user-typed symbol.
        req = req.model_copy(
            update={"quote_mint": pair.mint, "creator_wallet": agent_pubkey}
        )

        if settings.dry_run:
            return LaunchResult(
                status="dry_run",
                message=resp_dry(req.symbol, quote_symbol),
                service_fee_sol=settings.service_fee_sol,
                raw={"creator": agent_pubkey, "note": "dry_run", "safe_to_retry": True},
            )

        # --- quote the launch (charges nothing) ---------------------------
        try:
            prepared = client.prepare_launch(req)
        except StonkFunError as e:
            guard.on_failure()
            # /prepare only quotes; it never charges.
            return LaunchResult(
                status="failed",
                message=resp_error(e.message),
                raw={"safe_to_retry": True},
            )

        payment_b64 = prepared.get("paymentTransaction") or prepared.get(
            "payment_transaction"
        )
        signed_quote = prepared.get("signedQuote") or prepared.get("signed_quote")
        if not payment_b64 or not signed_quote:
            guard.on_failure()
            return LaunchResult(
                status="failed",
                message=resp_error("StonkFun returned an incomplete quote"),
                raw={"safe_to_retry": True},
            )

        # --- verify before signing ----------------------------------------
        try:
            inspection = inspect_payment_transaction(payment_b64, agent_pubkey)
        except UnsafeTransaction as e:
            guard.on_failure()
            log.error("refused to sign payment transaction: %s", e)
            return LaunchResult(
                status="failed",
                message=resp_error(f"safety check failed: {e}"),
                raw={"safe_to_retry": True},
            )

        cost = LaunchCost(
            stonkfun_cost_sol=round(inspection.cost_sol, 6),
            service_fee_sol=settings.service_fee_sol,
            network_reserve_sol=settings.network_fee_reserve_sol,
            measured=inspection.cost_is_simulated,
        )

        # --- balance check against the real quote -------------------------
        balance = get_balance_sol(agent_pubkey)
        if balance < cost.total_sol:
            return LaunchResult(
                status="failed",
                cost=cost,
                message=resp_error(
                    f"need {cost.total_sol:.3f} SOL, wallet has {balance:.3f}"
                ),
                raw={"pubkey": agent_pubkey, "balance": balance, "safe_to_retry": True},
            )

        # --- sign and submit ----------------------------------------------
        try:
            signed_tx = sign_transaction_b64(x_handle, payment_b64)
        except Exception as e:
            guard.on_failure()
            log.error("signing failed for @%s", x_handle)
            return LaunchResult(
                status="failed",
                message=resp_error(f"signing failed: {e}"),
                raw={"safe_to_retry": True},
            )

        try:
            result = client.submit_launch(
                signed_quote, signed_tx, logo=req.logo_data_uri
            )
        except StonkFunError as e:
            guard.on_failure()
            if e.code == "conflict":
                # Payment landed but the launch needs manual recovery. Retrying
                # or re-paying here would mint a second token.
                log.error("launch conflict for @%s — manual recovery required", x_handle)
                return LaunchResult(
                    status="failed",
                    cost=cost,
                    message=resp_error(
                        "payment landed but the launch needs manual recovery — "
                        "do not retry, we're on it"
                    ),
                    raw={"code": e.code, "needs_recovery": True},
                )
            if e.definitely_not_charged:
                return LaunchResult(
                    status="failed",
                    cost=cost,
                    message=resp_error("StonkFun was busy — nothing charged, try again"),
                    raw={"code": e.code, "charged": False, "safe_to_retry": True},
                )
            # Unknown whether the payment landed: say so rather than guessing.
            return LaunchResult(
                status="failed",
                cost=cost,
                message=resp_error(f"launch failed ({e.code}) — check before retrying"),
                raw={"code": e.code},
            )

        status = result.get("status", "processing")
        mint = result.get("mint")
        signature = result.get("paymentSignature") or result.get("payment_signature")

        # --- confirm before reporting success -----------------------------
        if status == "processing" and signature:
            for _ in range(_POLL_ATTEMPTS):
                time.sleep(_POLL_INTERVAL_SECONDS)
                try:
                    polled = client.get_launch(signature)
                except StonkFunError as e:
                    log.warning("poll failed: %s", e)
                    continue
                status = polled.get("status", status)
                mint = polled.get("mint") or mint
                if status in ("completed", "failed"):
                    result = polled
                    break

    if status == "failed":
        guard.on_failure()
        return LaunchResult(
            status="failed",
            cost=cost,
            message=resp_error("StonkFun reported the launch failed"),
            raw=result,
        )

    if not mint:
        # On chain per StonkFun's contract, but we have no mint yet. Do not
        # claim success and do not charge — this is reported as in flight.
        guard.on_failure()
        return LaunchResult(
            status="processing",
            payment_signature=signature,
            cost=cost,
            message=resp_error("launch is still settling — check back shortly"),
            raw=result,
        )

    guard.on_success()
    url = STONKFUN_TOKEN_URL.format(mint=mint)
    fee_paid, fee_signature, _ = _pay_service_fee(x_handle, mint, ref_handle)

    return LaunchResult(
        status="completed" if status == "completed" else "processing",
        mint=mint,
        payment_signature=signature,
        stonkfun_url=url,
        service_fee_sol=settings.service_fee_sol,
        service_fee_paid=fee_paid,
        service_fee_signature=fee_signature,
        cost=cost,
        message=resp_success(req.symbol, quote_symbol, url),
        raw=result,
    )
