"""Shared fixtures. Every test runs against a throwaway data dir and dry-run."""

from __future__ import annotations

import pytest

from stonkbot.config import get_settings


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Point all state at a temp dir and force safe defaults.

    autouse so no test can accidentally touch a real vault, ledger or wallet.
    """
    monkeypatch.setenv("STONKBOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STONKBOT_DRY_RUN", "true")
    monkeypatch.setenv("AGENT_VAULT_KEY", "test-vault-key-that-is-long-enough-0123456789")
    monkeypatch.setenv("STONKBOT_SERVICE_FEE_SOL", "0.1")
    monkeypatch.setenv("STONKBOT_REFERRAL_SHARE", "0.30")
    monkeypatch.setenv("STONKBOT_MAX_LAUNCH_COST_SOL", "1.0")
    # Settings are cached; clear around every test so env changes take effect.
    get_settings.cache_clear()
    # The guard is a module-level singleton holding rate-limit counters, so it
    # must be rebuilt per test or one test's launches exhaust another's budget.
    from stonkbot.security import guard

    guard.reset()
    yield
    get_settings.cache_clear()
