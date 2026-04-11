# X/Twitter Thread

**Tweet 1 (hook):**
Built a cost circuit breaker for Claude Code.

claude-guard doesn't just show you the bill — it blocks tool calls when you hit your budget.

3 commands to install. Zero dependencies. Open source.

🧵👇

**Tweet 2 (problem):**
Claude Code has "cost limits" but they only warn.

By the time you see the warning, the money's gone.

Agents running overnight. Retry loops burning cash. No hard stop.

**Tweet 3 (solution):**
claude-guard hooks into PreToolUse:

✅ Under budget → normal
⚠️ 80% → warning to Claude
⛔ 100% → tool call BLOCKED
💀 150% → session KILLED

Real enforcement, not suggestions.

**Tweet 4 (how):**
```
pip install claude-guard
claude-guard init
claude-guard install
```

Tracks: session, hourly, daily, monthly budgets
Detects: spending rate spikes (anomaly detection)
Runs: Windows, Mac, Linux
Needs: Python 3.10+, nothing else

**Tweet 5 (comparison):**
Existing tools:
- claude-hud = dashboard (read-only)
- ccusage = post-hoc analyzer
- cccost = logger

claude-guard = circuit breaker (prevention)

Speedometer vs brake pedal.

**Tweet 6 (CTA):**
GitHub: github.com/LuciferDono/claude-guard

Star if you've ever been surprised by a Claude Code bill.

MIT license. PRs welcome.
