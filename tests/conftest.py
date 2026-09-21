"""Test isolation.

Without this, the suite writes to the user's REAL ~/.claude-guard/state.json,
because State.save() and Config.load() default to home-directory paths. That
was observed in practice: running the suite mutated live budget state, and it
made at least one test order-dependent (passing alone, failing in the suite).

A test run must never touch a user's real state.
"""

import pytest

import claude_guard.config as cfg
import claude_guard.state as st


@pytest.fixture(autouse=True)
def isolate_user_state(tmp_path, monkeypatch):
    home = tmp_path / "fake-home"
    state_dir = home / ".claude-guard"
    state_dir.mkdir(parents=True)

    monkeypatch.setattr(cfg, "DEFAULT_STATE_DIR", state_dir, raising=False)
    monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", home / ".claude-guard.json", raising=False)
    monkeypatch.setattr(st, "DEFAULT_STATE_DIR", state_dir, raising=False)
    monkeypatch.setattr(st, "STATE_FILE", state_dir / "state.json", raising=False)
    monkeypatch.setattr(st, "LOCK_FILE", state_dir / "state.lock", raising=False)

    yield
