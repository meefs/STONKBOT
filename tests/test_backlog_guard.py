"""The backlog guard — what stops a burst of stale replies.

The cursor only moves forward, so every mention that arrives while the bot is
offline is still waiting when it comes back, and would be answered all at once.
That burst would be the account's first public act after a quiet period. These
pin the refusal, and — just as important — pin that a refusal consumes nothing,
so the decision is still open afterwards.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from stonkbot import x_bot
from stonkbot.config import get_settings
from stonkbot.state import get_since_id, mark_seen, seen_total


class FakeResponse:
    def __init__(self, data, users):
        self.data = data
        self.includes = {"users": users} if users else {}


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.created = []

    def get_users_mentions(self, **kwargs):
        return self.response

    def create_tweet(self, **kwargs):
        self.created.append(kwargs)


def _tweet(tweet_id, author_id, text, age_hours=0.0):
    return SimpleNamespace(
        id=tweet_id,
        author_id=author_id,
        text=text,
        created_at=datetime.now(UTC) - timedelta(hours=age_hours),
    )


def _user(user_id, username):
    return SimpleNamespace(id=user_id, username=username)


def _client(tweets, users):
    return FakeClient(FakeResponse(tweets, users))


def _pile(count, age_hours=0.0):
    """`count` mentions from distinct handles, newest id first (as v2 returns)."""
    tweets = [
        _tweet(1000 + i, 10 + i, "@stonkfunbot register", age_hours)
        for i in reversed(range(count))
    ]
    users = [_user(10 + i, f"user{i}") for i in range(count)]
    return _client(tweets, users)


@pytest.fixture
def limits(monkeypatch):
    """A small, explicit limit so the tests do not depend on the default."""
    monkeypatch.setenv("STONKBOT_BACKLOG_LIMIT", "3")
    monkeypatch.setenv("STONKBOT_BACKLOG_MAX_AGE_HOURS", "24")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def handled(monkeypatch):
    seen = []
    monkeypatch.setattr(x_bot, "handle_mention", lambda api, m: seen.append(m))
    return seen


def test_a_normal_tick_passes_through(limits, handled):
    x_bot.poll_once(_pile(3), "1", "stonkfunbot")

    assert len(handled) == 3


def test_too_many_waiting_refuses(limits, handled):
    with pytest.raises(x_bot.BacklogTooLarge):
        x_bot.poll_once(_pile(4), "1", "stonkfunbot")

    assert handled == []


def test_a_refusal_consumes_nothing(limits, handled):
    """The whole point: after a trip, the mentions are still answerable.

    If the guard advanced the cursor or claimed the mentions on its way out, it
    would have destroyed the thing it was protecting.
    """
    with pytest.raises(x_bot.BacklogTooLarge):
        x_bot.poll_once(_pile(9), "1", "stonkfunbot")

    assert get_since_id() is None
    assert seen_total() == 0


def test_a_single_stale_mention_also_trips_it(limits, handled):
    """Count is not the only signal. One mention from last week, answered now,
    is its own kind of wrong."""
    with pytest.raises(x_bot.BacklogTooLarge) as excinfo:
        x_bot.poll_once(_pile(1, age_hours=200), "1", "stonkfunbot")

    assert excinfo.value.report["pending"] == 1
    assert excinfo.value.report["oldest_hours"] > 24


def test_explicit_acceptance_proceeds(limits, handled, monkeypatch):
    monkeypatch.setenv("STONKBOT_ACCEPT_BACKLOG", "true")
    get_settings.cache_clear()

    x_bot.poll_once(_pile(9), "1", "stonkfunbot")

    assert len(handled) == 9


def test_already_handled_mentions_are_not_a_backlog(limits, handled):
    """Re-reading a page the bot already answered is not a pile-up."""
    for i in range(9):
        mark_seen(str(1000 + i))

    x_bot.poll_once(_pile(9), "1", "stonkfunbot")

    assert handled == []


def test_the_bots_own_posts_are_not_a_backlog(limits, handled):
    tweets = [_tweet(1000 + i, 7, "@someone live") for i in range(9)]
    client = _client(tweets, [_user(7, "StonkFunBot")])

    x_bot.poll_once(client, "1", "stonkfunbot")

    assert handled == []


def test_the_report_names_who_and_what(limits):
    """An operator has to be able to decide from the report alone."""
    tweets = [
        _tweet(1004, 11, "@stonkfunbot launch GameStop paired with GMEX"),
        _tweet(1003, 12, "@stonkfunbot register"),
        _tweet(1002, 13, "@stonkfunbot balance"),
        _tweet(1001, 14, "@stonkfunbot help"),
    ]
    users = [_user(11, "alice"), _user(12, "bob"), _user(13, "carol"), _user(14, "dan")]

    with pytest.raises(x_bot.BacklogTooLarge) as excinfo:
        x_bot.poll_once(_client(tweets, users), "1", "stonkfunbot")

    report = excinfo.value.report
    assert report["pending"] == 4
    assert {i["handle"] for i in report["items"]} == {"alice", "bob", "carol", "dan"}
    intents = {i["handle"]: i["intent"] for i in report["items"]}
    assert intents["alice"] == "launch"
    assert intents["bob"] == "register"
    assert all(i["url"].startswith("https://x.com/i/status/") for i in report["items"])


def test_the_message_says_how_to_proceed(limits):
    with pytest.raises(x_bot.BacklogTooLarge) as excinfo:
        x_bot.poll_once(_pile(9), "1", "stonkfunbot")

    message = str(excinfo.value)
    assert "STONKBOT_ACCEPT_BACKLOG" in message
    assert "cursor --set" in message


def test_the_loop_stops_rather_than_retrying(limits, monkeypatch):
    """Retrying an unactionable refusal every 30s would bury the warning."""
    monkeypatch.setattr(x_bot, "_start", lambda: (_pile(9), "1", "stonkfunbot"))

    def no_sleep(_seconds):
        raise AssertionError("the loop should have returned, not slept")

    monkeypatch.setattr(x_bot.time, "sleep", no_sleep)

    x_bot.run_poll_loop()
