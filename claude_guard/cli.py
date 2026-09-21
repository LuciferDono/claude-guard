"""CLI interface for claude-guard."""

import argparse
import io
import json
import sys
import subprocess
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from .config import Config, DEFAULT_CONFIG_PATH, DEFAULT_STATE_DIR
from .state import State
from .engine import Engine, BudgetStatus
from .anomaly import AnomalyDetector


# ANSI colors
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _bar(percent: float, width: int = 20) -> str:
    """Generate a progress bar."""
    filled = int(width * min(percent, 100) / 100)
    empty = width - filled

    if percent >= 100:
        color = RED
    elif percent >= 80:
        color = YELLOW
    else:
        color = GREEN

    bar = f"{color}{'█' * filled}{DIM}{'░' * empty}{RESET}"
    return f"[{bar}]"


def _status_label(percent: float) -> str:
    if percent >= 100:
        return f" {RED}⛔ BLOCKED{RESET}"
    elif percent >= 80:
        return f" {YELLOW}⚠ WARNING{RESET}"
    return ""


def cmd_init(args: argparse.Namespace) -> None:
    """Create default config file."""
    if DEFAULT_CONFIG_PATH.exists() and not args.force:
        print(f"Config already exists: {DEFAULT_CONFIG_PATH}")
        print("Use --force to overwrite.")
        return

    config = Config()
    config.save()
    print(f"Created config: {DEFAULT_CONFIG_PATH}")
    print(f"Defaults: session=${config.budgets.session:.2f}, "
          f"hourly=${config.budgets.hourly:.2f}, "
          f"daily=${config.budgets.daily:.2f}, "
          f"monthly=${config.budgets.monthly:.2f}")


def cmd_status(args: argparse.Namespace) -> None:
    """Show current spend vs limits."""
    config = Config.load()
    state = State.load()
    engine = Engine(config=config, state=state)

    summary = engine.get_summary()
    result = engine.check_budget()

    print(f"\n{BOLD}claude-guard{RESET} v0.2.0\n")

    for category in ["session", "hourly", "daily", "monthly"]:
        info = summary[category]
        current = info["current"]
        limit = info["limit"]
        pct = info["percent"]

        if limit <= 0:
            label = f"  {category.title():8s}  {DIM}disabled{RESET}"
        else:
            bar = _bar(pct)
            label = (
                f"  {category.title():8s}  ${current:>7.2f} / ${limit:>7.2f}  "
                f"{bar} {pct:5.1f}%{_status_label(pct)}"
            )
        print(label)

    # Overall status
    status_colors = {
        "ok": GREEN, "warn": YELLOW, "deny": RED, "kill": RED,
    }
    status_name = summary["status"]
    color = status_colors.get(status_name, RESET)
    print(f"\n  Status:   {color}{BOLD}{status_name.upper()}{RESET}")

    # Anomaly info
    detector = AnomalyDetector(config.anomaly)
    rate_info = detector.get_rate_summary()
    print(f"  Anomaly:  {DIM}{rate_info['status']}{RESET}")
    print()


def cmd_history(args: argparse.Namespace) -> None:
    """Show recent cost history."""
    config = Config.load()
    log_file = Path(config.alerts.log_file)

    if not log_file.exists():
        print("No history yet.")
        return

    lines = log_file.read_text().strip().split("\n")
    # Show last N entries
    count = args.count or 20
    recent = lines[-count:]

    print(f"\n{BOLD}Recent cost events{RESET} (last {len(recent)}):\n")
    print(f"  {'Timestamp':<22s} {'Tool':<15s} {'Session $':>10s} {'Daily $':>10s} {'Status':>8s}")
    print(f"  {'─' * 22} {'─' * 15} {'─' * 10} {'─' * 10} {'─' * 8}")

    for line in recent:
        try:
            entry = json.loads(line)
            ts = entry.get("timestamp", "?")[:19]
            tool = entry.get("tool", "?")[:15]
            sess = entry.get("session_cost", 0)
            daily = entry.get("daily_cost", 0)
            status = entry.get("status", "?")

            status_color = GREEN if status == "ok" else YELLOW if status == "warn" else RED
            print(f"  {ts:<22s} {tool:<15s} ${sess:>9.2f} ${daily:>9.2f} {status_color}{status:>8s}{RESET}")
        except json.JSONDecodeError:
            continue

    print()


def cmd_set(args: argparse.Namespace) -> None:
    """Set a budget limit."""
    config = Config.load()

    category = args.category.lower()
    amount = args.amount

    if category == "session":
        config.budgets.session = amount
    elif category == "hourly":
        config.budgets.hourly = amount
    elif category == "daily":
        config.budgets.daily = amount
    elif category == "monthly":
        config.budgets.monthly = amount
    else:
        print(f"Unknown category: {category}")
        print("Valid: session, hourly, daily, monthly")
        sys.exit(1)

    config.save()
    print(f"Set {category} budget to ${amount:.2f}")


def cmd_reset(args: argparse.Namespace) -> None:
    """Reset counters."""
    state = State.load()
    target = args.target or "session"

    if target == "session":
        state.reset_session()
        print("Session counter reset.")
    elif target == "daily":
        state.reset_daily()
        print("Daily counter reset.")
    elif target == "all":
        state = State()
        print("All counters reset.")
    else:
        print(f"Unknown target: {target}")
        print("Valid: session, daily, all")
        sys.exit(1)

    state.save()


def cmd_watch(args: argparse.Namespace) -> None:
    """Start watcher daemon (foreground)."""
    import time
    from .watcher import SessionWatcher

    config = Config.load()
    state = State.load()
    engine = Engine(config=config, state=state)
    detector = AnomalyDetector(config.anomaly)

    def on_cost(cost_data: dict) -> None:
        result = engine.check_budget()
        anomaly = detector.record(cost_data["cost"])

        status = result.status.name
        model = cost_data["model"]
        cost = cost_data["cost"]

        color = GREEN if result.status == BudgetStatus.OK else YELLOW if result.status == BudgetStatus.WARN else RED
        print(f"  {color}[{status}]{RESET} ${cost:.4f} ({model}) — session: ${engine.state.current_session_cost:.2f}")

        if anomaly.is_anomaly:
            detector.alert(anomaly)

    watcher = SessionWatcher(engine, on_cost=on_cost, poll_interval=args.interval or 1.0,
                             backfill=getattr(args, 'backfill', False))

    print(f"{BOLD}claude-guard watcher{RESET} — monitoring session files")
    print(f"  Poll interval: {watcher.poll_interval}s")
    print(f"  Config: {DEFAULT_CONFIG_PATH}")
    print(f"  Press Ctrl+C to stop\n")

    try:
        watcher.start()
        while watcher.is_running():
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()
        print("\nWatcher stopped.")


def cmd_doctor(args: argparse.Namespace) -> None:
    """Verify claude-guard can actually see your sessions.

    Exists because the failure that matters most is invisible: if discovery
    finds nothing, the guard records no cost, never trips, and still reports a
    healthy status. Version 0.1.0 did exactly that on every machine.
    """
    from .watcher import diagnose

    report = diagnose()
    state = State.load()

    print(f"\n{BOLD}claude-guard doctor{RESET}\n")
    ok = f"{GREEN}OK{RESET}"
    bad = f"{RED}FAIL{RESET}"

    print(f"  Claude directory   {report['claude_dir']}")
    print(f"    exists           {ok if report['claude_dir_exists'] else bad}")
    print(f"    projects/        {ok if report['projects_dir_exists'] else bad}")
    print(f"  Projects found     {report['project_count']}")
    print(f"  Session files      {report['session_file_count']}")
    if report["newest_session"]:
        print(f"  Newest session     {DIM}{report['newest_session']}{RESET}")
    print()
    print(f"  Config             {DEFAULT_CONFIG_PATH}")
    print(f"  State              {DEFAULT_STATE_DIR / 'state.json'}")
    print(f"  Baselined          {state.baselined_at or 'not yet'}")
    print(f"  Files tracked      {len(state.file_positions)}")
    print(f"  Dedup ledger       {len(state.seen_uuids)} records")
    print()

    if report["healthy"]:
        print(f"  {GREEN}{BOLD}Cost tracking is working.{RESET}\n")
    else:
        for problem in report["problems"]:
            print(f"  {RED}x{RESET} {problem}")
        print(f"\n  {RED}{BOLD}claude-guard is NOT protecting you.{RESET}\n")
        sys.exit(1)


def cmd_install(args: argparse.Namespace) -> None:
    """Install as Claude Code plugin."""
    plugin_root = Path(__file__).resolve().parent.parent
    plugin_json = plugin_root / "plugin.json"

    if not plugin_json.exists():
        print(f"plugin.json not found at {plugin_root}")
        sys.exit(1)

    print(f"Installing claude-guard plugin from: {plugin_root}")
    print(f"\nTo install manually, add to your Claude Code settings:")
    print(f'  claude plugin add "{plugin_root}"')
    print(f"\nOr add to ~/.claude/settings.json:")
    print(f'  "plugins": ["{plugin_root}"]')


def cmd_uninstall(args: argparse.Namespace) -> None:
    """Remove Claude Code plugin."""
    plugin_root = Path(__file__).resolve().parent.parent
    print(f"To uninstall, run:")
    print(f'  claude plugin remove claude-guard')


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="claude-guard",
        description="Cost circuit breaker for Claude Code",
    )
    parser.add_argument("--version", action="version", version="claude-guard 0.2.0")

    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="Create default config")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing config")

    # status
    sub.add_parser("status", help="Show current spend vs limits")

    # history
    p_hist = sub.add_parser("history", help="Show cost history")
    p_hist.add_argument("-n", "--count", type=int, default=20, help="Number of entries")

    # set
    p_set = sub.add_parser("set", help="Set a budget limit")
    p_set.add_argument("category", choices=["session", "hourly", "daily", "monthly"])
    p_set.add_argument("amount", type=float)

    # reset
    p_reset = sub.add_parser("reset", help="Reset counters")
    p_reset.add_argument("target", nargs="?", default="session",
                         choices=["session", "daily", "all"])

    # watch
    p_watch = sub.add_parser("watch", help="Start watcher (foreground)")
    p_watch.add_argument("-i", "--interval", type=float, default=1.0, help="Poll interval in seconds")
    p_watch.add_argument("--backfill", action="store_true",
                         help="Count pre-existing session history instead of starting from now")

    # doctor
    sub.add_parser("doctor", help="Verify cost tracking actually works")

    # install / uninstall
    sub.add_parser("install", help="Install as Claude Code plugin")
    sub.add_parser("uninstall", help="Remove Claude Code plugin")

    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "status": cmd_status,
        "history": cmd_history,
        "set": cmd_set,
        "reset": cmd_reset,
        "watch": cmd_watch,
        "doctor": cmd_doctor,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
