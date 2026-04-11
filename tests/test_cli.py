"""Tests for claude-guard CLI."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from claude_guard.cli import _bar, _status_label, cmd_set, cmd_reset
from claude_guard.config import Config, DEFAULT_CONFIG_PATH
from claude_guard.state import State


class TestBarRendering:
    def test_bar_low(self):
        bar = _bar(20.0)
        assert "█" in bar
        assert "░" in bar

    def test_bar_high(self):
        bar = _bar(90.0)
        assert "█" in bar

    def test_bar_over(self):
        bar = _bar(150.0)
        assert "█" in bar

    def test_status_label_ok(self):
        label = _status_label(50.0)
        assert label == ""

    def test_status_label_warn(self):
        label = _status_label(85.0)
        assert "WARNING" in label

    def test_status_label_blocked(self):
        label = _status_label(105.0)
        assert "BLOCKED" in label


class TestCLICommands:
    def test_set_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = Config()
            config.save(config_path)

            with patch("claude_guard.cli.Config.load", return_value=Config.load(config_path)):
                with patch("claude_guard.cli.Config.save") as mock_save:
                    import argparse
                    args = argparse.Namespace(category="daily", amount=75.0)
                    cmd_set(args)

    def test_reset_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state = State()
            state.record_cost(5.0, "claude-sonnet-4-6", "s1")
            state.save(state_path)

            with patch("claude_guard.cli.State.load", return_value=State.load(state_path)):
                with patch.object(State, "save"):
                    import argparse
                    args = argparse.Namespace(target="session")
                    cmd_reset(args)

    def test_cli_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "claude_guard.cli", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "claude-guard" in result.stdout

    def test_cli_version(self):
        result = subprocess.run(
            [sys.executable, "-m", "claude_guard.cli", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "0.1.0" in result.stdout

    def test_cli_status(self):
        result = subprocess.run(
            [sys.executable, "-m", "claude_guard.cli", "status"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "claude-guard" in result.stdout
