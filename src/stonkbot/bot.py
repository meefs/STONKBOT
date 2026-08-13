"""Entrypoint: python -m stonkbot.bot"""

from __future__ import annotations

import logging
import sys

from .config import get_settings
from .x_bot import run_poll_loop


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("stonkbot")
    settings = get_settings()

    # Fail fast rather than discovering a missing vault key mid-launch.
    if not settings.agent_vault_key:
        log.error("AGENT_VAULT_KEY is not set — refusing to start")
        sys.exit(1)

    if settings.dry_run:
        log.info("DRY_RUN is ON — no funds will move")
    else:
        log.warning("DRY_RUN is OFF — launches will spend real SOL")

    log.info(
        "STONKBOT start surface=x_only service_fee=%s SOL",
        settings.service_fee_sol,
    )
    run_poll_loop()


if __name__ == "__main__":
    main()
