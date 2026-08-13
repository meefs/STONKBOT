"""The X v2 surface.

v1.1 (`statuses/mentions_timeline`, `statuses/update`) is retired, so these
tests pin the v2 shapes the loop depends on: mentions come back newest-first
with handles only in the `includes.users` expansion, and replies go out via
`create_tweet(in_reply_to_tweet_id=...)`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from stonkbot.responses import MAX_REPLY_LENGTH
from stonkbot.x_bot import Mention, _fetch_mentions, reply


class FakeResponse:
    def __init__(self, data, users):
        self.data = data
        self.includes = {"users": users} if users else {}


def _tweet(tweet_id: int, author_id: int, text: str):
    return SimpleNamespace(id=tweet_id, author_id=author_id, text=text)


def _user(user_id: int, username: str):
    return SimpleNamespace(id=user_id, username=username)


class FakeClient:
    """Records calls and returns canned v2 payloads."""

    def __init__(self, response=None):
        self.response = response or FakeResponse([], [])
        self.mentions_calls = []
        self.created = []

    def get_users_mentions(self, **kwargs):
        self.mentions_calls.append(kwargs)
        return self.response

    def create_tweet(self, **kwargs):
        self.created.append(kwargs)


def test_fetch_mentions_resolves_handles_from_expansion():
    client = FakeClient(
        FakeResponse(
            [_tweet(200, 99, "@bot launch Foo paired with BAR")],
            [_user(99, "alice")],
        )
    )

    mentions = _fetch_mentions(client, "1234", since_id=None)

    assert mentions == [
        Mention(id=200, text="@bot launch Foo paired with BAR", handle="alice")
    ]
    # The handle keys the wallet vault — it must come from the expansion, never
    # from the author id.
    assert mentions[0].id_str == "200"


def test_fetch_mentions_returns_oldest_first():
    """v2 pages newest-first; since_id bookkeeping needs the reverse."""
    client = FakeClient(
        FakeResponse(
            [_tweet(300, 99, "newer"), _tweet(100, 99, "older")],
            [_user(99, "alice")],
        )
    )

    assert [m.id for m in _fetch_mentions(client, "1234", since_id=None)] == [100, 300]


def test_fetch_mentions_skips_unresolvable_author():
    """A suspended author has no expansion. Skip, never guess a handle."""
    client = FakeClient(
        FakeResponse(
            [_tweet(200, 99, "hi"), _tweet(201, 77, "from a ghost")],
            [_user(99, "alice")],
        )
    )

    mentions = _fetch_mentions(client, "1234", since_id=None)

    assert [m.handle for m in mentions] == ["alice"]


def test_fetch_mentions_handles_empty_page():
    """`data` is None, not [], when nothing is new."""
    assert _fetch_mentions(FakeClient(FakeResponse(None, [])), "1234", None) == []


@pytest.mark.parametrize("since_id, expected", [(None, None), (0, None), (55, 55)])
def test_fetch_mentions_passes_since_id(since_id, expected):
    """0 must go out as None — the API rejects since_id=0."""
    client = FakeClient()

    _fetch_mentions(client, "1234", since_id)

    call = client.mentions_calls[0]
    assert call["since_id"] == expected
    assert call["id"] == "1234"
    assert call["user_auth"] is True


def test_reply_uses_v2_create_tweet():
    client = FakeClient()

    reply(client, "200", "hello")

    assert client.created == [
        {"text": "hello", "in_reply_to_tweet_id": "200", "user_auth": True}
    ]


def test_reply_truncates_to_the_limit():
    client = FakeClient()

    reply(client, "200", "x" * 500)

    assert len(client.created[0]["text"]) == MAX_REPLY_LENGTH


def test_reply_swallows_api_errors():
    """A failed reply must never take down the poll loop."""

    class Boom(FakeClient):
        def create_tweet(self, **kwargs):
            raise RuntimeError("rate limited")

    reply(Boom(), "200", "hello")  # must not raise
