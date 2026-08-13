"""Client for the StonkFun public API (https://www.stonkfun.xyz/developers).

No API key exists or is required — what authorises a launch is the creator's
own signature on the payment transaction.

Error handling follows StonkFun's documented contract, because the difference
between the codes is the difference between "retry" and "you just paid twice":

  conflict (409)            payment landed, needs manual recovery — NEVER re-pay
  service_unavailable (503) with charged=false — nothing charged, safe to retry
                            from a fresh /prepare quote
  rate_limited (429)        honour Retry-After
"""

from __future__ import annotations

import logging
import time

import httpx

from .config import get_settings
from .models import LaunchRequest, QuotePair

log = logging.getLogger("stonkbot.stonkfun")

# Codes where the caller must not retry or re-submit a payment.
TERMINAL_CODES = {"conflict", "forbidden", "invalid_request", "not_found",
                  "method_not_allowed"}


class StonkFunError(Exception):
    def __init__(self, code: str, message: str, charged: bool | None = None):
        self.code = code
        self.message = message
        # None = unknown. Only an explicit False means "definitely not charged".
        self.charged = charged
        super().__init__(f"{code}: {message}")

    @property
    def is_terminal(self) -> bool:
        """True when retrying is unsafe or pointless."""
        return self.code in TERMINAL_CODES

    @property
    def definitely_not_charged(self) -> bool:
        """True only when StonkFun explicitly confirmed nothing was charged."""
        return self.charged is False


class StonkFunClient:
    """Synchronous client. Use as a context manager so the pool is closed."""

    def __init__(self, base_url: str | None = None, timeout: float = 30.0):
        settings = get_settings()
        self.base = (base_url or settings.stonkfun_api_base).rstrip("/")
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "stonkbot/1.0 (+https://github.com/PhantomCapAI/STONKBOT)",
            },
        )

    def __enter__(self) -> StonkFunClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _call(self, method: str, path: str, retries: int = 2, **kwargs) -> dict:
        url = f"{self.base}{path}"
        last_error: Exception | None = None

        for attempt in range(retries + 1):
            try:
                r = self._client.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                # A timeout on a *write* is ambiguous — we cannot know whether
                # the server processed it, so writes are never auto-retried.
                last_error = e
                if method.upper() != "GET" or attempt == retries:
                    raise StonkFunError("network_error", str(e)) from e
                time.sleep(0.5 * (2**attempt))
                continue

            try:
                body = r.json()
            except ValueError as e:
                raise StonkFunError(
                    "invalid_response", f"non-JSON response (HTTP {r.status_code})"
                ) from e

            if r.is_success:
                data = body.get("data", body)
                if not isinstance(data, dict):
                    raise StonkFunError("invalid_response", "unexpected response shape")
                return data

            err = body.get("error") or {}
            code = err.get("code", "http_error")
            message = err.get("message", f"HTTP {r.status_code}")
            charged = body.get("charged", err.get("charged"))

            if code == "rate_limited" and attempt < retries and method.upper() == "GET":
                delay = float(r.headers.get("Retry-After", 2))
                time.sleep(min(delay, 30))
                continue

            raise StonkFunError(code, message, charged=charged)

        raise StonkFunError("network_error", str(last_error))

    def list_pairs(self, launchable: bool = True) -> list[QuotePair]:
        params = {"launchable": "true"} if launchable else {}
        data = self._call("GET", "/pairs", params=params)

        raw_pairs = data.get("pairs")
        if not isinstance(raw_pairs, list):
            raise StonkFunError("invalid_response", "pairs missing from response")

        pairs: list[QuotePair] = []
        for p in raw_pairs:
            if not isinstance(p, dict):
                continue
            mint = p.get("mint") or p.get("address") or ""
            symbol = p.get("symbol") or ""
            if not mint or not symbol:
                continue
            pairs.append(
                QuotePair(
                    mint=mint,
                    symbol=symbol,
                    name=p.get("name"),
                    category=p.get("category"),
                    launchable=bool(p.get("launchable", True)),
                )
            )
        return pairs

    def find_pair(self, query: str, launchable: bool = True) -> QuotePair | None:
        """Resolve a user-supplied quote (symbol or mint) to a launchable pair."""
        if not query:
            return None
        needle = query.strip()
        for pair in self.list_pairs(launchable=launchable):
            if pair.mint == needle or pair.symbol.upper() == needle.upper():
                return pair
        return None

    def prepare_launch(self, req: LaunchRequest) -> dict:
        """Get an unsigned payment transaction. Nothing is charged by this call."""
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
            payload["devBuyPercent"] = req.dev_buy_percent
        if req.twitter:
            payload["twitter"] = req.twitter
        # StonkFun sets the website itself and rejects a custom one, so we never
        # send req.website.
        return self._call("POST", "/launches/prepare", retries=0, json=payload)

    def submit_launch(
        self, signed_quote: str, signed_transaction_b64: str, logo: str | None = None
    ) -> dict:
        """Submit the signed payment. This is the call that spends money."""
        payload = {
            "signedQuote": signed_quote,
            "signedTransaction": signed_transaction_b64,
        }
        if logo:
            # Must be byte-identical to the logo sent to /prepare.
            payload["logo"] = logo
        return self._call("POST", "/launches/submit", retries=0, json=payload)

    def get_launch(self, payment_signature: str) -> dict:
        return self._call("GET", f"/launches/{payment_signature}")

    def get_token(self, mint: str) -> dict:
        return self._call("GET", f"/tokens/{mint}")

    def get_stats(self) -> dict:
        return self._call("GET", "/stats")
