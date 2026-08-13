"""Thin client for StonkFun public API. No API key required."""

from __future__ import annotations

import httpx
from .config import get_settings
from .models import QuotePair, LaunchRequest


class StonkFunError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class StonkFunClient:
    def __init__(self, base_url: str | None = None, timeout: float = 30.0):
        settings = get_settings()
        self.base = (base_url or settings.stonkfun_api_base).rstrip("/")
        self._client = httpx.Client(timeout=timeout, headers={"Content-Type": "application/json"})

    def _call(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base}{path}"
        r = self._client.request(method, url, **kwargs)
        body = r.json()
        if not r.is_success:
            err = body.get("error") or {}
            raise StonkFunError(err.get("code", "http_error"), err.get("message", r.text))
        return body.get("data", body)

    def list_pairs(self, launchable: bool = True) -> list[QuotePair]:
        q = {"launchable": "true"} if launchable else {}
        data = self._call("GET", "/pairs", params=q)
        pairs = data.get("pairs") or data if isinstance(data, list) else data.get("pairs", [])
        out = []
        for p in pairs:
            out.append(
                QuotePair(
                    mint=p.get("mint") or p.get("address", ""),
                    symbol=p.get("symbol", ""),
                    name=p.get("name"),
                    category=p.get("category"),
                    launchable=p.get("launchable", True),
                )
            )
        return out

    def prepare_launch(self, req: LaunchRequest) -> dict:
        payload: dict = {
            "creatorWallet": req.creator_wallet,
            "quoteMint": req.quote_mint,
            "name": req.name,
            "symbol": req.symbol,
            "mode": req.mode,
        }
        if req.logo_data_uri:
            payload["logo"] = req.logo_data_uri
        if req.dev_buy_percent is not None:
            payload["devBuyPercent"] = min(req.dev_buy_percent, 2.5)
        if req.website:
            payload["website"] = req.website
        if req.twitter:
            payload["twitter"] = req.twitter
        return self._call("POST", "/launches/prepare", json=payload)

    def submit_launch(self, signed_quote: str, signed_transaction_b64: str, logo: str | None = None) -> dict:
        payload = {
            "signedQuote": signed_quote,
            "signedTransaction": signed_transaction_b64,
        }
        if logo:
            payload["logo"] = logo
        return self._call("POST", "/launches/submit", json=payload)

    def get_launch(self, payment_signature: str) -> dict:
        return self._call("GET", f"/launches/{payment_signature}")

    def get_token(self, mint: str) -> dict:
        return self._call("GET", f"/tokens/{mint}")

    def close(self) -> None:
        self._client.close()
