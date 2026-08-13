"""Lightweight intent parser — no LLM, low cost."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

IntentKind = Literal["link", "launch", "whoami", "help", "unknown"]


@dataclass
class Intent:
    kind: IntentKind
    wallet: str | None = None
    name: str | None = None
    symbol: str | None = None
    quote: str | None = None
    raw: str = ""


_WALLET_RE = re.compile(r"\b([1-9A-HJ-NP-Za-km-z]{32,44})\b")
_LINK_RE = re.compile(r"(?:^|\s)(?:link|connect)\s+", re.I)
_LAUNCH_RE = re.compile(
    r"(?:launch|deploy|create)\s+(?:a\s+)?(?:token\s+)?(?:called\s+|named\s+)?[\"']?([^\"'\n,]+?)[\"']?"
    r"(?:\s+with\s+(?:ticker|symbol)\s+[\"']?([A-Za-z0-9]{2,12})[\"']?)?"
    r"(?:\s+(?:paired\s+with|vs|against|quote)\s+[\"']?([A-Za-z0-9x]{2,16})[\"']?)?",
    re.I,
)
_SIMPLE_LAUNCH = re.compile(
    r"(?:launch|deploy)\s+([A-Za-z0-9][A-Za-z0-9 \-]{1,40}?)\s+(?:paired\s+with|vs|against)\s+([A-Za-z0-9x]{2,16})",
    re.I,
)
_WHO_RE = re.compile(r"\b(whoami|my wallet|linked|status)\b", re.I)
_HELP_RE = re.compile(r"\b(help|how|commands)\b", re.I)


def parse(text: str) -> Intent:
    t = (text or "").strip()
    if not t:
        return Intent(kind="unknown", raw=t)

    if _HELP_RE.search(t) and not _LAUNCH_RE.search(t):
        return Intent(kind="help", raw=t)

    if _WHO_RE.search(t):
        return Intent(kind="whoami", raw=t)

    if _LINK_RE.search(t) or (t.lower().startswith("link") or "connect" in t.lower()[:12]):
        m = _WALLET_RE.search(t)
        return Intent(kind="link", wallet=m.group(1) if m else None, raw=t)

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

    # fallback: "GameStop AMC" style
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
