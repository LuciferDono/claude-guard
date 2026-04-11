#!/usr/bin/env python3
"""PostToolUse hook — log cost after every tool call.

Triggers a watcher scan to update state from JSONL session files,
then logs the event to history file.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_guard.config import Config, DEFAULT_STATE_DIR
from claude_guard.state import State
from claude_guard.engine import Engine
from claude_guard.watcher import SessionWatcher
from claude_guard.anomaly import AnomalyDetector


def main() -> None:
    # Read hook input from stdin
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    tool_name = hook_input.get("tool_name", "unknown")
    session_id = hook_input.get("session_id", "")

    # Load state and run a quick watcher scan
    config = Config.load()
    state = State.load()
    engine = Engine(config=config, state=state)

    watcher = SessionWatcher(engine)
    watcher.scan_once()

    # Log to history file
    log_file = Path(config.alerts.log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tool": tool_name,
        "session_id": session_id,
        "session_cost": state.current_session_cost,
        "daily_cost": state.get_current_day_cost(),
        "status": engine.check_budget().status.name.lower(),
    }

    try:
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # non-critical — don't break the hook

    # Output empty — PostToolUse doesn't block
    print("{}")


if __name__ == "__main__":
    main()
