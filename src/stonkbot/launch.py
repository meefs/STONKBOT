"""Fully automated launch: bot hot wallet = creator, signs + submits."""

from __future__ import annotations

import time

from .config import get_settings
from .fees import record_expected
from .models import LaunchRequest, LaunchResult
from .responses import dry_run as resp_dry, success as resp_success, error as resp_error
from .security import guard
from .stonkfun_client import StonkFunClient, StonkFunError


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
            raw={"request": req.model_dump(), "quote": quote.model_dump() if quote else None},
        )
    except StonkFunError as e:
        return LaunchResult(status="failed", message=resp_error(str(e)))
    finally:
        client.close()


def run_launch(
    req: LaunchRequest,
    x_handle: str | None = None,
) -> LaunchResult:
    """
    Automated path:
    - dry_run → preview only
    - live → prepare with bot hot wallet as creator → sign → submit → poll
    """
    settings = get_settings()
    ok, reason = guard.can_launch()
    if not ok and reason != "dry_run":
        return LaunchResult(status="failed", message=resp_error(reason))

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
                raw={"note": "dry_run — no submit"},
            )

        # Force creator = bot hot wallet for automation
        from .wallet import pubkey_str, sign_transaction_b64, WalletError

        try:
            bot_pubkey = pubkey_str()
        except WalletError as e:
            return LaunchResult(status="failed", message=resp_error(str(e)))

        req = req.model_copy(update={"creator_wallet": bot_pubkey})

        prepared = client.prepare_launch(req)
        payment_b64 = prepared.get("paymentTransaction") or prepared.get("payment_transaction")
        signed_quote = prepared.get("signedQuote") or prepared.get("signed_quote")
        if not payment_b64 or not signed_quote:
            return LaunchResult(status="failed", message=resp_error("prepare missing payment fields"), raw=prepared)

        try:
            signed_tx = sign_transaction_b64(payment_b64)
        except Exception as e:
            guard.on_failure()
            return LaunchResult(status="failed", message=resp_error(f"sign failed: {e}"))

        result = client.submit_launch(signed_quote, signed_tx, logo=req.logo_data_uri)
        status = result.get("status", "processing")
        mint = result.get("mint")
        sig = result.get("paymentSignature") or result.get("payment_signature")

        # poll if processing
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
            if x_handle:
                record_expected(x_handle, mint)
            url = f"https://www.stonkfun.xyz/token/{mint}"
            return LaunchResult(
                status="completed" if status == "completed" else "processing",
                mint=mint,
                payment_signature=sig,
                stonkfun_url=url,
                service_fee_sol=settings.service_fee_sol,
                service_fee_paid=False,
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
