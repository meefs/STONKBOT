"""One poll cycle — the unit the serverless cron invokes.

`poll_once` has to be safe to call repeatedly from a scheduler that gives it no
memory between invocations, so these pin the durable behaviour: the cursor only
moves forward, an already-seen mention is not re-handled, and the bot ignores
itself.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from stonkbot import x_bot
from stonkbot.state import get_since_id, set_since_id


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


def _client(tweets, users):
    return FakeClient(FakeResponse(tweets, users))


def _tweet(tweet_id, author_id, text):
    return SimpleNamespace(id=tweet_id, author_id=author_id, text=text)


def _user(user_id, username):
    return SimpleNamespace(id=user_id, username=username)


@pytest.fixture
def handled(monkeypatch):
    """Capture what reached handle_mention instead of running intents."""
    seen = []
    monkeypatch.setattr(x_bot, "handle_mention", lambda api, m: seen.append(m))
    return seen


def test_advances_the_cursor_to_the_highest_id(handled):
    client = _client(
        [_tweet(300, 9, "hi"), _tweet(100, 9, "older")], [_user(9, "alice")]
    )

    summary = x_bot.poll_once(client, "1", "bot")

    assert summary["handled"] == 2
    assert get_since_id() == 300


def test_cursor_never_moves_backwards(handled):
    set_since_id(500)
    client = _client([_tweet(100, 9, "old")], [_user(9, "alice")])

    x_bot.poll_once(client, "1", "bot")

    assert get_since_id() == 500


def test_a_mention_is_handled_once_across_invocations(handled):
    """Two cron ticks racing on the same page must not double-handle."""
    client = _client([_tweet(300, 9, "launch it")], [_user(9, "alice")])

    x_bot.poll_once(client, "1", "bot")
    second = x_bot.poll_once(client, "1", "bot")

    assert len(handled) == 1
    assert second["handled"] == 0
    assert second["skipped"] == 1


def test_the_bot_ignores_its_own_posts(handled):
    client = _client([_tweet(300, 9, "@someone done")], [_user(9, "StonkBot")])

    summary = x_bot.poll_once(client, "1", "stonkbot")

    assert handled == []
    assert summary["handled"] == 0
    # The cursor still advances: its own post is processed, just not answered.
    assert get_since_id() == 300


def test_one_failing_mention_does_not_abort_the_batch(monkeypatch):
    boom = []

    def explode(api, mention):
        boom.append(mention.id)
        if mention.id == 100:
            raise RuntimeError("intent blew up")

    monkeypatch.setattr(x_bot, "handle_mention", explode)
    # Newest first, as v2 returns them — so 100 is processed before 300.
    client = _client(
        [_tweet(300, 9, "good"), _tweet(100, 9, "bad")], [_user(9, "alice")]
    )

    summary = x_bot.poll_once(client, "1", "bot")

    assert boom == [100, 300]
    assert summary["handled"] == 1
    assert get_since_id() == 300


def test_empty_page_is_a_no_op(handled):
    summary = x_bot.poll_once(_client(None, []), "1", "bot")

    assert summary == {"fetched": 0, "handled": 0, "skipped": 0, "since_id": None}
    assert get_since_id() is None
