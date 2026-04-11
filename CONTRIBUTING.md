# Contributing to claude-guard

PRs welcome. Here's how.

## Setup

```bash
git clone https://github.com/LuciferDono/claude-guard
cd claude-guard
pip install -e .
python -m pytest tests/ -v
```

## Rules

1. **Tests required.** No PR gets merged without tests for new functionality.
2. **Zero dependencies.** stdlib only. If you need something external, make it optional.
3. **Hook latency matters.** PreToolUse hook must stay under 50ms. Don't add I/O to the hot path.
4. **Cross-platform.** Must work on Windows, macOS, Linux. Test paths with `pathlib`.

## What to work on

- **Model support** — Add pricing for new Claude models as they launch
- **Integrations** — Cursor, Windsurf, Cline adapter layers
- **Alerts** — Desktop notifications, Slack/Discord webhooks
- **Dashboard** — Web UI for cost visualization (optional dependency)
- **JSONL parsing** — Better coverage of Claude Code's JSONL record formats

## PR process

1. Fork & branch from `master`
2. Write code + tests
3. `python -m pytest tests/ -v` — all green
4. Open PR with description of what and why
