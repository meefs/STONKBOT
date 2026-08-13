"""Launch orchestration — user agent wallet is creator + signer."""

from __future__ import annotations

import time

from .config import get_settings
from .fees import record_expected
from .models import LaunchRequest, LaunchResult
from .responses import dry_run as resp_dry, success as resp_success, error as resp_error
from .security import guard
from .solana_pay import get_balance_sol, transfer_sol
from .stonkfun_client import StonkFunClient, StonkFunError
from .vault import get as get_agent, sign_tx_b64


def preview(req: LaunchRequest) -> LaunchResult:
    settings = get_settings()
    client = StonkFunClient()
    try:
        pairs = client.list_pairs(launchable=True)
        quote = next(
            (p for p in pairs if p.mint == req.quote_mint or p.symbol.upper() == req.quote_mint.upper()),
            None,
        )
        quote_sym = quote.symbol if quote else req.quote_mint[:8]
        return LaunchResult(
            status="dry_run",
            message=resp_dry(req.symbol, quote_sym),
            service_fee_sol=settings.service_fee_sol,
            raw={"request": req.model_dump()},
        )
    except StonkFunError as e:
        return LaunchResult(status="failed", message=resp_error(str(e)))
    finally:
        client.close()


def run_launch(req: LaunchRequest, x_handle: str) -> LaunchResult:
    """
    Bankr model:
    - agent wallet for x_handle is creator
    - agent wallet signs StonkFun payment
    - on success, 0.1 SOL service fee → operator fee_recipient
    """
    settings = get_settings()
    ok, reason = guard.can_launch()
    if not ok and reason != "dry_run":
        return LaunchResult(status="failed", message=resp_error(reason))

    agent = get_agent(x_handle)
    if not agent:
        return LaunchResult(status="failed", message=resp_error("register first"))

    client = StonkFunClient()
    try:
        pairs = client.list_pairs(launchable=True)
        quote = next(
            (p for p in pairs if p.mint == req.quote_mint or p.symbol.upper() == req.quote_mint.upper()),
            None,
        )
        quote_sym = quote.symbol if quote else req.quote_mint[:8]

        if settings.dry_run:
            return LaunchResult(
                status="dry_run",
                message=resp_dry(req.symbol, quote_sym),
                service_fee_sol=settings.service_fee_sol,
                raw={"creator": agent.pubkey, "note": "dry_run"},
            )

        bal = get_balance_sol(agent.pubkey)
        if bal < settings.min_launch_balance_sol:
            return LaunchResult(
                status="failed",
                message=resp_error(
                    f"fund agent wallet first. need ~{settings.min_launch_balance_sol} SOL, have {bal:.3f}"
                ),
                raw={"pubkey": agent.pubkey, "balance": bal},
            )

        req = req.model_copy(update={"creator_wallet": agent.pubkey})
        prepared = client.prepare_launch(req)
        payment_b64 = prepared.get("paymentTransaction") or prepared.get("payment_transaction")
        signed_quote = prepared.get("signedQuote") or prepared.get("signed_quote")
        if not payment_b64 or not signed_quote:
            return LaunchResult(status="failed", message=resp_error("prepare missing fields"), raw=prepared)

        try:
            signed_tx = sign_tx_b64(x_handle, payment_b64)
        except Exception as e:
            guard.on_failure()
            return LaunchResult(status="failed", message=resp_error(f"sign failed: {e}"))

        result = client.submit_launch(signed_quote, signed_tx, logo=req.logo_data_uri)
        status = result.get("status", "processing")
        mint = result.get("mint")
        sig = result.get("paymentSignature") or result.get("payment_signature")

        if status == "processing" and sig:
            for _ in range(12):
                time.sleep(5)
                try:
                    result = client.get_launch(sig)
                    status = result.get("status", status)
                    mint = result.get("mint") or mint
                    if status in ("completed", "failed"):
                        break
                except Exception:
                    break

        if status in ("completed", "processing") and mint:
            guard.on_success()
            fee_paid = False
            try:
                fee_sig = transfer_sol(x_handle, settings.fee_recipient, settings.service_fee_sol)
                fee_paid = fee_sig is not None or settings.dry_run
            except Exception:
                fee_paid = False
            record_expected(x_handle, mint)
            url = f"https://www.stonkfun.xyz/token/{mint}"
            return LaunchResult(
                status="completed" if status == "completed" else "processing",
                mint=mint,
                payment_signature=sig,
                stonkfun_url=url,
                service_fee_sol=settings.service_fee_sol,
                service_fee_paid=fee_paid,
                message=resp_success(req.symbol, quote_sym, url),
                raw=result,
            )

        guard.on_failure()
        return LaunchResult(status="failed", message=resp_error(str(result)), raw=result)

    except StonkFunError as e:
        guard.on_failure()
        return LaunchResult(status="failed", message=resp_error(str(e)))
    except Exception as e:
        guard.on_failure()
        return LaunchResult(status="failed", message=resp_error(str(e)))
    finally:
        client.close()
