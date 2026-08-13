"""Lightweight intent parser — no LLM."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

IntentKind = Literal["register", "launch", "whoami", "help", "balance", "unknown"]


@dataclass
class Intent:
    kind: IntentKind
    name: str | None = None
    symbol: str | None = None
    quote: str | None = None
    raw: str = ""


_REGISTER_RE = re.compile(r"\b(register|signup|sign up|create wallet|start)\b", re.I)
_BALANCE_RE = re.compile(r"\b(balance|funded|funds)\b", re.I)
_WHO_RE = re.compile(r"\b(whoami|my wallet|wallet|account)\b", re.I)
_HELP_RE = re.compile(r"\b(help|how|commands)\b", re.I)
_SIMPLE_LAUNCH = re.compile(
    r"(?:launch|deploy)\s+([A-Za-z0-9][A-Za-z0-9 \-]{1,40}?)\s+(?:paired\s+with|vs|against)\s+([A-Za-z0-9x]{2,16})",
    re.I,
)
_LAUNCH_RE = re.compile(
    r"(?:launch|deploy|create)\s+(?:a\s+)?(?:token\s+)?(?:called\s+|named\s+)?[\"']?([^\"'\n,]+?)[\"']?"
    r"(?:\s+with\s+(?:ticker|symbol)\s+[\"']?([A-Za-z0-9]{2,12})[\"']?)?"
    r"(?:\s+(?:paired\s+with|vs|against|quote)\s+[\"']?([A-Za-z0-9x]{2,16})[\"']?)?",
    re.I,
)


def parse(text: str) -> Intent:
    t = (text or "").strip()
    if not t:
        return Intent(kind="unknown", raw=t)

    if _HELP_RE.search(t) and not _LAUNCH_RE.search(t):
        return Intent(kind="help", raw=t)
    if _REGISTER_RE.search(t):
        return Intent(kind="register", raw=t)
    if _BALANCE_RE.search(t):
        return Intent(kind="balance", raw=t)
    if _WHO_RE.search(t):
        return Intent(kind="whoami", raw=t)

    m2 = _SIMPLE_LAUNCH.search(t)
    if m2:
        name = m2.group(1).strip()
        quote = m2.group(2).strip().upper()
        symbol = re.sub(r"[^A-Za-z0-9]", "", name)[:10].upper() or "STONK"
        return Intent(kind="launch", name=name, symbol=symbol, quote=quote, raw=t)

    m = _LAUNCH_RE.search(t)
    if m:
        name = (m.group(1) or "").strip()
        symbol = (m.group(2) or re.sub(r"[^A-Za-z0-9]", "", name)[:10] or "STONK").upper()
        quote = (m.group(3) or "").upper() or None
        if name:
            return Intent(kind="launch", name=name, symbol=symbol, quote=quote, raw=t)

    parts = t.split()
    if len(parts) >= 2 and parts[0].lower() in ("launch", "deploy"):
        return Intent(
            kind="launch",
            name=" ".join(parts[1:-1]) or parts[1],
            symbol=re.sub(r"[^A-Za-z0-9]", "", parts[1])[:10].upper(),
            quote=parts[-1].upper() if len(parts) > 2 else None,
            raw=t,
        )

    return Intent(kind="unknown", raw=t)
