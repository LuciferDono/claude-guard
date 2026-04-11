"""Tests for claude-guard core engine."""

import json
import tempfile
from pathlib import Path

from claude_guard.config import Config, BudgetLimits, AlertSettings, AnomalySettings
from claude_guard.state import State, CostEntry
from claude_guard.engine import Engine, BudgetStatus


class TestConfig:
    def test_default_config(self):
        config = Config()
        assert config.budgets.session == 5.00
        assert config.budgets.daily == 50.00
        assert config.action_on_limit == "deny"

    def test_load_missing_file(self):
        config = Config.load(Path("/nonexistent/path.json"))
        assert config.budgets.session == 5.00

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            config = Config()
            config.budgets.daily = 100.0
            config.save(path)

            loaded = Config.load(path)
            assert loaded.budgets.daily == 100.0
            assert loaded.budgets.session == 5.00  # default preserved

    def test_partial_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"budgets": {"daily": 25.0}}))
            config = Config.load(path)
            assert config.budgets.daily == 25.0
            assert config.budgets.session == 5.00  # default


class TestState:
    def test_record_cost(self):
        state = State()
        state.record_cost(1.50, "claude-sonnet-4-6", "sess-1")
        assert state.current_session_cost == 1.50
        assert state.get_current_day_cost() == 1.50
        assert len(state.cost_history) == 1

    def test_session_switch_resets(self):
        state = State()
        state.record_cost(2.00, "claude-sonnet-4-6", "sess-1")
        state.record_cost(1.00, "claude-sonnet-4-6", "sess-2")
        assert state.current_session_cost == 1.00
        assert state.get_current_day_cost() == 3.00  # cumulative

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = State()
            state.record_cost(3.00, "claude-opus-4-6", "sess-1")
            state.save(path)

            loaded = State.load(path)
            assert loaded.current_session_cost == 3.00
            assert len(loaded.cost_history) == 1

    def test_history_pruning(self):
        state = State()
        for i in range(600):
            state.record_cost(0.01, "claude-haiku-4-5", f"sess-{i}")
        assert len(state.cost_history) <= 500

    def test_reset_session(self):
        state = State()
        state.record_cost(5.00, "claude-opus-4-6", "sess-1")
        state.reset_session()
        assert state.current_session_cost == 0.0

    def test_corrupt_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("not json{{{")
            state = State.load(path)
            assert state.current_session_cost == 0.0  # fresh state


class TestEngine:
    def _make_engine(self, session=5.0, hourly=10.0, daily=50.0, monthly=500.0):
        config = Config(budgets=BudgetLimits(
            session=session, hourly=hourly, daily=daily, monthly=monthly
        ))
        state = State()
        return Engine(config=config, state=state)

    def test_ok_status(self):
        engine = self._make_engine()
        engine.state.record_cost(1.00, "claude-sonnet-4-6", "s1")
        result = engine.check_budget()
        assert result.status == BudgetStatus.OK

    def test_warn_at_80_percent(self):
        engine = self._make_engine(session=10.0)
        engine.state.record_cost(8.50, "claude-sonnet-4-6", "s1")
        result = engine.check_budget()
        assert result.status == BudgetStatus.WARN
        assert "Session" in result.reason

    def test_deny_at_100_percent(self):
        engine = self._make_engine(session=5.0)
        engine.state.record_cost(5.50, "claude-sonnet-4-6", "s1")
        result = engine.check_budget()
        assert result.should_block
        assert result.status == BudgetStatus.DENY

    def test_kill_at_150_percent(self):
        engine = self._make_engine(session=5.0)
        engine.state.record_cost(8.00, "claude-sonnet-4-6", "s1")
        result = engine.check_budget()
        assert result.should_kill

    def test_disabled_budget(self):
        engine = self._make_engine(session=0, hourly=0, daily=0, monthly=0)
        engine.state.record_cost(1000.0, "claude-opus-4-6", "s1")
        result = engine.check_budget()
        assert result.status == BudgetStatus.OK

    def test_summary(self):
        engine = self._make_engine(daily=50.0)
        engine.state.record_cost(10.0, "claude-sonnet-4-6", "s1")
        summary = engine.get_summary()
        assert summary["daily"]["current"] == 10.0
        assert summary["daily"]["limit"] == 50.0
        assert summary["daily"]["percent"] == 20.0

    def test_record_cost_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            config = Config()
            state = State()
            engine = Engine(config=config, state=state)

            # Monkey-patch state file path for test
            original_save = state.save
            state.save = lambda path=None: original_save(state_path)

            engine.record_cost(2.50, "claude-sonnet-4-6", "s1")

            loaded = State.load(state_path)
            assert loaded.current_session_cost == 2.50
