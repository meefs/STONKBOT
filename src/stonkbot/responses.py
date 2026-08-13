"""Short replies for X.

House style: market-native, a bit irreverent, never a promise. Nothing here
claims a user will earn anything — only what the fee mechanism does.
"""

from __future__ import annotations

import random

# X counts a reply's visible text; keep well under the limit so an appended
# mention or link can never truncate the useful part.
MAX_REPLY_LENGTH = 260

SUCCESS = [
    "Live.\n${symbol} / {quote}\n{url}\nCreator fees route to your wallet.",
    "Deployed.\n${symbol} paired with {quote}\n{url}\nYou're the creator.",
    "It's up.\n${symbol} / {quote}\n{url}\nClaim creator fees on the token page.",
]

DRY_RUN = [
    "Dry-run.\nWould launch ${symbol} vs {quote}.\nNothing on chain, nothing charged.",
    "Simulated.\n${symbol} paired with {quote}.\nNo funds moved.",
]

ERROR = [
    "Failed.\n{detail}",
    "Nope.\n{detail}",
]


def _fit(text: str) -> str:
    if len(text) <= MAX_REPLY_LENGTH:
        return text
    return text[: MAX_REPLY_LENGTH - 1].rstrip() + "…"


def success(symbol: str, quote: str, url: str) -> str:
    return _fit(random.choice(SUCCESS).format(symbol=symbol, quote=quote, url=url))


def dry_run(symbol: str, quote: str) -> str:
    return _fit(random.choice(DRY_RUN).format(symbol=symbol, quote=quote))


def error(detail: str) -> str:
    # Truncate the detail itself so an upstream message cannot crowd out the
    # rest of the reply, and strip newlines that would fragment it.
    clean = " ".join(str(detail).split())[:160]
    return _fit(random.choice(ERROR).format(detail=clean))


def fee_note(fee: float) -> str:
    return f"STONKBOT fee: {fee} SOL per launch. StonkFun splits trading fees 50/50 with creators."
