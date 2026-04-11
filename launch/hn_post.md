# Hacker News Post

**Title:** Show HN: Claude-guard – Cost circuit breaker for Claude Code

**URL:** https://github.com/LuciferDono/claude-guard

**Text (if self-post):**

I built claude-guard because Claude Code has no hard spending limits. The built-in sessionLimit and dailyLimit only warn — they don't actually stop tool calls.

claude-guard plugs into Claude Code's hook system (PreToolUse) and acts as a real circuit breaker:

- Under budget → normal operation
- 80% → warning message to Claude
- 100% → tool call BLOCKED (permissionDecision: deny)
- 150% → session KILLED (continue: false)

It also has anomaly detection — if your cost-per-minute spikes 3x above your rolling average, it fires an alert before you hit the hard limit.

Setup is 3 commands:

```
pip install claude-guard
claude-guard init
claude-guard install
```

No external dependencies, pure Python stdlib. Tracks session, hourly, daily, and monthly budgets.

The difference from existing tools like claude-hud (dashboard) and ccusage (post-hoc analyzer) is that claude-guard actually prevents costs rather than just showing them after the fact. Speedometer vs brake pedal.

Would love feedback on the approach. Code: https://github.com/LuciferDono/claude-guard
