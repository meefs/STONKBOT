"""Entrypoint: python -m stonkbot.bot"""

from __future__ import annotations

import logging

from .config import get_settings
from .x_bot import run_poll_loop


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    s = get_settings()
    logging.info("STONKBOT start surface=x_only dry_run=%s fee=%s SOL", s.dry_run, s.service_fee_sol)
    run_poll_loop()


if __name__ == "__main__":
    main()
