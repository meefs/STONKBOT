"""Balance checks + service fee transfer from agent wallet → operator."""

from __future__ import annotations

import logging

from .config import get_settings

log = logging.getLogger("stonkbot.pay")

LAMPORTS = 1_000_000_000


def get_balance_sol(pubkey: str) -> float:
    from solana.rpc.api import Client
    from solders.pubkey import Pubkey

    s = get_settings()
    client = Client(s.solana_rpc_url)
    resp = client.get_balance(Pubkey.from_string(pubkey))
    lamports = resp.value or 0
    return lamports / LAMPORTS


def transfer_sol(from_handle: str, to_pubkey: str, amount_sol: float) -> str | None:
    """Send SOL from agent wallet. Returns signature or None on dry-run/fail."""
    s = get_settings()
    if s.dry_run:
        log.info("dry_run skip transfer %.4f SOL → %s…", amount_sol, to_pubkey[:8])
        return None

    from solana.rpc.api import Client
    from solana.rpc.types import TxOpts
    from solders.pubkey import Pubkey
    from solders.system_program import TransferParams, transfer
    from solders.transaction import Transaction
    from solders.message import Message

    from .vault import load_keypair

    kp = load_keypair(from_handle)
    client = Client(s.solana_rpc_url)
    to = Pubkey.from_string(to_pubkey)
    lamports = int(amount_sol * LAMPORTS)

    ix = transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=to, lamports=lamports))
    blockhash = client.get_latest_blockhash().value.blockhash
    msg = Message.new_with_blockhash([ix], kp.pubkey(), blockhash)
    tx = Transaction.new_unsigned(msg)
    tx.sign([kp], blockhash)
    result = client.send_transaction(tx, opts=TxOpts(skip_preflight=False))
    sig = str(result.value)
    log.info("service fee sent sig=%s", sig[:16])
    return sig
