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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Mention:
    """One mention, flattened out of the v2 payload.

    v2 returns author ids, not handles, and the handle only appears in the
    ``includes.users`` expansion. Resolving that once here keeps the intent
    handlers working on a plain object instead of threading `includes` through
    every branch.
    """

    id: int
    text: str
    handle: str

    @property
    def id_str(self) -> str:
        return str(self.id)


def _api() -> Any:
    """An OAuth 1.0a user-context v2 client.

    v1.1 (``tweepy.API``) is retired: ``statuses/mentions_timeline`` and
    ``statuses/update`` are gone, so this is ``tweepy.Client`` throughout.
    OAuth 1.0a rather than bearer token because posting replies needs the user
    context, not app-only auth.
    """
    import tweepy

    s = get_settings()
    if not all(
        [s.x_api_key, s.x_api_secret, s.x_access_token, s.x_access_token_secret]
    ):
        raise RuntimeError("X API credentials missing")
    return tweepy.Client(
        consumer_key=s.x_api_key,
        consumer_secret=s.x_api_secret,
        access_token=s.x_access_token,
        access_token_secret=s.x_access_token_secret,
        wait_on_rate_limit=True,
    )


def _resolve_bot(api: Any) -> tuple[str, str]:
    """(user id, handle) of the authenticated account.

    v2 mentions are fetched by numeric user id, so this call is required before
    the first poll. The handle it returns is authoritative — a mistyped
    ``X_BOT_USERNAME`` would otherwise leave the bot replying to itself.
    """
    me = api.get_me(user_auth=True)
    if not me or not me.data:
        raise RuntimeError("X get_me returned no user — check the access token")
    return str(me.data.id), str(me.data.username)


def reply(api: Any, status_id: str, text: str) -> None:
    try:
        api.create_tweet(
            text=text[:MAX_REPLY_LENGTH],
            in_reply_to_tweet_id=status_id,
            user_auth=True,
        )
    except Exception:
        # Never let a failed reply take down the poll loop.
        log.exception("reply to %s failed", status_id)


def handle_mention(api: Any, status: Mention) -> None:
    settings = get_settings()
    handle = status.handle
    text = status.text

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


def _handle_launch(
    api: Any, status: Mention, handle: str, intent: Any, settings
) -> None:
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


def _fetch_mentions(api: Any, bot_id: str, since_id: int | None) -> list[Mention]:
    """One page of mentions, oldest first, with handles resolved.

    ``max_results`` is capped at 100 by the API and floored at 5; 20 matches
    the old v1.1 ``count`` and keeps per-poll cost predictable on pay-per-use
    billing.
    """
    response = api.get_users_mentions(
        id=bot_id,
        since_id=since_id or None,
        max_results=20,
        expansions=["author_id"],
        tweet_fields=["author_id"],
        user_fields=["username"],
        user_auth=True,
    )

    tweets = response.data or []
    users = {u.id: u.username for u in (response.includes or {}).get("users", [])}

    mentions = []
    for tweet in tweets:
        handle = users.get(tweet.author_id)
        if not handle:
            # No expansion for this author (deleted/suspended mid-page). The
            # handle keys the wallet vault, so guessing one is not an option.
            log.warning("mention %s has no resolvable author, skipping", tweet.id)
            continue
        mentions.append(Mention(id=tweet.id, text=tweet.text, handle=handle))

    # v2 returns newest first; the loop's since_id bookkeeping wants oldest first.
    return list(reversed(mentions))


def _start() -> tuple[Any, str, str]:
    """Authenticate and resolve the bot's own identity.

    Shared by both drivers: the long-running loop does this once at boot, the
    serverless handler once per invocation.
    """
    api = _api()
    bot_id, resolved_handle = _resolve_bot(api)

    configured = (get_settings().x_bot_username or "").lstrip("@").lower()
    if configured and configured != resolved_handle.lower():
        log.warning(
            "X_BOT_USERNAME=%s but the token authenticates as @%s — using @%s",
            configured,
            resolved_handle,
            resolved_handle,
        )
    return api, bot_id, resolved_handle.lower()


def poll_once(api: Any, bot_id: str, bot_username: str) -> dict:
    """Process one page of mentions. Returns a summary for the caller to log.

    This is the whole unit of work, factored out of the loop so it can also be
    driven by a scheduler that gets one short invocation at a time. Everything
    it touches is durable, so being called once a minute by cron and being
    called in a `while True` are the same thing to it.

    Raises on infrastructure failure — advancing the cursor past mentions that
    were never handled would drop them silently, so the caller decides whether
    a failure is retryable.
    """
    since_id = get_since_id()
    mentions = _fetch_mentions(api, bot_id, since_id)

    highest = since_id or 0
    handled = 0
    skipped = 0

    for status in mentions:
        highest = max(highest, status.id)

        if status.handle.lower() == bot_username:
            continue
        if not mark_seen(status.id_str):
            log.debug("already handled %s", status.id_str)
            skipped += 1
            continue

        try:
            handle_mention(api, status)
            handled += 1
        except Exception:
            # One bad mention must not stop the batch.
            log.exception("failed handling mention %s", status.id_str)

    # Only now is the batch genuinely processed. A crash before this point
    # replays the batch, and mark_seen makes that harmless.
    if highest > (since_id or 0):
        set_since_id(highest)

    prune_seen()
    return {
        "fetched": len(mentions),
        "handled": handled,
        "skipped": skipped,
        "since_id": highest or None,
    }


def run_poll_loop(poll_seconds: int = 30) -> None:
    api, bot_id, bot_username = _start()
    settings = get_settings()

    log.info(
        "STONKBOT up as @%s (id=%s) dry_run=%s since_id=%s",
        bot_username,
        bot_id,
        settings.dry_run,
        get_since_id() or "(start of timeline)",
    )

    consecutive_errors = 0

    while True:
        try:
            poll_once(api, bot_id, bot_username)
            consecutive_errors = 0
        except Exception:
            consecutive_errors += 1
            log.exception("poll cycle failed (%d in a row)", consecutive_errors)

        # Back off when X or the network is unhappy, up to 5 minutes.
        delay = poll_seconds * min(2**consecutive_errors, 10) if consecutive_errors else poll_seconds
        time.sleep(min(delay, 300))


def poll_once_standalone() -> dict:
    """One full cycle including authentication — the serverless entry point."""
    api, bot_id, bot_username = _start()
    return poll_once(api, bot_id, bot_username)
