"""Cron-invoked mention poll.

Vercel runs this on the schedule in ``vercel.json``. Each invocation is one
`poll_once` cycle: fetch new mentions, handle them, advance the durable cursor.
Nothing is kept in memory between invocations — the cursor, the seen set, the
wallet vault and the idempotency ledger all live in Postgres.

Auth: Vercel sends ``Authorization: Bearer $CRON_SECRET`` on scheduled
invocations. Without a matching secret this endpoint is a public button that
makes the bot post, so a missing or wrong secret is a hard 401.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

# The bot package lives in src/, which is not on the path in the function
# sandbox the way it is after an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("stonkbot.cron")


def _authorized(header: str | None) -> bool:
    secret = os.environ.get("CRON_SECRET") or os.environ.get("STONKBOT_CRON_SECRET")
    if not secret:
        # Fail closed. An unset secret must not mean "let everyone in".
        log.error("CRON_SECRET is not set — refusing to run")
        return False
    expected = f"Bearer {secret}"
    # Constant-time: this compares a caller-supplied string against a secret.
    return bool(header) and hmac.compare_digest(header, expected)


class handler(BaseHTTPRequestHandler):  # noqa: N801 - Vercel requires this name
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not _authorized(self.headers.get("Authorization")):
            self._respond(401, {"ok": False, "error": "unauthorized"})
            return

        try:
            from stonkbot.x_bot import poll_once_standalone

            summary = poll_once_standalone()
        except Exception as e:
            # 500 so a failed cycle is visible in Vercel's cron history rather
            # than silently reported as a success.
            log.exception("poll cycle failed")
            self._respond(500, {"ok": False, "error": type(e).__name__})
            return

        log.info("poll ok %s", summary)
        self._respond(200, {"ok": True, **summary})

    def _respond(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
