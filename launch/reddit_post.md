# Reddit Posts

## r/ClaudeCode

**Title:** I built a cost circuit breaker for Claude Code — it actually blocks tool calls when you hit your budget

**Body:**

Got tired of the built-in cost limits only showing warnings. So I built claude-guard — a Claude Code plugin that uses PreToolUse hooks to hard-block tool calls when you exceed your budget.

How it works:
- Monitors your spending in real-time via JSONL session files
- At 80% budget → warns Claude
- At 100% → blocks the tool call entirely
- At 150% → kills the session dead

It tracks session, hourly, daily, and monthly limits. Also has anomaly detection for spending spikes.

Setup:
```
pip install claude-guard
claude-guard init
claude-guard install
```

Zero dependencies, pure stdlib Python. Works on Windows, Mac, Linux.

The existing tools (claude-hud, ccusage, cccost) are all read-only — they show you costs but don't prevent them. claude-guard is the brake pedal.

GitHub: https://github.com/LuciferDono/claude-guard

Open source, MIT. Feedback welcome.

---

## r/programming

**Title:** claude-guard: Cost circuit breaker that prevents runaway API spending in Claude Code

**Body:**

Built an open-source tool that prevents Claude Code from racking up unexpected API bills. It hooks into Claude Code's PreToolUse event and blocks tool calls when spending exceeds configured limits.

The interesting technical bit: Claude Code has a hook system where you can intercept tool calls before they execute. Return `permissionDecision: "deny"` and the tool is blocked. Return `continue: false` and the entire session dies.

claude-guard uses this to enforce hard budget limits (session/hourly/daily/monthly), with anomaly detection that catches spending rate spikes before they hit the hard cap.

Pure Python, no dependencies, ~2300 lines including tests. 67 tests passing across Windows/Mac/Linux.

GitHub: https://github.com/LuciferDono/claude-guard
