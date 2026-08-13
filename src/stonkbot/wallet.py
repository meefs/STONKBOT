"""Hot wallet for automated StonkFun launches.

Secret stays in env only. Never log the key.
"""

from __future__ import annotations

import base64
import json
import os
from functools import lru_cache


class WalletError(Exception):
    pass


@lru_cache
def load_hot_keypair():
    """Load bot hot wallet from STONKBOT_HOT_WALLET_SECRET.

    Accepts:
    - JSON array of bytes (solana-keygen export)
    - base58 secret key string
    """
    raw = os.environ.get("STONKBOT_HOT_WALLET_SECRET", "").strip()
    if not raw:
        raise WalletError("STONKBOT_HOT_WALLET_SECRET not set")

    try:
        from solders.keypair import Keypair
    except ImportError as e:
        raise WalletError("solders not installed — pip install solders") from e

    # JSON byte array
    if raw.startswith("["):
        data = bytes(json.loads(raw))
        return Keypair.from_bytes(data)

    # base58
    try:
        from solders.keypair import Keypair as KP

        return KP.from_base58_string(raw)
    except Exception:
        pass

    # base64 fallback
    try:
        data = base64.b64decode(raw)
        return Keypair.from_bytes(data)
    except Exception as e:
        raise WalletError("invalid STONKBOT_HOT_WALLET_SECRET format") from e


def pubkey_str() -> str:
    return str(load_hot_keypair().pubkey())


def sign_transaction_b64(unsigned_b64: str) -> str:
    """Sign a base64-encoded VersionedTransaction or legacy Transaction."""
    from solders.transaction import VersionedTransaction
    from solders.message import to_bytes_versioned

    kp = load_hot_keypair()
    raw = base64.b64decode(unsigned_b64)

    try:
        tx = VersionedTransaction.from_bytes(raw)
        # re-sign
        msg_bytes = to_bytes_versioned(tx.message)
        sig = kp.sign_message(msg_bytes)
        signed = VersionedTransaction.populate(tx.message, [sig])
        return base64.b64encode(bytes(signed)).decode()
    except Exception:
        # legacy Transaction path
        from solana.transaction import Transaction

        tx = Transaction.deserialize(raw)
        tx.sign(kp)
        return base64.b64encode(tx.serialize()).decode()
