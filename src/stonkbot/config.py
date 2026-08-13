"""Settings — secrets only from env, never logged."""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    dry_run: bool = True
    require_approval: bool = True
    service_fee_sol: float = 0.1
    fee_recipient: str = "GKCJKSDJMfq4Zm4ye16oQFHRxqVqParBPvA5ja3FPBzS"

    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    stonkfun_api_base: str = "https://www.stonkfun.xyz/api/public/v1"

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    daily_launch_budget: int = 10
    rate_limit_per_min: int = 5

    def redacted(self) -> dict:
        """Safe view for logs / status."""
        return {
            "dry_run": self.dry_run,
            "require_approval": self.require_approval,
            "service_fee_sol": self.service_fee_sol,
            "fee_recipient": self.fee_recipient[:8] + "…",
            "daily_launch_budget": self.daily_launch_budget,
            "telegram_configured": bool(self.telegram_bot_token and self.telegram_chat_id),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
