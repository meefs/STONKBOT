"""Short, low-token WSB-flavored replies. Keep everything brief."""

from __future__ import annotations

import random

SUCCESS = [
    "Token live.\n{symbol} paired with {quote}\n{url}\nApes ate. 🦍",
    "Deployed.\n${symbol} live on StonkFun.\n{url}\nTendies loading.",
    "It's live.\n${symbol} / {quote}\n{url}\nDiamond hands only.",
    "Launched.\n${symbol}\n{url}\nTo the moon? Maybe. Fees? Yours.",
]

DRY_RUN = [
    "Dry-run only. Nothing on chain.\nWould launch ${symbol} vs {quote}.\nFlip dry-run when ready.",
    "Simulated.\n${symbol} paired with {quote}.\nNo SOL spent. No token minted.",
]

NEED_WALLET = [
    "Link a Solana wallet first.\nReply with your address or use /link.",
    "No wallet linked to this handle.\nSend your Solana address to continue.",
]

ERROR = [
    "Launch failed.\n{detail}\nTry again or check status.",
    "Something broke.\n{detail}\nNot your fault (probably).",
]

FEE_NOTE = "Service fee: {fee} SOL → operator. You keep the 50% creator fees."


def success(symbol: str, quote: str, url: str) -> str:
    return random.choice(SUCCESS).format(symbol=symbol, quote=quote, url=url)


def dry_run(symbol: str, quote: str) -> str:
    return random.choice(DRY_RUN).format(symbol=symbol, quote=quote)


def need_wallet() -> str:
    return random.choice(NEED_WALLET)


def error(detail: str) -> str:
    return random.choice(ERROR).format(detail=detail[:120])


def fee_note(fee: float = 0.1) -> str:
    return FEE_NOTE.format(fee=fee)
