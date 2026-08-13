"""Settings — secrets only from env, never logged."""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    dry_run: bool = True
    service_fee_sol: float = 0.1
    fee_recipient: str = "GKCJKSDJMfq4Zm4ye16oQFHRxqVqParBPvA5ja3FPBzS"

    # Master key to encrypt per-user agent wallet secrets at rest (32+ char random string)
    agent_vault_key: str | None = None

    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    stonkfun_api_base: str = "https://www.stonkfun.xyz/api/public/v1"

    # Min SOL required in agent wallet before launch (launch fee ~0.2 + service 0.1 + buffer)
    min_launch_balance_sol: float = 0.35

    x_api_key: str | None = None
    x_api_secret: str | None = None
    x_access_token: str | None = None
    x_access_token_secret: str | None = None
    x_bearer_token: str | None = None
    x_bot_username: str | None = None

    daily_launch_budget: int = 10
    rate_limit_per_min: int = 5

    def redacted(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "service_fee_sol": self.service_fee_sol,
            "fee_recipient": self.fee_recipient[:8] + "…",
            "min_launch_balance_sol": self.min_launch_balance_sol,
            "daily_launch_budget": self.daily_launch_budget,
            "vault_configured": bool(self.agent_vault_key),
            "x_configured": bool(self.x_bearer_token or (self.x_api_key and self.x_access_token)),
            "surface": "x_only",
            "model": "bankr_agent_wallets",
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
