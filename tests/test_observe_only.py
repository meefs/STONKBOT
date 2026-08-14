"""Observe-only mode, and the cursor controls an operator needs after a bad run.

`dry_run` gates SOL and nothing else — a dry run still posts publicly. These
pin the separate switch that gates speech, and the state helpers that let a
consumed timeline be inspected and rewound.
"""

from __future__ import annotations

import pytest

from stonkbot import x_bot
from stonkbot.config import get_settings
from stonkbot.state import (
    clear_seen,
    get_since_id,
    mark_seen,
    seen_recent,
    seen_total,
    set_since_id,
    unset_since_id,
)


class FakeClient:
    def __init__(self):
        self.created = []

    def create_tweet(self, **kwargs):
        self.created.append(kwargs)


@pytest.fixture
def observe(monkeypatch):
    monkeypatch.setenv("STONKBOT_OBSERVE_ONLY", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_observe_only_posts_nothing(observe):
    client = FakeClient()

    x_bot.reply(client, "200", "this must not go out")

    assert client.created == []


def test_observe_only_logs_what_it_would_have_said(observe, caplog):
    with caplog.at_level("INFO", logger="stonkbot.x"):
        x_bot.reply(FakeClient(), "200", "hello there")

    assert "OBSERVE-ONLY" in caplog.text
    assert "hello there" in caplog.text


def test_posting_is_the_default():
    """The safe mode is opt-in, so the default must be verified explicitly."""
    client = FakeClient()

    x_bot.reply(client, "200", "hello")

    assert len(client.created) == 1


def test_unset_since_id_allows_moving_backwards():
    """set_since_id refuses to go backwards; an operator undoing a bad run
    needs to anyway."""
    set_since_id(500)

    unset_since_id()

    assert get_since_id() is None
    set_since_id(100)
    assert get_since_id() == 100


def test_seen_total_and_recent_report_what_was_handled():
    mark_seen("111")
    mark_seen("222")

    assert seen_total() == 2
    assert {row[0] for row in seen_recent(10)} == {"111", "222"}


def test_clear_seen_lets_a_mention_be_handled_again():
    mark_seen("111")
    assert mark_seen("111") is False  # already claimed

    removed = clear_seen()

    assert removed == 1
    assert seen_total() == 0
    assert mark_seen("111") is True
