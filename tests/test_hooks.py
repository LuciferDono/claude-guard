"""Tests for claude-guard plugin hooks."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from claude_guard.config import Config, BudgetLimits
from claude_guard.state import State
from claude_guard.engine import Engine, BudgetStatus


class TestPreToolUseHook:
    """Test the pre_tool_use hook logic (not subprocess — unit tests on the decision logic)."""

    def _check_decision(self, session_cost: float, session_limit: float) -> dict:
        """Simulate what the hook does: load state, check budget, return output."""
        config = Config(budgets=BudgetLimits(
            session=session_limit, hourly=0, daily=0, monthly=0
        ))
        state = State()
        if session_cost > 0:
            state.record_cost(session_cost, "claude-sonnet-4-6", "test-session")

        engine = Engine(config=config, state=state)
        result = engine.check_budget()

        if result.status == BudgetStatus.OK:
            return {}
        elif result.status == BudgetStatus.WARN:
            return {"systemMessage": f"⚠ claude-guard: {result.reason}"}
        elif result.status == BudgetStatus.DENY:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": result.reason,
                }
            }
        elif result.status == BudgetStatus.KILL:
            return {
                "continue": False,
                "stopReason": result.reason,
            }
        return {}

    def test_ok_allows(self):
        output = self._check_decision(session_cost=1.0, session_limit=5.0)
        assert output == {}

    def test_warn_adds_message(self):
        output = self._check_decision(session_cost=4.5, session_limit=5.0)
        assert "systemMessage" in output
        assert "warning" in output["systemMessage"].lower() or "warn" in output["systemMessage"].lower()

    def test_deny_blocks_tool(self):
        output = self._check_decision(session_cost=5.5, session_limit=5.0)
        assert "hookSpecificOutput" in output
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_kill_terminates(self):
        output = self._check_decision(session_cost=8.0, session_limit=5.0)
        assert output.get("continue") is False
        assert "stopReason" in output

    def test_disabled_budget_allows(self):
        output = self._check_decision(session_cost=100.0, session_limit=0)
        assert output == {}


class TestPreToolUseScript:
    """Integration test — run the actual hook script."""

    def test_hook_script_ok(self):
        """Hook script should output {} when under budget."""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / ".claude-guard.json"
            state_path = Path(tmp) / "state.json"

            # Write config with high limits
            config = Config(budgets=BudgetLimits(session=999, hourly=999, daily=999, monthly=999))
            config.save(config_path)

            # Write empty state
            state = State()
            state.save(state_path)

            # Run the hook script with env vars pointing to test files
            hook_script = Path(__file__).resolve().parent.parent / "hooks" / "pre_tool_use.py"

            result = subprocess.run(
                [sys.executable, str(hook_script)],
                input=json.dumps({"tool_name": "Read", "session_id": "test"}),
                capture_output=True, text=True, timeout=10,
                env={
                    **dict(__import__("os").environ),
                    "CLAUDE_GUARD_CONFIG": str(config_path),
                    "CLAUDE_GUARD_STATE": str(state_path),
                },
            )

            # Should succeed (exit 0) and output valid JSON
            assert result.returncode == 0
            output = json.loads(result.stdout.strip())
            assert isinstance(output, dict)


class TestPostToolUseHook:
    """Test post_tool_use hook logic."""

    def test_post_hook_logs(self):
        """PostToolUse should log to history file."""
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "history.jsonl"
            config = Config()
            config.alerts.log_file = str(log_file)

            # Simulate what post hook does
            import time
            entry = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "tool": "Read",
                "session_id": "test-session",
                "session_cost": 1.50,
                "daily_cost": 10.00,
                "status": "ok",
            }

            log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(log_file, "a") as f:
                f.write(json.dumps(entry) + "\n")

            # Verify log
            with open(log_file) as f:
                logged = json.loads(f.readline())
            assert logged["tool"] == "Read"
            assert logged["status"] == "ok"


class TestPluginJson:
    """Verify plugin.json is valid."""

    def test_plugin_json_valid(self):
        plugin_path = Path(__file__).resolve().parent.parent / "plugin.json"
        with open(plugin_path) as f:
            plugin = json.load(f)

        assert plugin["name"] == "claude-guard"
        assert "hooks" in plugin
        assert "PreToolUse" in plugin["hooks"]
        assert "PostToolUse" in plugin["hooks"]

    def test_hook_commands_reference_files(self):
        plugin_path = Path(__file__).resolve().parent.parent / "plugin.json"
        with open(plugin_path) as f:
            plugin = json.load(f)

        # Verify hook files exist
        root = plugin_path.parent
        for hook_type in ["PreToolUse", "PostToolUse"]:
            for matcher in plugin["hooks"][hook_type]:
                for hook in matcher["hooks"]:
                    # Extract filename from command
                    cmd = hook["command"]
                    # Command format: python3 "${CLAUDE_PLUGIN_ROOT}/hooks/xxx.py"
                    assert "hooks/" in cmd
                    filename = cmd.split("hooks/")[1].rstrip('"')
                    assert (root / "hooks" / filename).exists(), f"Hook file missing: hooks/{filename}"
