"""Settings — secrets only from env, never logged.

Every field accepts both a ``STONKBOT_``-prefixed and a bare environment
variable name. The prefixed form is what ``.env.example`` documents; the bare
form is kept so existing deployments keep working.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Base58 alphabet — Solana addresses never contain 0, O, I or l.
_B58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


def _both(name: str) -> AliasChoices:
    """Accept STONKBOT_FOO and FOO for field ``foo``."""
    return AliasChoices(f"STONKBOT_{name.upper()}", name.upper())


def is_valid_pubkey(value: str) -> bool:
    """Cheap structural check for a Solana address (32-byte base58)."""
    if not value or not (32 <= len(value) <= 44):
        return False
    return all(c in _B58 for c in value)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True
    )

    # --- Safety -------------------------------------------------------------
    # Default true: a misconfigured deploy must never move real funds.
    dry_run: bool = Field(default=True, validation_alias=_both("dry_run"))

    # --- STONKBOT's own service fee (not a StonkFun fee) --------------------
    service_fee_sol: float = Field(default=0.1, validation_alias=_both("service_fee_sol"))
    fee_recipient: str = Field(
        default="GKCJKSDJMfq4Zm4ye16oQFHRxqVqParBPvA5ja3FPBzS",
        validation_alias=_both("fee_recipient"),
    )
    # Share of service_fee_sol rebated to a referrer (rest → fee_recipient).
    referral_share: float = Field(default=0.30, validation_alias=_both("referral_share"))

    # --- Vault --------------------------------------------------------------
    # Master key encrypting per-user agent wallet secrets at rest.
    agent_vault_key: str | None = Field(
        default=None, validation_alias=_both("agent_vault_key")
    )

    # --- Network ------------------------------------------------------------
    solana_rpc_url: str = Field(
        default="https://api.mainnet-beta.solana.com",
        validation_alias=_both("solana_rpc_url"),
    )
    stonkfun_api_base: str = Field(
        default="https://www.stonkfun.xyz/api/public/v1",
        validation_alias=_both("stonkfun_api_base"),
    )
    rpc_timeout_seconds: float = Field(
        default=20.0, validation_alias=_both("rpc_timeout_seconds")
    )

    # --- Launch cost handling ----------------------------------------------
    # StonkFun does not publish a fixed launch price; the real cost rides inside
    # the payment transaction returned by /launches/prepare. We therefore quote
    # the live cost per launch rather than hardcoding it. These two values are
    # only guard rails around that quote.
    #
    # Hard ceiling on what a single prepared transaction may debit from a user's
    # agent wallet. A quote above this is refused unsigned — this is the control
    # that stops a compromised or misbehaving upstream API from draining wallets.
    max_launch_cost_sol: float = Field(
        default=1.0, validation_alias=_both("max_launch_cost_sol")
    )
    # Headroom kept for Solana network fees and rent on top of the quoted cost.
    network_fee_reserve_sol: float = Field(
        default=0.02, validation_alias=_both("network_fee_reserve_sol")
    )
    # Advertised funding target. Used for guidance copy only — never as the
    # authority on whether a specific launch can proceed.
    recommended_funding_sol: float = Field(
        default=0.35, validation_alias=_both("recommended_funding_sol")
    )

    # --- X ------------------------------------------------------------------
    x_api_key: str | None = Field(default=None, validation_alias=_both("x_api_key"))
    x_api_secret: str | None = Field(default=None, validation_alias=_both("x_api_secret"))
    x_access_token: str | None = Field(
        default=None, validation_alias=_both("x_access_token")
    )
    x_access_token_secret: str | None = Field(
        default=None, validation_alias=_both("x_access_token_secret")
    )
    x_bearer_token: str | None = Field(
        default=None, validation_alias=_both("x_bearer_token")
    )
    x_bot_username: str | None = Field(
        default=None, validation_alias=_both("x_bot_username")
    )

    # --- Rails --------------------------------------------------------------
    daily_launch_budget: int = Field(
        default=10, validation_alias=_both("daily_launch_budget")
    )
    rate_limit_per_min: int = Field(
        default=5, validation_alias=_both("rate_limit_per_min")
    )
    user_launches_per_hour: int = Field(
        default=3, validation_alias=_both("user_launches_per_hour")
    )
    data_dir: str = Field(default="data", validation_alias=_both("data_dir"))

    @field_validator("fee_recipient")
    @classmethod
    def _check_recipient(cls, v: str) -> str:
        if not is_valid_pubkey(v):
            raise ValueError("fee_recipient is not a valid Solana address")
        return v

    @field_validator("referral_share")
    @classmethod
    def _check_share(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("referral_share must be between 0 and 1")
        return v

    @field_validator("service_fee_sol", "max_launch_cost_sol")
    @classmethod
    def _check_positive(cls, v: float) -> float:
        if v < 0:
            raise ValueError("fee amounts must not be negative")
        return v

    def redacted(self) -> dict:
        """Safe-to-print snapshot. Never includes a secret value."""
        return {
            "dry_run": self.dry_run,
            "service_fee_sol": self.service_fee_sol,
            "referral_share": self.referral_share,
            "fee_recipient": self.fee_recipient[:6] + "…" + self.fee_recipient[-4:],
            "max_launch_cost_sol": self.max_launch_cost_sol,
            "network_fee_reserve_sol": self.network_fee_reserve_sol,
            "recommended_funding_sol": self.recommended_funding_sol,
            "daily_launch_budget": self.daily_launch_budget,
            "rate_limit_per_min": self.rate_limit_per_min,
            "user_launches_per_hour": self.user_launches_per_hour,
            "solana_rpc_host": self.solana_rpc_url.split("/")[2]
            if "//" in self.solana_rpc_url
            else "?",
            "stonkfun_api_base": self.stonkfun_api_base,
            "vault_configured": bool(self.agent_vault_key),
            "x_configured": bool(
                self.x_api_key
                and self.x_api_secret
                and self.x_access_token
                and self.x_access_token_secret
            ),
            "surface": "x_only",
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
