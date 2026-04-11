"""Tests for claude-guard JSONL watcher."""

import json
import tempfile
from pathlib import Path

from claude_guard.config import Config, BudgetLimits
from claude_guard.state import State
from claude_guard.engine import Engine
from claude_guard.watcher import (
    calculate_cost, parse_jsonl_line, find_session_files,
    SessionWatcher, PRICING,
)


class TestCalculateCost:
    def test_opus_cost(self):
        usage = {"input_tokens": 1000, "output_tokens": 500,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        cost = calculate_cost("claude-opus-4-6", usage)
        expected = 1000 * 15.0 / 1e6 + 500 * 75.0 / 1e6
        assert abs(cost - expected) < 0.0001

    def test_sonnet_cost(self):
        usage = {"input_tokens": 10000, "output_tokens": 2000,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        cost = calculate_cost("claude-sonnet-4-6", usage)
        expected = 10000 * 3.0 / 1e6 + 2000 * 15.0 / 1e6
        assert abs(cost - expected) < 0.0001

    def test_haiku_cost(self):
        usage = {"input_tokens": 50000, "output_tokens": 10000,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        cost = calculate_cost("claude-haiku-4-5", usage)
        expected = 50000 * 0.80 / 1e6 + 10000 * 4.0 / 1e6
        assert abs(cost - expected) < 0.0001

    def test_cache_tokens(self):
        usage = {"input_tokens": 10000, "output_tokens": 1000,
                 "cache_read_input_tokens": 5000, "cache_creation_input_tokens": 2000}
        cost = calculate_cost("claude-sonnet-4-6", usage)
        # regular_input = 10000 - 5000 - 2000 = 3000
        expected = (
            3000 * 3.0 / 1e6
            + 1000 * 15.0 / 1e6
            + 5000 * 0.375 / 1e6
            + 2000 * 3.75 / 1e6
        )
        assert abs(cost - expected) < 0.0001

    def test_alias_resolution(self):
        usage = {"input_tokens": 1000, "output_tokens": 500,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        cost_alias = calculate_cost("claude-opus-4-20250514", usage)
        cost_direct = calculate_cost("claude-opus-4-6", usage)
        assert cost_alias == cost_direct

    def test_unknown_model_uses_sonnet_pricing(self):
        usage = {"input_tokens": 1000, "output_tokens": 500,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        cost_unknown = calculate_cost("claude-future-9-9", usage)
        cost_sonnet = calculate_cost("claude-sonnet-4-6", usage)
        assert cost_unknown == cost_sonnet

    def test_zero_tokens(self):
        usage = {"input_tokens": 0, "output_tokens": 0}
        cost = calculate_cost("claude-sonnet-4-6", usage)
        assert cost == 0.0


class TestParseJsonlLine:
    def test_valid_message_record(self):
        record = {
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 5000,
                    "output_tokens": 1000,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                }
            }
        }
        result = parse_jsonl_line(json.dumps(record))
        assert result is not None
        assert result["model"] == "claude-sonnet-4-6"
        assert result["input_tokens"] == 5000
        assert result["output_tokens"] == 1000
        assert result["cost"] > 0

    def test_top_level_usage(self):
        record = {
            "model": "claude-haiku-4-5",
            "usage": {"input_tokens": 1000, "output_tokens": 200}
        }
        result = parse_jsonl_line(json.dumps(record))
        assert result is not None
        assert result["model"] == "claude-haiku-4-5"

    def test_result_type_record(self):
        record = {
            "type": "result",
            "result": {
                "model": "claude-opus-4-6",
                "usage": {"input_tokens": 2000, "output_tokens": 500}
            }
        }
        result = parse_jsonl_line(json.dumps(record))
        assert result is not None

    def test_no_usage_returns_none(self):
        record = {"type": "text", "content": "hello"}
        assert parse_jsonl_line(json.dumps(record)) is None

    def test_empty_line(self):
        assert parse_jsonl_line("") is None
        assert parse_jsonl_line("   ") is None

    def test_invalid_json(self):
        assert parse_jsonl_line("not json{{{") is None

    def test_zero_tokens_returns_none(self):
        record = {
            "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }
        assert parse_jsonl_line(json.dumps(record)) is None


class TestSessionWatcher:
    def _make_session_dir(self, tmp: str) -> Path:
        """Create a mock Claude session directory structure."""
        claude_dir = Path(tmp) / ".claude"
        session_dir = claude_dir / "projects" / "test-project" / "sessions"
        session_dir.mkdir(parents=True)
        return claude_dir

    def _write_session_file(self, claude_dir: Path, session_id: str, records: list[dict]) -> Path:
        session_dir = claude_dir / "projects" / "test-project" / "sessions"
        path = session_dir / f"{session_id}.jsonl"
        with open(path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        return path

    def test_process_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = self._make_session_dir(tmp)
            records = [
                {"message": {"model": "claude-sonnet-4-6",
                             "usage": {"input_tokens": 5000, "output_tokens": 1000}}},
                {"type": "text", "content": "not a cost record"},
                {"message": {"model": "claude-sonnet-4-6",
                             "usage": {"input_tokens": 3000, "output_tokens": 500}}},
            ]
            path = self._write_session_file(claude_dir, "sess-123", records)

            engine = Engine(config=Config(), state=State())
            watcher = SessionWatcher(engine, claude_dir=claude_dir)

            count = watcher.process_file(path)
            assert count == 2
            assert engine.state.current_session_cost > 0

    def test_incremental_reading(self):
        """Should not re-read already processed lines."""
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = self._make_session_dir(tmp)
            session_dir = claude_dir / "projects" / "test-project" / "sessions"
            path = session_dir / "sess-inc.jsonl"

            record = {"message": {"model": "claude-sonnet-4-6",
                                  "usage": {"input_tokens": 1000, "output_tokens": 200}}}

            # Write first record
            with open(path, "w") as f:
                f.write(json.dumps(record) + "\n")

            engine = Engine(config=Config(), state=State())
            watcher = SessionWatcher(engine, claude_dir=claude_dir)

            count1 = watcher.process_file(path)
            assert count1 == 1
            cost_after_first = engine.state.current_session_cost

            # Process again — should find nothing new
            count2 = watcher.process_file(path)
            assert count2 == 0
            assert engine.state.current_session_cost == cost_after_first

            # Append new record
            with open(path, "a") as f:
                f.write(json.dumps(record) + "\n")

            count3 = watcher.process_file(path)
            assert count3 == 1
            assert engine.state.current_session_cost > cost_after_first

    def test_scan_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = self._make_session_dir(tmp)
            records = [
                {"message": {"model": "claude-sonnet-4-6",
                             "usage": {"input_tokens": 2000, "output_tokens": 500}}},
            ]
            self._write_session_file(claude_dir, "sess-scan", records)

            engine = Engine(config=Config(), state=State())
            watcher = SessionWatcher(engine, claude_dir=claude_dir)

            total = watcher.scan_once()
            assert total == 1

    def test_find_session_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = self._make_session_dir(tmp)
            self._write_session_file(claude_dir, "sess-a", [])
            self._write_session_file(claude_dir, "sess-b", [])

            files = find_session_files(claude_dir)
            assert len(files) == 2

    def test_callback_on_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = self._make_session_dir(tmp)
            records = [
                {"message": {"model": "claude-sonnet-4-6",
                             "usage": {"input_tokens": 1000, "output_tokens": 200}}},
            ]
            self._write_session_file(claude_dir, "sess-cb", records)

            costs = []
            engine = Engine(config=Config(), state=State())
            watcher = SessionWatcher(engine, claude_dir=claude_dir, on_cost=lambda c: costs.append(c))
            watcher.scan_once()

            assert len(costs) == 1
            assert costs[0]["model"] == "claude-sonnet-4-6"

    def test_file_truncation_resets_position(self):
        """If file shrinks (rotation), reset position to 0."""
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = self._make_session_dir(tmp)
            session_dir = claude_dir / "projects" / "test-project" / "sessions"
            path = session_dir / "sess-trunc.jsonl"

            record = {"message": {"model": "claude-sonnet-4-6",
                                  "usage": {"input_tokens": 1000, "output_tokens": 200}}}

            # Write 3 records
            with open(path, "w") as f:
                for _ in range(3):
                    f.write(json.dumps(record) + "\n")

            engine = Engine(config=Config(), state=State())
            watcher = SessionWatcher(engine, claude_dir=claude_dir)
            watcher.process_file(path)

            # Truncate and write 1 record
            with open(path, "w") as f:
                f.write(json.dumps(record) + "\n")

            count = watcher.process_file(path)
            assert count == 1  # re-read from beginning
