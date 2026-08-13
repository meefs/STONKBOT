"""Data shapes."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# StonkFun's launch form: name max 32 chars, symbol max 10.
MAX_NAME_LENGTH = 32
MAX_SYMBOL_LENGTH = 10

LaunchMode = Literal["standard", "reward"]
LaunchStatus = Literal["dry_run", "pending", "processing", "completed", "failed"]


class QuotePair(BaseModel):
    mint: str
    symbol: str
    name: str | None = None
    category: str | None = None
    launchable: bool = True


class LaunchRequest(BaseModel):
    """A validated launch. Every field here is user-influenced, so each one is
    constrained before it can reach the StonkFun API."""

    name: str
    symbol: str
    quote_mint: str
    creator_wallet: str
    # 'reward' launches are admin-only on StonkFun's API and carry no creator
    # fee position, so the bot only ever submits 'standard'.
    mode: LaunchMode = "standard"
    logo_data_uri: str | None = None
    dev_buy_percent: float | None = None
    website: str | None = None
    twitter: str | None = None

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        # Strip control characters and collapse whitespace; these end up in
        # permanent on-chain metadata.
        cleaned = re.sub(r"[\x00-\x1f\x7f]", "", v or "").strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if not cleaned:
            raise ValueError("token name is empty")
        return cleaned[:MAX_NAME_LENGTH]

    @field_validator("symbol")
    @classmethod
    def _clean_symbol(cls, v: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9]", "", v or "").upper()
        if not cleaned:
            raise ValueError("token symbol is empty")
        return cleaned[:MAX_SYMBOL_LENGTH]

    @field_validator("dev_buy_percent")
    @classmethod
    def _clamp_dev_buy(cls, v: float | None) -> float | None:
        if v is None:
            return None
        # StonkFun caps the dev buy at 2.5% of supply.
        return max(0.0, min(float(v), 2.5))


class LaunchCost(BaseModel):
    """What a specific launch actually costs, quoted per launch.

    StonkFun does not publish a fixed launch price — the cost rides inside the
    payment transaction from /launches/prepare — so this is measured, not
    assumed.
    """

    stonkfun_cost_sol: float
    """SOL debited by StonkFun's payment transaction (launch fee + rent + any
    dev buy)."""
    service_fee_sol: float
    """STONKBOT's own fee, charged separately."""
    network_reserve_sol: float
    measured: bool = False
    """True when derived from an on-chain simulation rather than decoded
    instructions alone."""

    @property
    def total_sol(self) -> float:
        return round(
            self.stonkfun_cost_sol + self.service_fee_sol + self.network_reserve_sol, 6
        )


class LaunchResult(BaseModel):
    status: LaunchStatus
    mint: str | None = None
    payment_signature: str | None = None
    stonkfun_url: str | None = None
    service_fee_sol: float = 0.0
    service_fee_paid: bool = False
    service_fee_signature: str | None = None
    cost: LaunchCost | None = None
    message: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class AgentAccount(BaseModel):
    x_handle: str
    pubkey: str
    created_at: datetime
    active: bool = True
