"""Balance reads and service-fee transfers from an agent wallet."""

from __future__ import annotations

import logging

from .config import get_settings
from .rpc import RpcError, SolanaRpc, sol_to_lamports

log = logging.getLogger("stonkbot.pay")

# Leave enough for the signature fee so a transfer can never zero the account
# below rent-exemption in a way that strands it.
_MIN_REMAINDER_LAMPORTS = 10_000


class PaymentError(Exception):
    """A transfer could not be built, sent or confirmed."""


def get_balance_sol(pubkey: str) -> float:
    with SolanaRpc() as rpc:
        return rpc.get_balance_sol(pubkey)


def transfer_sol(
    from_handle: str,
    to_pubkey: str,
    amount_sol: float,
    *,
    confirm: bool = True,
) -> str | None:
    """Send SOL from a user's agent wallet.

    Returns the confirmed signature, or ``None`` in dry-run. Raises
    :class:`PaymentError` if the transfer could not be completed — callers must
    not treat a failure as success.
    """
    settings = get_settings()

    if settings.dry_run:
        log.info(
            "dry_run: would transfer %.4f SOL → %s…", amount_sol, to_pubkey[:8]
        )
        return None

    if amount_sol <= 0:
        return None

    from solders.hash import Hash
    from solders.message import Message
    from solders.pubkey import Pubkey
    from solders.system_program import TransferParams, transfer
    from solders.transaction import Transaction

    from .vault import load_keypair

    try:
        destination = Pubkey.from_string(to_pubkey)
    except Exception as e:
        raise PaymentError(f"invalid destination address: {e}") from e

    kp = load_keypair(from_handle)
    lamports = sol_to_lamports(amount_sol)

    with SolanaRpc() as rpc:
        try:
            balance = rpc.get_balance_lamports(str(kp.pubkey()))
            if balance < lamports + _MIN_REMAINDER_LAMPORTS:
                raise PaymentError(
                    f"insufficient balance for {amount_sol} SOL transfer"
                )

            blockhash_str, _ = rpc.get_latest_blockhash()
            blockhash = Hash.from_string(blockhash_str)

            ix = transfer(
                TransferParams(
                    from_pubkey=kp.pubkey(),
                    to_pubkey=destination,
                    lamports=lamports,
                )
            )
            message = Message.new_with_blockhash([ix], kp.pubkey(), blockhash)
            tx = Transaction([kp], message, blockhash)

            import base64

            signature = rpc.send_raw_transaction(base64.b64encode(bytes(tx)).decode())
        except RpcError as e:
            raise PaymentError(f"rpc error: {e}") from e

        if confirm and not rpc.confirm_signature(signature):
            raise PaymentError(f"transfer {signature[:16]}… was not confirmed")

    log.info("transfer confirmed sig=%s…", signature[:16])
    return signature
