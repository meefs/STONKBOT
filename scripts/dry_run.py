"""Pre-flight the local dry run, then hand off to the poll loop.

Checks, in the order that fails cheapest first:

1. every required key is present in .env
2. DRY_RUN is actually on
3. the X credentials authenticate, and as the *right* account
4. StonkFun is reachable

Only then does it start polling. The identity check matters most: the app is
owned by @PhantomCap_ai, so a token minted the wrong way authenticates fine and
posts from the wrong account. Better to stop here than to find out from a reply
that has already gone out.

    python scripts/dry_run.py                   # verify, then start the loop
    python scripts/dry_run.py --check           # verify and stop
    python scripts/dry_run.py --accept-backlog  # also work through a pile-up
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Three separate failures produced one lost log: the poll loop's log.info calls
# had no handler, stdout was block-buffered into a redirect, and the process was
# killed before anything flushed. A run that can post publicly must leave a
# record even when it dies, so everything also goes to a file that is flushed
# per line.
LOG_PATH = ROOT / "data" / "dry_run.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

_file = logging.FileHandler(LOG_PATH, encoding="utf-8")
_stream = logging.StreamHandler(sys.stderr)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[_file, _stream],
    force=True,
)
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except AttributeError:  # pragma: no cover - very old interpreters
    pass

EXPECTED_HANDLE = "stonkfunbot"

REQUIRED = (
    "AGENT_VAULT_KEY",
    "X_API_KEY",
    "X_API_SECRET",
    "X_ACCESS_TOKEN",
    "X_ACCESS_TOKEN_SECRET",
)


def _fail(message: str) -> None:
    print(f"\n  FAIL  {message}\n")
    sys.exit(1)


def main() -> None:
    check_only = "--check" in sys.argv

    # Set before the settings cache is populated, so the flag is read as if it
    # had been in the environment all along.
    if "--accept-backlog" in sys.argv:
        os.environ["STONKBOT_ACCEPT_BACKLOG"] = "true"

    from stonkbot.config import get_settings

    settings = get_settings()

    # 1. keys present
    missing = [key for key in REQUIRED if not getattr(settings, key.lower(), None)]
    if missing:
        _fail(
            "missing from .env: "
            + ", ".join(missing)
            + "\n        X_ACCESS_TOKEN/SECRET come from scripts/mint_x_token.py"
        )
    print("  ok    all credentials present")

    if len(settings.agent_vault_key) < 32:
        _fail("AGENT_VAULT_KEY is shorter than 32 characters")

    # 2. dry run genuinely on
    if not settings.dry_run:
        _fail("STONKBOT_DRY_RUN is not true — this would spend real SOL")
    print("  ok    dry run is ON — no SOL can move")

    # dry_run does not gate replies. Say plainly which of the two is happening,
    # because "dry run" reads like "nothing leaves this machine" and does not
    # mean that.
    if settings.observe_only:
        print("  ok    OBSERVE-ONLY — replies are logged, nothing is posted")
    else:
        print("  !!    replies WILL be posted publicly as @StonkFunBot")
        print("        set STONKBOT_OBSERVE_ONLY=true to watch without posting")

    # 3. X authenticates, as the right account
    try:
        from stonkbot.x_bot import _api, _resolve_bot

        api = _api()
        bot_id, handle = _resolve_bot(api)
    except Exception as e:
        _fail(f"X authentication failed: {type(e).__name__}: {e}")

    if handle.lower() != EXPECTED_HANDLE:
        _fail(
            f"authenticated as @{handle}, expected @{EXPECTED_HANDLE}.\n"
            f"        The token belongs to the wrong account — the bot would "
            f"post as @{handle}.\n"
            f"        Re-run scripts/mint_x_token.py signed in as "
            f"@{EXPECTED_HANDLE}."
        )
    print(f"  ok    authenticated as @{handle} (id {bot_id})")

    # 4. StonkFun reachable
    try:
        from stonkbot.stonkfun_client import StonkFunClient

        with StonkFunClient() as client:
            pairs = client.list_pairs(launchable=True)
        print(f"  ok    StonkFun reachable ({len(pairs)} launchable pairs)")
    except Exception as e:
        _fail(f"StonkFun unreachable: {e}")

    # 5. backlog posture
    if settings.accept_backlog:
        print("  !!    backlog guard DISABLED — a pile-up will be answered in full")
    else:
        print(
            f"  ok    backlog guard on "
            f"(stops above {settings.backlog_limit} waiting "
            f"or {settings.backlog_max_age_hours:.0f}h old)"
        )

    print(f"\nReady. Mention @{handle} with:  register  |  help  |  balance")

    if check_only:
        return

    print("Polling every 30s. Ctrl+C to stop.\n")
    from stonkbot.x_bot import run_poll_loop

    run_poll_loop()


if __name__ == "__main__":
    main()
