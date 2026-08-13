"""X surface — register, fund, launch.

Hardening over the previous loop:
  * ``since_id`` and handled mention ids persist, so a restart does not replay
    (and re-answer, or re-launch) the whole timeline.
  * Each mention is claimed exactly once before any work is done.
  * The tweet id becomes the launch idempotency key, so the same command can
    never pay twice.
  * Replies are length-bounded and the bot ignores itself.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from .config import get_settings
from .fees import referral_earnings
from .intent import parse
from .launch import run_launch
from .models import LaunchRequest
from .responses import MAX_REPLY_LENGTH, dry_run, error, fee_note, success
from .solana_pay import get_balance_sol
from .state import get_since_id, mark_seen, prune_seen, set_since_id
from .stonkfun_client import StonkFunClient, StonkFunError
from .vault import VaultError
from .vault import get as get_agent
from .vault import register as register_agent

log = logging.getLogger("stonkbot.x")

HELP_TEXT = (
    "1) register — get your agent wallet\n"
    "2) fund it with SOL\n"
    "3) launch <name> paired with <QUOTE>\n"
    "StonkFun splits trading fees 50/50 with creators."
)

# The bare command, so it can be embedded in a sentence without reading oddly.
EXAMPLE_COMMAND = "launch GameStop paired with GMEX"
SYNTAX_HINT = f"Try: {EXAMPLE_COMMAND}"


def _api() -> Any:
    import tweepy

    s = get_settings()
    if not all(
        [s.x_api_key, s.x_api_secret, s.x_access_token, s.x_access_token_secret]
    ):
        raise RuntimeError("X API credentials missing")
    auth = tweepy.OAuth1UserHandler(
        s.x_api_key, s.x_api_secret, s.x_access_token, s.x_access_token_secret
    )
    return tweepy.API(auth, wait_on_rate_limit=True)


def reply(api: Any, status_id: str, text: str) -> None:
    try:
        api.update_status(
            status=text[:MAX_REPLY_LENGTH],
            in_reply_to_status_id=status_id,
            auto_populate_reply_metadata=True,
        )
    except Exception:
        # Never let a failed reply take down the poll loop.
        log.exception("reply to %s failed", status_id)


def _tweet_text(status: Any) -> str:
    """Full text of a tweet, whether or not it was truncated by the API."""
    for attribute in ("full_text", "text"):
        value = getattr(status, attribute, None)
        if value:
            return str(value)
    return ""


def handle_mention(api: Any, status: Any) -> None:
    settings = get_settings()
    handle = status.user.screen_name
    text = _tweet_text(status)

    bot_username = (settings.x_bot_username or "").lstrip("@")
    if bot_username:
        # Case-insensitive: X handles are case-insensitive, so "@STONKFUNBOT"
        # must be stripped as readily as "@StonkFunBot".
        text = re.sub(rf"@{re.escape(bot_username)}\b", " ", text, flags=re.I)

    intent = parse(text)
    log.info("@%s → %s", handle, intent.kind)

    if intent.kind == "help":
        reply(api, status.id_str, HELP_TEXT)
        return

    if intent.kind == "register":
        try:
            account = register_agent(handle)
        except VaultError as e:
            reply(api, status.id_str, error(str(e)))
            return
        except Exception:
            log.exception("registration failed for @%s", handle)
            reply(api, status.id_str, error("could not create wallet, try again"))
            return
        reply(
            api,
            status.id_str,
            f"Agent wallet ready.\n{account.pubkey}\n"
            f"Fund it, then: {EXAMPLE_COMMAND}",
        )
        return

    if intent.kind == "ref":
        account = get_agent(handle)
        if not account:
            reply(api, status.id_str, "Register first: register")
            return
        stats = referral_earnings(handle)
        share = int(settings.referral_share * 100)
        reply(
            api,
            status.id_str,
            f"Your code: {handle.lower()}\n"
            f"Friends add: ref {handle.lower()}\n"
            f"You get {share}% of the {settings.service_fee_sol} SOL STONKBOT fee "
            f"on their launches.\n"
            f"Paid so far: {stats['paid_sol']:.3f} SOL",
        )
        return

    if intent.kind in ("whoami", "balance"):
        account = get_agent(handle)
        if not account:
            reply(api, status.id_str, "Not registered. Say: register")
            return
        try:
            balance = get_balance_sol(account.pubkey)
        except Exception:
            log.exception("balance lookup failed")
            reply(api, status.id_str, f"{account.pubkey}\n(balance check failed)")
            return
        reply(api, status.id_str, f"{account.pubkey}\nBalance: {balance:.4f} SOL")
        return

    if intent.kind == "launch":
        _handle_launch(api, status, handle, intent, settings)
        return

    reply(api, status.id_str, f"{SYNTAX_HINT}\nOr: register | balance | ref | help")


def _handle_launch(api: Any, status: Any, handle: str, intent: Any, settings) -> None:
    account = get_agent(handle)
    if not account:
        reply(api, status.id_str, "Register first: register")
        return

    if not intent.quote:
        reply(api, status.id_str, SYNTAX_HINT)
        return

    # Resolve the quote before doing anything expensive, so a typo costs
    # nothing and returns a useful message.
    try:
        with StonkFunClient() as client:
            pair = client.find_pair(intent.quote)
    except StonkFunError as e:
        reply(api, status.id_str, error(f"StonkFun unavailable ({e.code})"))
        return

    if not pair:
        reply(api, status.id_str, error(f"'{intent.quote}' isn't a launchable pair"))
        return

    try:
        request = LaunchRequest(
            name=intent.name or intent.symbol or "Stonk",
            symbol=intent.symbol or "STONK",
            quote_mint=pair.mint,
            creator_wallet=account.pubkey,
            mode="standard",
        )
    except ValueError as e:
        reply(api, status.id_str, error(f"invalid token details: {e}"))
        return

    result = run_launch(
        request,
        x_handle=handle,
        ref_handle=intent.ref,
        # The tweet id makes this launch exactly-once.
        idempotency_key=f"tweet:{status.id_str}",
    )

    if result.status == "dry_run":
        message = dry_run(request.symbol, pair.symbol)
        reply(api, status.id_str, f"{message}\n{fee_note(settings.service_fee_sol)}")
    elif result.status in ("completed", "processing"):
        reply(
            api,
            status.id_str,
            result.message or success(request.symbol, pair.symbol, result.stonkfun_url or ""),
        )
    else:
        reply(api, status.id_str, result.message or error("launch failed"))


def run_poll_loop(poll_seconds: int = 30) -> None:
    api = _api()
    settings = get_settings()
    bot_username = (settings.x_bot_username or "").lstrip("@").lower()

    if not bot_username:
        log.warning("X_BOT_USERNAME not set — cannot filter the bot's own tweets")

    since_id = get_since_id()
    log.info(
        "STONKBOT up dry_run=%s since_id=%s",
        settings.dry_run,
        since_id or "(start of timeline)",
    )

    consecutive_errors = 0

    while True:
        try:
            kwargs: dict = {"count": 20, "tweet_mode": "extended"}
            if since_id:
                kwargs["since_id"] = since_id

            mentions = api.mentions_timeline(**kwargs)
            highest = since_id or 0

            for status in reversed(mentions):
                highest = max(highest, status.id)

                if bot_username and status.user.screen_name.lower() == bot_username:
                    continue
                if not mark_seen(status.id_str):
                    log.debug("already handled %s", status.id_str)
                    continue

                try:
                    handle_mention(api, status)
                except Exception:
                    # One bad mention must not stop the loop.
                    log.exception("failed handling mention %s", status.id_str)

            # Only now is the batch genuinely processed. A crash before this
            # point replays the batch, and mark_seen makes that harmless.
            if highest > (since_id or 0):
                since_id = highest
                set_since_id(highest)

            prune_seen()
            consecutive_errors = 0
        except Exception:
            consecutive_errors += 1
            log.exception("poll cycle failed (%d in a row)", consecutive_errors)

        # Back off when X or the network is unhappy, up to 5 minutes.
        delay = poll_seconds * min(2**consecutive_errors, 10) if consecutive_errors else poll_seconds
        time.sleep(min(delay, 300))
