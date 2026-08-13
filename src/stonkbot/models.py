"""Data shapes."""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class QuotePair(BaseModel):
    mint: str
    symbol: str
    name: str | None = None
    category: str | None = None
    launchable: bool = True


class LaunchRequest(BaseModel):
    name: str
    symbol: str
    quote_mint: str
    creator_wallet: str
    mode: Literal["standard", "reward"] = "standard"
    logo_data_uri: str | None = None
    dev_buy_percent: float | None = None  # max 2.5
    website: str | None = None
    twitter: str | None = None


class LaunchResult(BaseModel):
    status: Literal["dry_run", "pending", "processing", "completed", "failed"]
    mint: str | None = None
    payment_signature: str | None = None
    stonkfun_url: str | None = None
    service_fee_sol: float = 0.1
    service_fee_paid: bool = False
    message: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class LinkedAccount(BaseModel):
    x_handle: str
    solana_wallet: str
    linked_at: datetime
    active: bool = True
