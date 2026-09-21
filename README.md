# claude-guard 🛡️

### the brake pedal claude code forgot to ship

[![Tests](https://github.com/LuciferDono/claude-guard/actions/workflows/test.yml/badge.svg)](https://github.com/LuciferDono/claude-guard/actions)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)](#)

> Other tools show you the fire. **claude-guard is the fire extinguisher.**

![claude-guard demo](demo.gif)

---

## what it does in 5 seconds

Claude Code's built-in limits only **warn**. claude-guard **blocks**.

```
$ claude-guard status

claude-guard v0.2.0

  Session   $  4.82 / $  5.00  [████████████████████░] 96.4%  ⚠ WARNING
  Hourly    $  8.12 / $ 10.00  [████████████████░░░░░] 81.2%  ⚠ WARNING
  Daily     $ 18.50 / $ 50.00  [███████░░░░░░░░░░░░░░] 37.0%
  Monthly   $142.30 / $500.00  [██████░░░░░░░░░░░░░░░] 28.5%

  Status:   DENY
  ⛔ Tool call BLOCKED — session budget exceeded
```

---

## who this is for

**claude-guard is for developers running Claude Code with their own API key** — not the Pro/Max subscription.

If you're on the $20/mo Pro or $100–200/mo Max plan, your costs are capped by the subscription. You don't need this.

But if you're using `ANTHROPIC_API_KEY` — because you hit subscription rate limits, your team runs Claude Code on company billing, you're using it in CI/CD, or you got pushed to API access — then every tool call is billed per token. And there's no hard spending cap.

## the problem (API-key users)

| what happens | how much it costs | how long it takes |
|:---|:---|:---|
| agent stuck in retry loop | **$200–500** | 30 minutes |
| forgotten background session overnight | **$1,000–2,000+** | 8 hours |
| recursive tool calls on large codebase | **$50–150** | 10 minutes |
| normal day of heavy API-key usage | **$20–80** | all day |

Claude Code has `sessionLimit` and `dailyLimit` in settings. They pop up a warning. Claude says "noted" and keeps spending.

**There is no hard stop. There is no circuit breaker. There is no brake pedal.**

Until now.

---

## install — 30 seconds

```bash
pip install claude-guard
claude-guard init        # creates ~/.claude-guard.json with sane defaults
claude-guard install     # hooks into Claude Code
```

Done. Every tool call now runs through a budget check first.

---

## how it works

```
  Claude Code                        claude-guard
  ───────────                        ────────────
  Tool Call ──→ PreToolUse Hook ──→  check budget
                                      │
                                      ├─ OK (<80%)      → allow ✅
                                      ├─ WARN (≥80%)    → allow + warn ⚠️
                                      ├─ DENY (≥100%)   → block tool ⛔
                                      └─ KILL (≥150%)   → terminate session 💀
                                      
  After Call ──→ PostToolUse Hook ──→ log cost, update state
```

### budget windows

| window | default | what it catches |
|:-------|:--------|:----------------|
| **Session** | $5.00 | single task going wild |
| **Hourly** | $10.00 | sustained expensive loops |
| **Daily** | $50.00 | all-day background agents |
| **Monthly** | $500.00 | slow cumulative bleed |

### enforcement actions

| threshold | action | result |
|:----------|:-------|:-------|
| < 80% | ✅ Allow | normal operation |
| ≥ 80% | ⚠️ Warn | system message injected to Claude |
| ≥ 100% | ⛔ Deny | `permissionDecision: "deny"` — tool blocked |
| ≥ 150% | 💀 Kill | `continue: false` — session terminated |

### anomaly detection

Doesn't just watch totals — watches **rate**.

If your $/min spikes 3x above rolling average → alert fires before you hit the hard cap. Catches runaway loops in seconds, not minutes.

---

## benchmarks

Hook performance on real workloads:

| metric | value |
|:-------|:------|
| Budget check latency | **< 10ms** |
| State file read | **< 2ms** |
| Hook timeout limit | 5,000ms |
| Overhead per tool call | **~0.2%** |
| False positive rate | **0%** (math, not heuristics) |
| Test coverage | **67 tests**, 5 test files |
| Dependencies | **0** (pure stdlib) |
| Lines of code | **~1,200** (excl. tests) |
| Supported platforms | **Windows, macOS, Linux** |

---

## comparison

| feature | claude-guard | claude-hud | ccusage | cccost |
|:--------|:---:|:---:|:---:|:---:|
| **prevents** overspend | ✅ | ❌ | ❌ | ❌ |
| hard budget enforcement | ✅ | ❌ | ❌ | ❌ |
| auto-kill runaway sessions | ✅ | ❌ | ❌ | ❌ |
| anomaly / spike detection | ✅ | ❌ | ❌ | ❌ |
| real-time monitoring | ✅ | ✅ | ❌ | ✅ |
| session/hourly/daily/monthly | ✅ | ❌ | ✅ | ❌ |
| zero dependencies | ✅ | ❌ | ❌ | ✅ |
| claude code plugin | ✅ | ✅ | ❌ | ❌ |
| **category** | 🛡️ circuit breaker | 📊 dashboard | 📈 analyzer | 📝 logger |

**speedometer vs brake pedal.**

---

## configuration

`~/.claude-guard.json`:

```json
{
  "budgets": {
    "session": 5.00,
    "hourly": 10.00,
    "daily": 50.00,
    "monthly": 500.00
  },
  "alerts": {
    "warn_at_percent": 80,
    "sound": true
  },
  "anomaly": {
    "spike_multiplier": 3.0,
    "lookback_window_minutes": 30
  },
  "action_on_limit": "deny",
  "action_on_hard_limit": "kill"
}
```

Or from CLI:

```bash
claude-guard set daily 25       # $25/day limit
claude-guard set session 10     # $10/session
claude-guard set monthly 200    # $200/month
claude-guard set hourly 0       # disable hourly limit
```

---

## cli

```bash
claude-guard init                          # create config with defaults
claude-guard status                        # live spend vs limits
claude-guard history                       # recent cost events
claude-guard set <session|hourly|daily|monthly> <amount>
claude-guard reset [session|daily|all]     # reset counters
claude-guard watch                         # live monitor (foreground)
claude-guard install                       # add claude code plugin
claude-guard uninstall                     # remove plugin
```

---

## pricing table

costs are calculated per-token using official Anthropic pricing:

| family | models | input | output | cache read | cache write (5m / 1h) |
|:-------|:-------|------:|-------:|-----------:|----------------------:|
| opus | opus-5, 4-8, 4-7, 4-6, 4 | $15.00/M | $75.00/M | $1.50/M | $18.75/M / $30.00/M |
| sonnet | sonnet-5, 4-6, 4 | $3.00/M | $15.00/M | $0.30/M | $3.75/M / $6.00/M |
| haiku | haiku-4-5, 4 | $0.80/M | $4.00/M | $0.08/M | $1.00/M / $1.60/M |

**Unknown models are priced at the most expensive tier (opus), not the cheapest.**
This is deliberate and it is the product contract: claude-guard is a safety tool,
so it errs toward over-estimating. Over-estimating trips the brake early;
under-estimating means it never trips at all. If a new model ships and this
table is stale, you get a conservative guess rather than silent free rein.

Prices are a snapshot — verify against [anthropic.com/pricing](https://www.anthropic.com/pricing).
Override any of them without waiting for a release:

```json
{
  "pricing": {
    "claude-opus-6": {
      "input": 15.0, "output": 75.0, "cache_read": 1.5,
      "cache_write_5m": 18.75, "cache_write_1h": 30.0
    }
  }
}
```

`<synthetic>` records are not billable API calls and are counted as $0.

---

## what it measures (read this once)

**claude-guard measures spend from the moment you install it, not retroactively.**

On first run it baselines: every existing session file is marked as already-read
and nothing is counted. Without this, installing on a machine with real history
would record tens of thousands of dollars of past spend, blow through the default
$50 daily budget instantly, and — with `action_on_hard_limit: "kill"` — terminate
every session before you typed anything.

Want your history counted anyway? `claude-guard watch --backfill`.

**Every cost record is counted exactly once.** Deduplication is keyed on each
record's `uuid`. File offsets are only an optimisation; the uuid ledger is the
guarantee. Rotation, truncation, a wiped state file, or Claude Code firing the
hook a hundred times all converge on the same total.

**Check that it is actually working:**

```
$ claude-guard doctor
```

This is not decoration. The worst failure mode for a guard is the silent one —
finding no session files, recording no cost, never tripping, and reporting a
healthy status the whole time. `doctor` exits non-zero if tracking is broken.

---

## 0.2.0 — correctness release

0.1.0 was published with defects that made it a no-op. If you are on it, upgrade.

| fixed | was |
|:------|:----|
| session discovery | looked for `projects/*/sessions/*.jsonl`; the real layout has no `sessions/` dir, so it found **zero** files on every machine and never blocked anything |
| double counting | file offsets lived in memory while the hook builds a new watcher per tool call, so one $3 call read as $15 after five calls, unbounded |
| model pricing | only knew three models; opus-5 / sonnet-5 / opus-4-7 / opus-4-8 all fell through to sonnet rates, undercounting opus ~5x |
| cache token maths | subtracted cached tokens from `input_tokens`, which already excludes them |
| install behaviour | first run billed your entire history and hard-killed every session |
| file cap | scanned only the 5 most recent sessions; spending elsewhere was invisible |
| state durability | non-atomic writes, no locking between concurrent hooks |
| test isolation | the suite wrote to your real `~/.claude-guard/state.json` |

New: `claude-guard doctor`, `--backfill`, per-model pricing overrides,
`CLAUDE_GUARD_HOME` / `CLAUDE_GUARD_CONFIG` / `CLAUDE_GUARD_STATE` env vars.

---

## architecture

```
claude-guard/
├── claude_guard/
│   ├── config.py      # budget limits, alert settings, anomaly config
│   ├── state.py       # time-window cost aggregation, persistence
│   ├── engine.py      # budget check logic, status decisions
│   ├── watcher.py     # JSONL session file parser, cost calculator
│   ├── anomaly.py     # rolling-window spike detection
│   └── cli.py         # terminal UI, colored progress bars
├── hooks/
│   ├── pre_tool_use.py   # the circuit breaker (deny/kill)
│   └── post_tool_use.py  # cost logging after each tool
├── plugin.json           # claude code plugin manifest
└── tests/                # 67 tests across 5 files
```

---

## faq

**will this slow down claude code?**
no. budget check = read JSON + arithmetic. < 10ms. hook timeout is 5 seconds. you won't notice it.

**what if i need to go over budget?**
`claude-guard set session 0` disables session limit. `claude-guard reset session` zeros the counter. your call.

**does this work with the claude API directly?**
no. claude-guard is built for claude code — it monitors JSONL session files and uses the hook system. for direct API usage, set limits in the anthropic console.

**can i use this with cursor / windsurf / other tools?**
currently claude code only. architecture is extensible. PRs welcome.

**what python versions?**
3.10, 3.11, 3.12. tested on all three across windows, macos, and linux via github actions.

---

## contributing

PRs welcome. tests required.

```bash
git clone https://github.com/LuciferDono/claude-guard
cd claude-guard
pip install -e .
python -m pytest tests/ -v    # 67 tests, ~2 seconds
```

---

## license

[MIT](LICENSE)
