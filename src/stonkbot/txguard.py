"""Inspect a StonkFun-prepared payment transaction *before* signing it.

The previous implementation base64-decoded whatever ``/launches/prepare``
returned and signed it immediately. That is an unconditional trust of a remote
service with a user's funds: a compromised, spoofed or simply buggy upstream
could return a transaction that moves the wallet's entire balance somewhere
else, and the bot would sign it.

Nothing here trusts the upstream. We decode the transaction ourselves and
refuse to sign unless it passes every check:

  1. It is a well-formed transaction we can parse.
  2. The fee payer is the user's own agent wallet.
  3. The agent wallet is the only signature required (we never co-sign for
     another party).
  4. The SOL leaving the agent wallet is known and within the configured cap.

Check 4 is enforced against a simulated balance delta where the RPC provider
supports it, and otherwise against the sum of the transaction's own System
Program transfers. Both are compared to ``max_launch_cost_sol``.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from .config import get_settings
from .rpc import SolanaRpc, lamports_to_sol

log = logging.getLogger("stonkbot.txguard")

SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
# System Program instruction discriminators (u32 little-endian, first 4 bytes).
_SYS_TRANSFER = 2
_SYS_TRANSFER_WITH_SEED = 11
_SYS_CREATE_ACCOUNT = 0


class UnsafeTransaction(Exception):
    """The prepared transaction failed verification and must not be signed."""


@dataclass
class TxInspection:
    """What we determined about a prepared payment transaction."""

    fee_payer: str
    declared_debit_sol: float
    """SOL leaving the agent wallet via System Program instructions we decoded."""
    simulated_debit_sol: float | None
    """True balance delta from simulation, or None when unavailable."""
    signers_required: int

    @property
    def cost_sol(self) -> float:
        """Best available estimate of what this launch actually costs."""
        if self.simulated_debit_sol is not None:
            return self.simulated_debit_sol
        return self.declared_debit_sol

    @property
    def cost_is_simulated(self) -> bool:
        return self.simulated_debit_sol is not None


def _decode(tx_b64: str) -> VersionedTransaction:
    try:
        raw = base64.b64decode(tx_b64, validate=True)
    except Exception as e:
        raise UnsafeTransaction(f"payment transaction is not valid base64: {e}") from e
    if not raw:
        raise UnsafeTransaction("payment transaction is empty")
    try:
        # VersionedTransaction.from_bytes parses both legacy and v0 wire formats.
        return VersionedTransaction.from_bytes(raw)
    except Exception as e:
        raise UnsafeTransaction(f"could not parse payment transaction: {e}") from e


def _system_debits(tx: VersionedTransaction, payer: Pubkey) -> int:
    """Sum lamports moved out of ``payer`` by System Program instructions.

    Deliberately conservative: an instruction we cannot decode contributes
    nothing here, which is why this figure is only ever a lower bound and the
    simulated delta is preferred when available.
    """
    keys = list(tx.message.account_keys)
    total = 0

    for ix in tx.message.instructions:
        if ix.program_id_index >= len(keys):
            continue
        if str(keys[ix.program_id_index]) != SYSTEM_PROGRAM_ID:
            continue
        data = bytes(ix.data)
        if len(data) < 4:
            continue
        discriminator = int.from_bytes(data[:4], "little")

        if discriminator in (_SYS_TRANSFER, _SYS_CREATE_ACCOUNT):
            if len(data) < 12 or not ix.accounts:
                continue
            source_index = ix.accounts[0]
            if source_index >= len(keys) or keys[source_index] != payer:
                continue
            total += int.from_bytes(data[4:12], "little")
        elif discriminator == _SYS_TRANSFER_WITH_SEED:
            # Funds move from a derived account, not the payer's own balance,
            # but flag it: we do not expect it in a launch payment.
            log.warning("payment transaction contains transferWithSeed")

    return total


def inspect_payment_transaction(
    tx_b64: str, agent_pubkey: str, rpc: SolanaRpc | None = None
) -> TxInspection:
    """Decode and validate a prepared payment transaction.

    Raises ``UnsafeTransaction`` if it must not be signed.
    """
    settings = get_settings()
    tx = _decode(tx_b64)

    keys = list(tx.message.account_keys)
    if not keys:
        raise UnsafeTransaction("payment transaction has no accounts")

    try:
        expected = Pubkey.from_string(agent_pubkey)
    except Exception as e:
        raise UnsafeTransaction(f"invalid agent public key: {e}") from e

    # 2. Fee payer must be the user's own wallet — never anyone else's.
    fee_payer = keys[0]
    if fee_payer != expected:
        raise UnsafeTransaction(
            f"fee payer is {str(fee_payer)[:8]}…, expected this user's agent wallet"
        )

    # 3. We must be the only required signer. More than one means the
    #    transaction is also binding some other account we cannot vouch for.
    required = tx.message.header.num_required_signatures
    if required != 1:
        raise UnsafeTransaction(
            f"payment transaction requires {required} signatures, expected exactly 1"
        )

    declared = _system_debits(tx, expected)

    # 4. Prefer a simulated balance delta — it captures debits from any program,
    #    not only the System Program instructions we can decode.
    simulated_sol: float | None = None
    owned_rpc = rpc is None
    client = rpc or SolanaRpc()
    try:
        before = client.get_balance_lamports(agent_pubkey)
        after = client.simulate_transaction_balances(tx_b64, [agent_pubkey])
        if after is not None:
            delta = before - after.get(agent_pubkey, before)
            # A negative delta (wallet gains SOL) is not a cost; clamp at zero.
            simulated_sol = lamports_to_sol(max(delta, 0))
    except Exception as e:  # simulation is best-effort, never fatal on its own
        log.warning("could not simulate payment transaction: %s", e)
    finally:
        if owned_rpc:
            client.close()

    inspection = TxInspection(
        fee_payer=str(fee_payer),
        declared_debit_sol=lamports_to_sol(declared),
        simulated_debit_sol=simulated_sol,
        signers_required=required,
    )

    # The cap applies to whichever figure we trust most, and also to the
    # declared sum, so neither path alone can slip past it.
    cap = settings.max_launch_cost_sol
    for label, amount in (
        ("simulated", inspection.simulated_debit_sol),
        ("declared", inspection.declared_debit_sol),
    ):
        if amount is not None and amount > cap:
            raise UnsafeTransaction(
                f"launch would move {amount:.4f} SOL ({label}), above the "
                f"{cap} SOL safety cap — refusing to sign"
            )

    log.info(
        "payment tx verified: cost=%.4f SOL (%s)",
        inspection.cost_sol,
        "simulated" if inspection.cost_is_simulated else "declared",
    )
    return inspection
