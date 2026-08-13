"""Short WSB replies."""

from __future__ import annotations

import random

SUCCESS = [
    "Token live.\n{symbol} paired with {quote}\n{url}\nApes ate. 🦍",
    "Deployed.\n${symbol} live on StonkFun.\n{url}\nTendies loading.",
    "It's live.\n${symbol} / {quote}\n{url}\nYou keep the creator fees.",
]

DRY_RUN = [
    "Dry-run only.\nWould launch ${symbol} vs {quote}.\nNothing on chain.",
    "Simulated.\n${symbol} paired with {quote}.\nFlip dry-run when ready.",
]

ERROR = [
    "Failed.\n{detail}",
    "Nope.\n{detail}",
]


def success(symbol: str, quote: str, url: str) -> str:
    return random.choice(SUCCESS).format(symbol=symbol, quote=quote, url=url)


def dry_run(symbol: str, quote: str) -> str:
    return random.choice(DRY_RUN).format(symbol=symbol, quote=quote)


def error(detail: str) -> str:
    return random.choice(ERROR).format(detail=str(detail)[:140])


def fee_note(fee: float = 0.1) -> str:
    return f"Service fee: {fee} SOL. You keep 50% creator fees."
