"""X-only surface: fully automated launches via bot hot wallet."""

from __future__ import annotations

import logging
import time
from typing import Any

from .accounts import get as get_account, link
from .config import get_settings
from .intent import parse
from .launch import run_launch
from .models import LaunchRequest
from .responses import need_wallet, dry_run, success, error, fee_note
from .security import guard
from .stonkfun_client import StonkFunClient

log = logging.getLogger("stonkbot.x")

HELP_TEXT = (
    "Commands:\n"
    "link <SOL_ADDRESS>  (optional identity)\n"
    "launch <name> paired with <QUOTE>\n"
    "whoami\n"
    "Fully automated. 0.1 SOL service fee."
)


def _client_tweepy():
    import tweepy

    s = get_settings()
    if not all([s.x_api_key, s.x_api_secret, s.x_access_token, s.x_access_token_secret]):
        raise RuntimeError("X API keys missing in env")
    auth = tweepy.OAuth1UserHandler(
        s.x_api_key, s.x_api_secret, s.x_access_token, s.x_access_token_secret
    )
    return tweepy.API(auth, wait_on_rate_limit=True)


def reply(api: Any, status_id: str, text: str) -> None:
    text = text[:260]
    try:
        api.update_status(
            status=text,
            in_reply_to_status_id=status_id,
            auto_include_user_mentions=True,
        )
    except Exception as e:
        log.exception("reply failed: %s", e)


def handle_mention(api: Any, status: Any) -> None:
    handle = status.user.screen_name
    text = status.text or ""
    s = get_settings()
    if s.x_bot_username:
        text = text.replace(f"@{s.x_bot_username}", "").strip()

    intent = parse(text)
    log.info("@%s intent=%s", handle, intent.kind)

    if intent.kind == "help":
        reply(api, status.id_str, HELP_TEXT)
        return

    if intent.kind == "link":
        if not intent.wallet:
            reply(api, status.id_str, "Send: link <your Solana address>")
            return
        acc = link(handle, intent.wallet)
        reply(api, status.id_str, f"Linked.\n@{acc.x_handle} → {acc.solana_wallet[:8]}…")
        return

    if intent.kind == "whoami":
        acc = get_account(handle)
        if not acc:
            reply(api, status.id_str, "No wallet linked. Optional: link <address>")
        else:
            reply(api, status.id_str, f"@{acc.x_handle}\n{acc.solana_wallet}")
        return

    if intent.kind == "launch":
        ok, reason = guard.can_launch()
        if not ok and reason != "dry_run":
            reply(api, status.id_str, error(reason))
            return

        if not intent.quote:
            reply(api, status.id_str, "Need a quote. Example: launch GameStop paired with GMEX")
            return

        client = StonkFunClient()
        try:
            pairs = client.list_pairs(launchable=True)
            match = next(
                (p for p in pairs if p.symbol.upper() == intent.quote.upper() or p.mint == intent.quote),
                None,
            )
            if not match:
                reply(api, status.id_str, error(f"quote not launchable: {intent.quote}"))
                return

            # creator forced to bot hot wallet inside run_launch
            req = LaunchRequest(
                name=intent.name or intent.symbol or "Stonk",
                symbol=(intent.symbol or "STONK")[:12],
                quote_mint=match.mint,
                creator_wallet="pending",  # overwritten by hot wallet
                mode="standard",
            )

            result = run_launch(req, x_handle=handle)

            if result.status == "dry_run":
                msg = dry_run(req.symbol, match.symbol)
                msg += f"\n{fee_note(s.service_fee_sol)}"
                reply(api, status.id_str, msg)
                return

            if result.status in ("completed", "processing"):
                reply(api, status.id_str, result.message or success(req.symbol, match.symbol, result.stonkfun_url or ""))
            else:
                reply(api, status.id_str, result.message or error("launch failed"))
        except Exception as e:
            guard.on_failure()
            reply(api, status.id_str, error(str(e)))
        finally:
            client.close()
        return

    reply(api, status.id_str, "Try: launch <name> paired with <QUOTE>")


def run_poll_loop(poll_seconds: int = 30) -> None:
    logging.basicConfig(level=logging.INFO)
    api = _client_tweepy()
    s = get_settings()
    bot = (s.x_bot_username or "").lstrip("@").lower()
    since_id = None
    log.info("STONKBOT X poller starting (dry_run=%s automated=True)", s.dry_run)

    while True:
        try:
            kwargs = {"count": 20, "tweet_mode": "extended"}
            if since_id:
                kwargs["since_id"] = since_id
            mentions = api.mentions_timeline(**kwargs)
            for st in reversed(mentions):
                since_id = max(since_id or 0, st.id)
                if st.user.screen_name.lower() == bot:
                    continue
                handle_mention(api, st)
        except Exception as e:
            log.exception("poll error: %s", e)
        time.sleep(poll_seconds)
