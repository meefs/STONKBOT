"""Lightweight intent parser — no LLM, no network.

Ordering matters here and the previous version got it wrong: a plain
``help`` check ran before ``register``, and the word "how" matched ``_HELP_RE``,
so "how do I register" answered with help instead of registering. More
seriously, ``_WHO_RE`` matched the bare word "wallet", so "launch Wallet Inc
paired with GMEX" could be read as a balance query.

Launch is now matched first — it is the only intent that spends money, so a
command that clearly asks for a launch must never be reinterpreted as
something else, and an ambiguous one must never become a launch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

IntentKind = Literal[
    "register", "launch", "whoami", "help", "balance", "ref", "unknown"
]

# X handles: 1-15 chars, letters/digits/underscore.
_HANDLE = r"[A-Za-z0-9_]{1,15}"


@dataclass
class Intent:
    kind: IntentKind
    name: str | None = None
    symbol: str | None = None
    quote: str | None = None
    ref: str | None = None
    raw: str = ""


# A launch always names a quote via an explicit pairing word. Requiring that
# keyword is what stops a vague message from being treated as a launch.
_LAUNCH_RE = re.compile(
    r"\b(?:launch|deploy|create)\s+"
    r"(?:a\s+)?(?:new\s+)?(?:token\s+|coin\s+)?"
    r"(?:called\s+|named\s+)?"
    r"[\"']?(?P<name>[A-Za-z0-9][A-Za-z0-9 .\-&']{0,40}?)[\"']?"
    r"(?:\s+(?:with\s+)?(?:ticker|symbol)\s+[\"']?(?P<symbol>[A-Za-z0-9]{2,10})[\"']?)?"
    r"\s+(?:paired\s+with|pair\s+with|against|vs\.?|quote)\s+"
    r"[\"']?(?P<quote>[A-Za-z0-9]{2,20})[\"']?",
    re.I,
)

_REF_CMD_RE = re.compile(r"^\s*(?:ref|referral|invite)\s*$", re.I)
_REF_INLINE_RE = re.compile(rf"\b(?:ref|referral|invite)\s+@?({_HANDLE})\b", re.I)
_REGISTER_RE = re.compile(r"\b(?:register|sign\s?up|create\s+wallet|get\s+started)\b", re.I)
_BALANCE_RE = re.compile(r"\b(?:balance|funded|funds|how\s+much)\b", re.I)
_WHO_RE = re.compile(r"\b(?:whoami|my\s+wallet|my\s+address|my\s+account)\b", re.I)
_HELP_RE = re.compile(r"\b(?:help|commands|how\s+does\s+this|what\s+can\s+you)\b", re.I)

# Reserved words that are never a token name, so "launch help" isn't a launch.
_RESERVED_NAMES = {"help", "register", "balance", "ref", "referral", "whoami", "token", "coin"}


def _extract_ref(text: str) -> str | None:
    match = _REF_INLINE_RE.search(text or "")
    if not match:
        return None
    return match.group(1).lstrip("@").lower()


def _strip_ref(text: str) -> str:
    """Remove the ref clause so it cannot leak into the token name."""
    return _REF_INLINE_RE.sub(" ", text or "")


def parse(text: str) -> Intent:
    raw = (text or "").strip()
    if not raw:
        return Intent(kind="unknown", raw=raw)

    ref = _extract_ref(raw)
    body = _strip_ref(raw).strip()

    # 1. Launch first — the money-moving intent, and the most specific pattern.
    match = _LAUNCH_RE.search(body)
    if match:
        name = (match.group("name") or "").strip()
        quote = (match.group("quote") or "").strip().upper()
        if name and quote and name.lower() not in _RESERVED_NAMES:
            explicit_symbol = match.group("symbol")
            symbol = (
                explicit_symbol.upper()
                if explicit_symbol
                else (re.sub(r"[^A-Za-z0-9]", "", name)[:10].upper() or "STONK")
            )
            return Intent(
                kind="launch",
                name=name,
                symbol=symbol,
                quote=quote,
                ref=ref,
                raw=raw,
            )

    # 2. Exact-match commands.
    if _REF_CMD_RE.match(body):
        return Intent(kind="ref", ref=ref, raw=raw)

    # 3. Register before help, so "how do I register" registers.
    if _REGISTER_RE.search(body):
        return Intent(kind="register", ref=ref, raw=raw)
    if _WHO_RE.search(body):
        return Intent(kind="whoami", raw=raw)
    if _BALANCE_RE.search(body):
        return Intent(kind="balance", raw=raw)
    if _HELP_RE.search(body):
        return Intent(kind="help", raw=raw)

    # An incomplete launch ("launch GameStop") is unknown, not a launch — the
    # handler answers with the correct syntax rather than guessing a quote.
    return Intent(kind="unknown", raw=raw)
