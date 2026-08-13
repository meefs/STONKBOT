"""Orchestrate prepare → (sign) → submit with security + fee model."""

from __future__ import annotations

from .config import get_settings
from .models import LaunchRequest, LaunchResult
from .responses import dry_run as resp_dry, success as resp_success, error as resp_error
from .security import guard
from .stonkfun_client import StonkFunClient, StonkFunError


def preview(req: LaunchRequest) -> LaunchResult:
    """Forge + show what would be sent. Never touches chain."""
    settings = get_settings()
    client = StonkFunClient()
    try:
        pairs = client.list_pairs(launchable=True)
        quote = next((p for p in pairs if p.mint == req.quote_mint or p.symbol.upper() == req.quote_mint.upper()), None)
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


def run_launch(req: LaunchRequest, signed_tx_b64: str | None = None, signed_quote: str | None = None) -> LaunchResult:
    """
    Full path.
    - dry_run=True → never submits
    - live → expects caller to have signed the payment tx from prepare
    Service fee (0.1 SOL) is recorded; actual transfer is handled by fee module / operator flow.
    """
    settings = get_settings()
    ok, reason = guard.can_launch()
    if not ok and reason != "dry_run":
        return LaunchResult(status="failed", message=resp_error(reason))

    client = StonkFunClient()
    try:
        if settings.dry_run or not signed_tx_b64:
            prepared = client.prepare_launch(req) if not settings.dry_run else {}
            pairs = client.list_pairs(launchable=True)
            quote = next((p for p in pairs if p.mint == req.quote_mint or p.symbol.upper() == req.quote_mint.upper()), None)
            quote_sym = quote.symbol if quote else req.quote_mint[:8]
            return LaunchResult(
                status="dry_run",
                message=resp_dry(req.symbol, quote_sym),
                service_fee_sol=settings.service_fee_sol,
                raw={"prepared": prepared, "note": "dry_run — no submit"},
            )

        # Live path: prepare already done by caller, we only submit
        if not signed_quote or not signed_tx_b64:
            return LaunchResult(status="failed", message=resp_error("missing signed payload"))

        result = client.submit_launch(signed_quote, signed_tx_b64, logo=req.logo_data_uri)
        status = result.get("status", "processing")
        mint = result.get("mint")
        sig = result.get("paymentSignature") or result.get("payment_signature")

        if status in ("completed", "processing"):
            guard.on_success()
            url = f"https://www.stonkfun.xyz/token/{mint}" if mint else "https://www.stonkfun.xyz"
            pairs = client.list_pairs(launchable=True)
            quote = next((p for p in pairs if p.mint == req.quote_mint), None)
            quote_sym = quote.symbol if quote else "quote"
            return LaunchResult(
                status="completed" if status == "completed" else "processing",
                mint=mint,
                payment_signature=sig,
                stonkfun_url=url,
                service_fee_sol=settings.service_fee_sol,
                service_fee_paid=False,  # collected out-of-band / separate transfer
                message=resp_success(req.symbol, quote_sym, url),
                raw=result,
            )

        guard.on_failure()
        return LaunchResult(status="failed", message=resp_error(str(result)), raw=result)

    except StonkFunError as e:
        guard.on_failure()
        return LaunchResult(status="failed", message=resp_error(str(e)))
    finally:
        client.close()
