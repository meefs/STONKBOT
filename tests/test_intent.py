"""Intent parsing — the front door for every user command."""

from __future__ import annotations

import pytest

from stonkbot.intent import parse


@pytest.mark.parametrize(
    "text,name,symbol,quote",
    [
        ("launch GameStop paired with GMEX", "GameStop", "GAMESTOP", "GMEX"),
        ("launch GameStop vs GMEX", "GameStop", "GAMESTOP", "GMEX"),
        ("deploy Tesla against TSLAX", "Tesla", "TESLA", "TSLAX"),
        (
            'launch a token called "Moon Corp" with ticker MOON paired with SPYX',
            "Moon Corp",
            "MOON",
            "SPYX",
        ),
    ],
)
def test_launch_forms(text, name, symbol, quote):
    intent = parse(text)
    assert intent.kind == "launch"
    assert intent.name == name
    assert intent.symbol == symbol
    assert intent.quote == quote


def test_register_beats_help():
    """'how do I register' must register, not print help.

    The previous parser checked help first and matched the word 'how'.
    """
    assert parse("how do I register").kind == "register"
    assert parse("register").kind == "register"


def test_incomplete_launch_is_not_a_launch():
    """A launch with no quote must not become a launch.

    Guessing a quote here would spend real SOL on the wrong pair.
    """
    assert parse("launch GameStop").kind != "launch"
    assert parse("launch").kind != "launch"


def test_reserved_words_are_not_token_names():
    assert parse("launch help paired with GMEX").kind != "launch"


def test_ref_is_extracted_and_stripped_from_name():
    intent = parse("launch GameStop paired with GMEX ref alice")
    assert intent.kind == "launch"
    assert intent.ref == "alice"
    # The ref clause must not leak into permanent on-chain metadata.
    assert "ref" not in (intent.name or "").lower()
    assert intent.name == "GameStop"


def test_ref_command_alone():
    intent = parse("ref")
    assert intent.kind == "ref"


def test_wallet_word_does_not_hijack_a_launch():
    """'wallet' used to match the whoami pattern and swallow launches."""
    intent = parse("launch Wallet Inc paired with GMEX")
    assert intent.kind == "launch"
    assert intent.quote == "GMEX"


def test_balance_and_whoami():
    assert parse("balance").kind == "balance"
    assert parse("whoami").kind == "whoami"
    assert parse("my wallet").kind == "whoami"


@pytest.mark.parametrize("text", ["", "   ", "gm", "wen moon", "🚀🚀🚀"])
def test_junk_is_unknown(text):
    assert parse(text).kind == "unknown"


def test_malicious_input_does_not_crash():
    for payload in [
        "launch " + "A" * 5000 + " paired with GMEX",
        "launch '; DROP TABLE agent_wallets; -- paired with GMEX",
        "launch \x00\x01null paired with GMEX",
        "register\nregister\nregister",
    ]:
        parse(payload)  # must not raise
