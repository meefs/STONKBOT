"""X surface — Bankr-style register + automated launch."""

from __future__ import annotations

import logging
import time
from typing import Any

from .config import get_settings
from .intent import parse
from .launch import run_launch
from .models import LaunchRequest
from .responses import dry_run, success, error, fee_note
from .security import guard
from .solana_pay import get_balance_sol
from .stonkfun_client import StonkFunClient
from .vault import get as get_agent, register as register_agent

log = logging.getLogger("stonkbot.x")

HELP_TEXT = (
    "1) register — get your agent wallet\n"
    "2) fund it (~0.35 SOL)\n"
    "3) launch <name> paired with <QUOTE>\n"
    "You keep 50% creator fees. Service 0.1 SOL."
)


def _api():
    import tweepy

    s = get_settings()
    if not all([s.x_api_key, s.x_api_secret, s.x_access_token, s.x_access_token_secret]):
        raise RuntimeError("X API keys missing")
    auth = tweepy.OAuth1UserHandler(
        s.x_api_key, s.x_api_secret, s.x_access_token, s.x_access_token_secret
    )
    return tweepy.API(auth, wait_on_rate_limit=True)


def reply(api: Any, status_id: str, text: str) -> None:
    try:
        api.update_status(
            status=text[:260],
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
    log.info("@%s → %s", handle, intent.kind)

    if intent.kind == "help":
        reply(api, status.id_str, HELP_TEXT)
        return

    if intent.kind == "register":
        try:
            acc = register_agent(handle)
            reply(
                api,
                status.id_str,
                f"Agent wallet ready.\n{acc.pubkey}\nFund ~{s.min_launch_balance_sol} SOL then launch.",
            )
        except Exception as e:
            reply(api, status.id_str, error(str(e)))
        return

    if intent.kind in ("whoami", "balance"):
        acc = get_agent(handle)
        if not acc:
            reply(api, status.id_str, "Not registered. Say: register")
            return
        try:
            bal = get_balance_sol(acc.pubkey)
            reply(api, status.id_str, f"{acc.pubkey}\nBalance: {bal:.4f} SOL")
        except Exception:
            reply(api, status.id_str, f"{acc.pubkey}\n(balance check failed)")
        return

    if intent.kind == "launch":
        acc = get_agent(handle)
        if not acc:
            reply(api, status.id_str, "Register first: register")
            return

        ok, reason = guard.can_launch()
        if not ok and reason != "dry_run":
            reply(api, status.id_str, error(reason))
            return

        if not intent.quote:
            reply(api, status.id_str, "Example: launch GameStop paired with GMEX")
            return

        client = StonkFunClient()
        try:
            pairs = client.list_pairs(launchable=True)
            match = next(
                (p for p in pairs if p.symbol.upper() == intent.quote.upper() or p.mint == intent.quote),
                None,
            )
            if not match:
                reply(api, status.id_str, error(f"bad quote: {intent.quote}"))
                return

            req = LaunchRequest(
                name=intent.name or intent.symbol or "Stonk",
                symbol=(intent.symbol or "STONK")[:12],
                quote_mint=match.mint,
                creator_wallet=acc.pubkey,
                mode="standard",
            )
            result = run_launch(req, x_handle=handle)

            if result.status == "dry_run":
                msg = dry_run(req.symbol, match.symbol)
                msg += f"\n{fee_note(s.service_fee_sol)}"
                reply(api, status.id_str, msg)
            elif result.status in ("completed", "processing"):
                reply(api, status.id_str, result.message or success(req.symbol, match.symbol, result.stonkfun_url or ""))
            else:
                reply(api, status.id_str, result.message or error("failed"))
        except Exception as e:
            guard.on_failure()
            reply(api, status.id_str, error(str(e)))
        finally:
            client.close()
        return

    reply(api, status.id_str, "Say: register | launch <name> paired with <QUOTE> | help")


def run_poll_loop(poll_seconds: int = 30) -> None:
    logging.basicConfig(level=logging.INFO)
    api = _api()
    s = get_settings()
    bot = (s.x_bot_username or "").lstrip("@").lower()
    since_id = None
    log.info("STONKBOT up dry_run=%s model=agent_wallets", s.dry_run)

    while True:
        try:
            kwargs: dict = {"count": 20, "tweet_mode": "extended"}
            if since_id:
                kwargs["since_id"] = since_id
            for st in reversed(api.mentions_timeline(**kwargs)):
                since_id = max(since_id or 0, st.id)
                if st.user.screen_name.lower() == bot:
                    continue
                handle_mention(api, st)
        except Exception as e:
            log.exception("poll: %s", e)
        time.sleep(poll_seconds)
