"""Minimal Solana JSON-RPC client built on httpx.

solana-py dropped its synchronous ``Client`` (it only ships ``AsyncClient`` as
of 0.40), so the previous ``from solana.rpc.api import Client`` import failed at
runtime. Everything here needs is a handful of RPC methods, so we speak
JSON-RPC directly and keep ``solders`` for the types. That also gives us
explicit timeouts, bounded retries and a single place to reason about
commitment levels.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .config import get_settings

log = logging.getLogger("stonkbot.rpc")

LAMPORTS_PER_SOL = 1_000_000_000

# Retried on: transient transport faults and 5xx/429 from the RPC provider.
_RETRY_STATUS = {429, 500, 502, 503, 504}


class RpcError(Exception):
    """An RPC call failed or returned an error object."""


def lamports_to_sol(lamports: int) -> float:
    return lamports / LAMPORTS_PER_SOL


def sol_to_lamports(sol: float) -> int:
    # round() not int(): float maths on values like 0.1 otherwise truncates low.
    return int(round(sol * LAMPORTS_PER_SOL))


class SolanaRpc:
    """Small synchronous RPC client. Use as a context manager."""

    def __init__(self, url: str | None = None, timeout: float | None = None):
        s = get_settings()
        self.url = url or s.solana_rpc_url
        self._client = httpx.Client(
            timeout=timeout or s.rpc_timeout_seconds,
            headers={"Content-Type": "application/json"},
        )
        self._id = 0

    def __enter__(self) -> SolanaRpc:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _call(self, method: str, params: list[Any], retries: int = 3) -> Any:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        last: Exception | None = None

        for attempt in range(retries):
            try:
                r = self._client.post(self.url, json=payload)
                if r.status_code in _RETRY_STATUS:
                    last = RpcError(f"{method}: HTTP {r.status_code}")
                    time.sleep(0.5 * (2**attempt))
                    continue
                r.raise_for_status()
                body = r.json()
                if "error" in body:
                    err = body["error"]
                    # RPC-level errors are deterministic; retrying won't help.
                    raise RpcError(f"{method}: {err.get('message', err)}")
                return body.get("result")
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = e
                time.sleep(0.5 * (2**attempt))
            except httpx.HTTPStatusError as e:
                raise RpcError(f"{method}: HTTP {e.response.status_code}") from e

        raise RpcError(f"{method}: failed after {retries} attempts: {last}")

    # --- reads --------------------------------------------------------------

    def get_balance_lamports(self, pubkey: str, commitment: str = "confirmed") -> int:
        res = self._call("getBalance", [pubkey, {"commitment": commitment}])
        return int((res or {}).get("value", 0))

    def get_balance_sol(self, pubkey: str, commitment: str = "confirmed") -> float:
        return lamports_to_sol(self.get_balance_lamports(pubkey, commitment))

    def get_latest_blockhash(self, commitment: str = "confirmed") -> tuple[str, int]:
        res = self._call("getLatestBlockhash", [{"commitment": commitment}])
        value = (res or {}).get("value", {})
        return value.get("blockhash", ""), int(value.get("lastValidBlockHeight", 0))

    def get_multiple_balances(self, pubkeys: list[str]) -> dict[str, int]:
        """Batch balance lookup via getMultipleAccounts (missing account -> 0)."""
        if not pubkeys:
            return {}
        res = self._call(
            "getMultipleAccounts",
            [pubkeys, {"commitment": "confirmed", "encoding": "base64"}],
        )
        values = (res or {}).get("value", []) or []
        out: dict[str, int] = {}
        for key, acc in zip(pubkeys, values, strict=False):
            out[key] = int(acc.get("lamports", 0)) if acc else 0
        return out

    def simulate_transaction_balances(
        self, tx_b64: str, accounts: list[str]
    ) -> dict[str, int] | None:
        """Simulate an unsigned tx and return post-simulation lamport balances.

        Returns ``None`` when the RPC provider cannot simulate it (some
        providers reject unsigned transactions). Callers must treat ``None`` as
        "unknown", never as "safe".
        """
        try:
            res = self._call(
                "simulateTransaction",
                [
                    tx_b64,
                    {
                        "sigVerify": False,
                        "replaceRecentBlockhash": True,
                        "commitment": "confirmed",
                        "encoding": "base64",
                        "accounts": {"encoding": "base64", "addresses": accounts},
                    },
                ],
                retries=1,
            )
        except RpcError as e:
            log.warning("simulation unavailable: %s", e)
            return None

        value = (res or {}).get("value") or {}
        if value.get("err") is not None:
            log.warning("simulation returned an error: %s", value.get("err"))
            return None

        sim_accounts = value.get("accounts") or []
        if len(sim_accounts) != len(accounts):
            return None

        out: dict[str, int] = {}
        for key, acc in zip(accounts, sim_accounts, strict=False):
            # A null entry means the account does not exist post-simulation.
            out[key] = int(acc.get("lamports", 0)) if acc else 0
        return out

    # --- writes -------------------------------------------------------------

    def send_raw_transaction(self, tx_b64: str, skip_preflight: bool = False) -> str:
        res = self._call(
            "sendTransaction",
            [
                tx_b64,
                {
                    "encoding": "base64",
                    "skipPreflight": skip_preflight,
                    "preflightCommitment": "confirmed",
                    "maxRetries": 3,
                },
            ],
            retries=1,  # never blind-resend a payment
        )
        if not res:
            raise RpcError("sendTransaction returned no signature")
        return str(res)

    def get_signature_status(self, signature: str) -> dict | None:
        res = self._call(
            "getSignatureStatuses", [[signature], {"searchTransactionHistory": True}]
        )
        values = (res or {}).get("value") or [None]
        return values[0]

    def confirm_signature(
        self, signature: str, timeout_seconds: float = 60.0, poll_seconds: float = 2.0
    ) -> bool:
        """Block until the signature is confirmed. False on timeout or failure."""
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                status = self.get_signature_status(signature)
            except RpcError:
                status = None
            if status:
                if status.get("err") is not None:
                    log.error("transaction %s… failed on chain", signature[:16])
                    return False
                confirmation = status.get("confirmationStatus")
                if confirmation in ("confirmed", "finalized"):
                    return True
            time.sleep(poll_seconds)
        return False
