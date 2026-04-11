#!/usr/bin/env python3
"""Demo script that simulates claude-guard in action."""

import io
import sys
import time

# Force UTF-8
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

def bar(percent, width=20):
    filled = int(width * min(percent, 100) / 100)
    empty = width - filled
    if percent >= 100:
        color = RED
    elif percent >= 80:
        color = YELLOW
    else:
        color = GREEN
    return f"[{color}{'█' * filled}{DIM}{'░' * empty}{RESET}]"

def status_label(percent):
    if percent >= 100:
        return f" {RED}⛔ BLOCKED{RESET}"
    elif percent >= 80:
        return f" {YELLOW}⚠ WARNING{RESET}"
    return ""

def show_status(session, hourly, daily, monthly, s_lim=5, h_lim=10, d_lim=50, m_lim=500):
    print(f"\n{BOLD}claude-guard{RESET} v0.1.0\n")

    data = [
        ("Session", session, s_lim),
        ("Hourly", hourly, h_lim),
        ("Daily", daily, d_lim),
        ("Monthly", monthly, m_lim),
    ]

    for name, current, limit in data:
        pct = current / limit * 100 if limit > 0 else 0
        b = bar(pct)
        print(f"  {name:8s}  ${current:>7.2f} / ${limit:>7.2f}  {b} {pct:5.1f}%{status_label(pct)}")

    # Overall status
    worst_pct = max(
        session / s_lim * 100 if s_lim > 0 else 0,
        hourly / h_lim * 100 if h_lim > 0 else 0,
        daily / d_lim * 100 if d_lim > 0 else 0,
        monthly / m_lim * 100 if m_lim > 0 else 0,
    )
    if worst_pct >= 100:
        status = f"{RED}{BOLD}DENY{RESET}"
    elif worst_pct >= 80:
        status = f"{YELLOW}{BOLD}WARN{RESET}"
    else:
        status = f"{GREEN}{BOLD}OK{RESET}"

    print(f"\n  Status:   {status}")
    print(f"  Anomaly:  {DIM}normal{RESET}")
    print()


def main():
    # Scene 1: Normal usage
    print(f"{CYAN}${RESET} claude-guard status")
    time.sleep(0.3)
    show_status(session=1.24, hourly=3.80, daily=12.50, monthly=89.30)
    time.sleep(2)

    # Scene 2: Approaching limit
    print(f"{CYAN}${RESET} # ... after some heavy Claude Code usage ...")
    time.sleep(1)
    print(f"{CYAN}${RESET} claude-guard status")
    time.sleep(0.3)
    show_status(session=4.20, hourly=8.50, daily=18.50, monthly=142.30)
    time.sleep(2)

    # Scene 3: Budget exceeded!
    print(f"{CYAN}${RESET} # Claude Code tries to run a tool...")
    time.sleep(1)
    print(f"\n  {RED}🛡 claude-guard: Session budget exceeded: $5.50 / $5.00.{RESET}")
    print(f"  {RED}Tool call BLOCKED → permissionDecision: deny{RESET}")
    time.sleep(1.5)

    print(f"\n{CYAN}${RESET} claude-guard status")
    time.sleep(0.3)
    show_status(session=5.50, hourly=9.80, daily=22.50, monthly=146.30)
    time.sleep(2)

    # Scene 4: Reset and continue
    print(f"{CYAN}${RESET} claude-guard set session 10")
    time.sleep(0.3)
    print(f"  Set session budget to $10.00")
    time.sleep(1)

    print(f"\n{CYAN}${RESET} claude-guard status")
    time.sleep(0.3)
    show_status(session=5.50, hourly=9.80, daily=22.50, monthly=146.30, s_lim=10)
    time.sleep(2)


if __name__ == "__main__":
    main()
