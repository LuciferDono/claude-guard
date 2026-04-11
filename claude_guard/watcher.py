"""JSONL session file watcher for claude-guard."""

import json
import os
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable

from .engine import Engine


# Pricing per million tokens (April 2026)
PRICING = {
    "claude-opus-4-6":   {"input": 15.0, "output": 75.0, "cache_read": 1.875, "cache_create": 18.75},
    "claude-sonnet-4-6": {"input": 3.0,  "output": 15.0, "cache_read": 0.375, "cache_create": 3.75},
    "claude-haiku-4-5":  {"input": 0.80, "output": 4.0,  "cache_read": 0.08,  "cache_create": 1.0},
}

# Aliases — Claude Code sometimes uses short names or versioned names
MODEL_ALIASES = {
    "claude-opus-4-20250514": "claude-opus-4-6",
    "claude-sonnet-4-20250514": "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
}

# Default session directory
CLAUDE_DIR = Path.home() / ".claude"


def calculate_cost(model: str, usage: dict) -> float:
    """Calculate cost in dollars from token usage and model."""
    resolved = MODEL_ALIASES.get(model, model)

    # Find best match if exact match fails
    pricing = PRICING.get(resolved)
    if not pricing:
        for key in PRICING:
            if key in resolved or resolved in key:
                pricing = PRICING[key]
                break
    if not pricing:
        # Unknown model — use sonnet pricing as conservative default
        pricing = PRICING["claude-sonnet-4-6"]

    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_create = usage.get("cache_creation_input_tokens", 0)

    # Non-cached input = total input - cache_read - cache_create
    regular_input = max(0, input_tokens - cache_read - cache_create)

    cost = (
        regular_input * pricing["input"] / 1_000_000
        + output_tokens * pricing["output"] / 1_000_000
        + cache_read * pricing["cache_read"] / 1_000_000
        + cache_create * pricing["cache_create"] / 1_000_000
    )
    return cost


def parse_jsonl_line(line: str) -> Optional[dict]:
    """Parse a JSONL line and extract cost-relevant data if present."""
    line = line.strip()
    if not line:
        return None

    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None

    # Look for API response records with usage data
    # Claude Code JSONL has various record types; we want the ones with token usage
    usage = None
    model = None

    # Direct message format: {"message": {"usage": {...}, "model": "..."}}
    if isinstance(record, dict):
        msg = record.get("message", {})
        if isinstance(msg, dict):
            usage = msg.get("usage")
            model = msg.get("model") or record.get("model")

        # Alternative: top-level usage
        if not usage:
            usage = record.get("usage")
        if not model:
            model = record.get("model")

        # Another format: {"type": "result", ...}
        if not usage and record.get("type") == "result":
            result = record.get("result", {})
            if isinstance(result, dict):
                usage = result.get("usage")
                model = result.get("model") or model

    if not usage or not model:
        return None

    if not isinstance(usage, dict):
        return None

    # Must have at least some tokens
    if usage.get("input_tokens", 0) == 0 and usage.get("output_tokens", 0) == 0:
        return None

    cost = calculate_cost(model, usage)

    return {
        "model": model,
        "cost": cost,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read": usage.get("cache_read_input_tokens", 0),
        "cache_create": usage.get("cache_creation_input_tokens", 0),
    }


def find_session_files(claude_dir: Optional[Path] = None) -> list[Path]:
    """Find all JSONL session files."""
    base = claude_dir or CLAUDE_DIR
    projects_dir = base / "projects"
    if not projects_dir.exists():
        return []

    files = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        sessions_dir = project_dir / "sessions"
        if not sessions_dir.exists():
            continue
        for f in sessions_dir.glob("*.jsonl"):
            files.append(f)

    # Sort by modification time, newest first
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files


def find_active_session_file(claude_dir: Optional[Path] = None) -> Optional[Path]:
    """Find the most recently modified session file."""
    files = find_session_files(claude_dir)
    if not files:
        return None
    return files[0]


class SessionWatcher:
    """Watches Claude Code JSONL session files and feeds costs to Engine."""

    def __init__(
        self,
        engine: Engine,
        claude_dir: Optional[Path] = None,
        on_cost: Optional[Callable[[dict], None]] = None,
        poll_interval: float = 1.0,
    ):
        self.engine = engine
        self.claude_dir = claude_dir or CLAUDE_DIR
        self.on_cost = on_cost  # callback for each cost event
        self.poll_interval = poll_interval

        self._file_positions: dict[str, int] = {}  # path -> last read position
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _get_session_id(self, path: Path) -> str:
        """Extract session ID from file path."""
        return path.stem

    def process_file(self, path: Path) -> int:
        """Process new lines from a session file. Returns number of cost entries found."""
        str_path = str(path)
        last_pos = self._file_positions.get(str_path, 0)

        try:
            file_size = path.stat().st_size
        except OSError:
            return 0

        if file_size <= last_pos:
            if file_size < last_pos:
                # File was truncated/rotated — reset position
                last_pos = 0
            else:
                return 0

        session_id = self._get_session_id(path)
        count = 0

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(last_pos)
                for line in f:
                    result = parse_jsonl_line(line)
                    if result:
                        self.engine.record_cost(
                            amount=result["cost"],
                            model=result["model"],
                            session_id=session_id,
                        )
                        if self.on_cost:
                            self.on_cost(result)
                        count += 1
                self._file_positions[str_path] = f.tell()
        except OSError:
            pass

        return count

    def scan_once(self) -> int:
        """Scan all session files once. Returns total cost entries found."""
        files = find_session_files(self.claude_dir)
        total = 0
        # Only process recent files (last 5 to avoid scanning ancient history)
        for path in files[:5]:
            total += self.process_file(path)
        return total

    def _watch_loop(self) -> None:
        """Background watch loop."""
        while not self._stop_event.is_set():
            self.scan_once()
            self._stop_event.wait(self.poll_interval)

    def start(self) -> None:
        """Start watching in a background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the background watcher."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
