"""Transaction guard — the control that stops a wallet being drained.

These build real Solana transactions and assert the guard's verdict, including
the attack it exists to stop: an upstream returning a transaction that pays
someone else.
"""

from __future__ import annotations

import base64

import pytest
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import Message
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

from stonkbot.txguard import UnsafeTransaction, inspect_payment_transaction


class _NoSimRpc:
    """RPC stub that cannot simulate, forcing the decoded-instruction path.

    This is the pessimistic case: the guard must still be safe without
    simulation.
    """

    def get_balance_lamports(self, pubkey, commitment="confirmed"):
        return 1_000_000_000

    def simulate_transaction_balances(self, tx_b64, accounts):
        return None

    def close(self):
        pass


def _build(payer: Keypair, transfers, signers=None) -> str:
    """Serialize an *unsigned* payment transaction, as /prepare returns one."""
    blockhash = Hash.default()
    instructions = [
        transfer(
            TransferParams(
                from_pubkey=source, to_pubkey=destination, lamports=lamports
            )
        )
        for source, destination, lamports in transfers
    ]
    message = Message.new_with_blockhash(instructions, payer.pubkey(), blockhash)
    tx = Transaction.new_unsigned(message)
    return base64.b64encode(bytes(tx)).decode()


def test_accepts_a_normal_launch_payment():
    agent = Keypair()
    platform = Keypair().pubkey()
    tx = _build(agent, [(agent.pubkey(), platform, 150_000_000)])  # 0.15 SOL

    inspection = inspect_payment_transaction(tx, str(agent.pubkey()), rpc=_NoSimRpc())

    assert inspection.fee_payer == str(agent.pubkey())
    assert inspection.declared_debit_sol == pytest.approx(0.15)
    assert inspection.signers_required == 1


def test_rejects_transaction_paid_by_someone_else():
    """The fee payer must be this user's wallet, never another account."""
    agent = Keypair()
    attacker = Keypair()
    tx = _build(attacker, [(attacker.pubkey(), Keypair().pubkey(), 1_000)])

    with pytest.raises(UnsafeTransaction, match="fee payer"):
        inspect_payment_transaction(tx, str(agent.pubkey()), rpc=_NoSimRpc())


def test_rejects_transaction_over_the_safety_cap(monkeypatch):
    """A quote above max_launch_cost_sol is refused unsigned.

    This is the wallet-drain case: upstream asks for 50 SOL instead of ~0.2.
    """
    from stonkbot.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("STONKBOT_MAX_LAUNCH_COST_SOL", "1.0")

    agent = Keypair()
    tx = _build(agent, [(agent.pubkey(), Keypair().pubkey(), 50_000_000_000)])  # 50 SOL

    with pytest.raises(UnsafeTransaction, match="safety cap"):
        inspect_payment_transaction(tx, str(agent.pubkey()), rpc=_NoSimRpc())

    get_settings.cache_clear()


def test_sums_multiple_debits_against_the_cap(monkeypatch):
    """Splitting a drain across several transfers must not evade the cap."""
    from stonkbot.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("STONKBOT_MAX_LAUNCH_COST_SOL", "1.0")

    agent = Keypair()
    tx = _build(
        agent,
        [
            (agent.pubkey(), Keypair().pubkey(), 400_000_000),
            (agent.pubkey(), Keypair().pubkey(), 400_000_000),
            (agent.pubkey(), Keypair().pubkey(), 400_000_000),
        ],
    )  # 1.2 SOL total, each leg individually under the cap

    with pytest.raises(UnsafeTransaction, match="safety cap"):
        inspect_payment_transaction(tx, str(agent.pubkey()), rpc=_NoSimRpc())

    get_settings.cache_clear()


def test_rejects_malformed_input():
    agent = str(Keypair().pubkey())
    for payload in ["", "not base64!!", base64.b64encode(b"garbage").decode()]:
        with pytest.raises(UnsafeTransaction):
            inspect_payment_transaction(payload, agent, rpc=_NoSimRpc())


def test_simulated_delta_is_preferred_over_declared():
    """When simulation works, its balance delta is the cost we trust.

    A transaction can debit a wallet through programs whose instructions we do
    not decode, so the simulated delta is the more complete figure.
    """
    agent = Keypair()
    tx = _build(agent, [(agent.pubkey(), Keypair().pubkey(), 10_000_000)])  # 0.01 SOL

    class SimRpc(_NoSimRpc):
        def simulate_transaction_balances(self, tx_b64, accounts):
            # Wallet really loses 0.25 SOL, far more than the 0.01 declared.
            return {accounts[0]: 1_000_000_000 - 250_000_000}

    inspection = inspect_payment_transaction(tx, str(agent.pubkey()), rpc=SimRpc())

    assert inspection.cost_is_simulated
    assert inspection.cost_sol == pytest.approx(0.25)
    assert inspection.declared_debit_sol == pytest.approx(0.01)


def test_simulated_drain_is_caught_even_when_declared_is_small(monkeypatch):
    """The attack the declared-only path would miss."""
    from stonkbot.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("STONKBOT_MAX_LAUNCH_COST_SOL", "1.0")

    agent = Keypair()
    tx = _build(agent, [(agent.pubkey(), Keypair().pubkey(), 1_000)])

    class DrainRpc(_NoSimRpc):
        def get_balance_lamports(self, pubkey, commitment="confirmed"):
            return 20_000_000_000  # 20 SOL

        def simulate_transaction_balances(self, tx_b64, accounts):
            return {accounts[0]: 0}  # everything gone

    with pytest.raises(UnsafeTransaction, match="safety cap"):
        inspect_payment_transaction(tx, str(agent.pubkey()), rpc=DrainRpc())

    get_settings.cache_clear()
