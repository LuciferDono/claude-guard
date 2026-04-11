#!/usr/bin/env python3
"""PreToolUse hook — budget check before every tool call.

Reads current state, checks budget, and returns:
- {} if OK (allow tool)
- {"systemMessage": "..."} if WARNING
- {"hookSpecificOutput": {"permissionDecision": "deny", ...}} if DENY
- {"continue": false, "stopReason": "..."} if KILL
"""

import json
import sys
from pathlib import Path

# Add parent directory to path so we can import claude_guard
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_guard.config import Config
from claude_guard.state import State
from claude_guard.engine import Engine, BudgetStatus


def main() -> None:
    # Read hook input from stdin
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    # Load config and state
    config = Config.load()
    state = State.load()
    engine = Engine(config=config, state=state)

    # Check budget
    result = engine.check_budget()

    if result.status == BudgetStatus.OK:
        # Allow — empty response
        print("{}")

    elif result.status == BudgetStatus.WARN:
        # Allow but warn
        output = {
            "systemMessage": f"⚠ claude-guard: {result.reason}"
        }
        print(json.dumps(output))

    elif result.status == BudgetStatus.DENY:
        # Block this tool call
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"🛡 claude-guard: {result.reason}. "
                    f"Use 'claude-guard set {result.category} <amount>' to adjust, "
                    f"or 'claude-guard reset' to reset counters."
                ),
            }
        }
        print(json.dumps(output))
        sys.exit(2)

    elif result.status == BudgetStatus.KILL:
        # Terminate session
        output = {
            "continue": False,
            "stopReason": (
                f"🛡 CLAUDE-GUARD: Hard budget limit exceeded. "
                f"{result.reason}. Session terminated to prevent cost overrun."
            ),
        }
        print(json.dumps(output))
        sys.exit(2)


if __name__ == "__main__":
    main()
