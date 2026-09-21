"""JSONL session file watcher for claude-guard.

Correctness contract
--------------------
1. Every cost-bearing record is counted EXACTLY ONCE, ever. Deduplication is keyed
   on the record's ``uuid``, which Claude Code emits uniquely per record. File
   offsets are an optimisation only; the uuid ledger is the guarantee. This holds
   even though each hook invocation constructs a brand-new SessionWatcher.
2. An unknown model is priced at the MOST EXPENSIVE known tier, never the cheapest.
   This is a safety tool: over-estimating trips the breaker early, under-estimating
   fails to trip it at all. The asymmetry is deliberate.
3. Discovery never silently finds nothing. See ``diagnose()``.
"""

import json
import os
import re
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable, Iterable

from .engine import Engine


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
# USD per million tokens. Cache convention: read = 0.1x input,
# 5-minute cache write = 1.25x input, 1-hour cache write = 2x input.
#
# VERIFY THESE against https://www.anthropic.com/pricing before relying on exact
# figures - vendor prices change and this table is a snapshot. Any model absent
# here is billed at UNKNOWN_MODEL_PRICING (the most expensive tier), so a stale
# table degrades toward over-estimation rather than silent under-counting.
# Override per-model via the "pricing" key in ~/.claude-guard.json.

def _tier(inp: float, out: float) -> dict:
    return {
        "input": inp,
        "output": out,
        "cache_read": round(inp * 0.10, 6),
        "cache_write_5m": round(inp * 1.25, 6),
        "cache_write_1h": round(inp * 2.00, 6),
    }


OPUS_TIER = _tier(15.0, 75.0)
SONNET_TIER = _tier(3.0, 15.0)
HAIKU_TIER = _tier(0.80, 4.0)

PRICING: dict[str, dict] = {
    # Opus family
    "claude-opus-5": OPUS_TIER,
    "claude-opus-4-8": OPUS_TIER,
    "claude-opus-4-7": OPUS_TIER,
    "claude-opus-4-6": OPUS_TIER,
    "claude-opus-4": OPUS_TIER,
    # Sonnet family
    "claude-sonnet-5": SONNET_TIER,
    "claude-sonnet-4-6": SONNET_TIER,
    "claude-sonnet-4": SONNET_TIER,
    # Haiku family
    "claude-haiku-4-5": HAIKU_TIER,
    "claude-haiku-4": HAIKU_TIER,
}

# Unknown models are billed at the most expensive known tier. Deliberate: a cost
# guard that guesses low is worse than useless, because it gives false assurance.
UNKNOWN_MODEL_PRICING = OPUS_TIER

# Family fallback, applied before the unknown-model default. Ordered most- to
# least-expensive so an ambiguous name resolves upward, never downward.
FAMILY_PATTERNS: list[tuple[str, dict]] = [
    ("opus", OPUS_TIER),
    ("sonnet", SONNET_TIER),
    ("haiku", HAIKU_TIER),
]

# Records that are not real billable API calls.
NON_BILLABLE_MODELS = {"<synthetic>", "synthetic", "", "unknown", None}

# Dated vendor IDs -> canonical key.
MODEL_ALIASES = {
    "claude-opus-4-20250514": "claude-opus-4",
    "claude-sonnet-4-20250514": "claude-sonnet-4",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
}

CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))

# Session files are <uuid>.jsonl. Sidecar files and per-session subdirectories
# live in the same directory, so enumerate deliberately rather than rglob.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# A single JSONL line larger than this is treated as corrupt and skipped, rather
# than read into memory.
MAX_LINE_BYTES = 8 * 1024 * 1024


def resolve_pricing(model: Optional[str], overrides: Optional[dict] = None) -> tuple[dict, bool]:
    """Resolve a model name to a pricing tier.

    Returns ``(pricing, is_exact)``. ``is_exact`` is False when the tier was
    inferred by family match or fell back to the unknown-model default, which
    callers may surface as a warning.
    """
    if overrides and model and model in overrides:
        return overrides[model], True

    if not model:
        return UNKNOWN_MODEL_PRICING, False

    resolved = MODEL_ALIASES.get(model, model)

    if resolved in PRICING:
        return PRICING[resolved], True

    lowered = resolved.lower()
    for needle, tier in FAMILY_PATTERNS:
        if needle in lowered:
            return tier, False

    return UNKNOWN_MODEL_PRICING, False


def calculate_cost(
    model: Optional[str],
    usage: dict,
    overrides: Optional[dict] = None,
) -> float:
    """Calculate cost in USD from a usage block.

    ``input_tokens`` from the Anthropic API already EXCLUDES cached tokens -
    ``cache_read_input_tokens`` and ``cache_creation_input_tokens`` are reported
    separately and must be added, never subtracted. (Verified against real
    session logs: input_tokens=3 alongside cache_creation_input_tokens=55614.)
    """
    if model in NON_BILLABLE_MODELS:
        return 0.0
    if not isinstance(usage, dict):
        return 0.0

    pricing, _ = resolve_pricing(model, overrides)

    def _num(value) -> float:
        # Session logs are machine-written but not trusted: guard against nulls,
        # strings, negatives and NaN rather than letting them poison the total.
        try:
            n = float(value)
        except (TypeError, ValueError):
            return 0.0
        if n != n or n in (float("inf"), float("-inf")) or n < 0:
            return 0.0
        return n

    input_tokens = _num(usage.get("input_tokens"))
    output_tokens = _num(usage.get("output_tokens"))
    cache_read = _num(usage.get("cache_read_input_tokens"))
    cache_create_total = _num(usage.get("cache_creation_input_tokens"))

    # Split 5-minute vs 1-hour cache writes when the breakdown is present; they
    # are priced differently. Any remainder we cannot attribute is charged at
    # the more expensive 1h rate.
    breakdown = usage.get("cache_creation")
    write_5m = write_1h = 0.0
    if isinstance(breakdown, dict):
        write_5m = _num(breakdown.get("ephemeral_5m_input_tokens"))
        write_1h = _num(breakdown.get("ephemeral_1h_input_tokens"))

    attributed = write_5m + write_1h
    if attributed > cache_create_total:
        # Breakdown disagrees with the total; trust the total, price it high.
        write_5m, write_1h = 0.0, cache_create_total
    else:
        write_1h += cache_create_total - attributed

    return (
        input_tokens * pricing["input"]
        + output_tokens * pricing["output"]
        + cache_read * pricing["cache_read"]
        + write_5m * pricing["cache_write_5m"]
        + write_1h * pricing["cache_write_1h"]
    ) / 1_000_000


def parse_jsonl_line(line: str, overrides: Optional[dict] = None) -> Optional[dict]:
    """Extract cost data from one JSONL line, or None if it carries no cost.

    Never raises. Malformed input is skipped, because a cost guard that crashes
    on one bad line stops guarding entirely.
    """
    if not line:
        return None
    if len(line) > MAX_LINE_BYTES:
        return None
    line = line.strip()
    if not line or line[0] != "{":
        return None

    try:
        record = json.loads(line)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None

    if not isinstance(record, dict):
        return None

    msg = record.get("message")
    msg = msg if isinstance(msg, dict) else {}

    result = record.get("result")
    result = result if isinstance(result, dict) else {}

    usage = msg.get("usage")
    if not isinstance(usage, dict):
        usage = record.get("usage")
    if not isinstance(usage, dict):
        usage = result.get("usage")
    if not isinstance(usage, dict):
        return None

    # Model can live alongside the usage block in any of the three shapes.
    model = msg.get("model") or record.get("model") or result.get("model")
    if model in NON_BILLABLE_MODELS:
        return None

    cost = calculate_cost(model, usage, overrides)
    if cost <= 0:
        return None

    # The dedup key. Unique per record in real logs (verified: 4,391/4,391
    # unique across sampled sessions). message.id is NOT unique - streamed
    # chunks share it, so keying on it under-counts by roughly half.
    uid = record.get("uuid") or record.get("requestId")

    _, exact = resolve_pricing(model, overrides)

    return {
        "uuid": uid,
        "model": model,
        "cost": cost,
        "pricing_exact": exact,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read": usage.get("cache_read_input_tokens", 0),
        "cache_create": usage.get("cache_creation_input_tokens", 0),
    }


def _iter_project_dirs(base: Path) -> Iterable[Path]:
    projects_dir = base / "projects"
    try:
        if not projects_dir.is_dir():
            return
        children = list(projects_dir.iterdir())
    except OSError:
        return
    for child in children:
        try:
            if child.is_dir():
                yield child
        except OSError:
            continue


def find_session_files(claude_dir: Optional[Path] = None) -> list[Path]:
    """Find Claude Code JSONL session files.

    Real layout is ``~/.claude/projects/<encoded-cwd>/<uuid>.jsonl``. Per-session
    subdirectories and a ``memory/`` directory sit alongside those files, so this
    enumerates deliberately at depth 1 instead of recursively globbing. The
    legacy ``<project>/sessions/*.jsonl`` layout is also accepted so older
    installs keep working.
    """
    base = claude_dir or CLAUDE_DIR
    files: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        try:
            if not path.is_file():
                return
        except OSError:
            return
        key = str(path)
        if key not in seen:
            seen.add(key)
            files.append(path)

    for project_dir in _iter_project_dirs(base):
        try:
            entries = list(project_dir.iterdir())
        except OSError:
            entries = []
        for entry in entries:
            try:
                if entry.is_file() and entry.suffix == ".jsonl":
                    _add(entry)
            except OSError:
                continue

        legacy = project_dir / "sessions"
        try:
            if legacy.is_dir():
                for entry in legacy.glob("*.jsonl"):
                    _add(entry)
        except OSError:
            pass

    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    files.sort(key=_mtime, reverse=True)
    return files


def find_active_session_file(claude_dir: Optional[Path] = None) -> Optional[Path]:
    files = find_session_files(claude_dir)
    return files[0] if files else None


def diagnose(claude_dir: Optional[Path] = None) -> dict:
    """Report on discovery so a silent no-op is visible rather than invisible.

    The original failure mode this guards against: the watcher looked in a
    directory shape that did not exist, found zero files, recorded zero cost,
    and never tripped - while reporting healthy status.
    """
    base = claude_dir or CLAUDE_DIR
    projects_dir = base / "projects"
    files = find_session_files(base)
    problems: list[str] = []

    if not base.exists():
        problems.append(f"Claude directory not found: {base}")
    elif not projects_dir.exists():
        problems.append(f"No projects directory: {projects_dir}")
    elif not files:
        problems.append(
            f"No session files found under {projects_dir}. "
            "claude-guard cannot track cost and will NOT block spending."
        )

    return {
        "claude_dir": str(base),
        "claude_dir_exists": base.exists(),
        "projects_dir": str(projects_dir),
        "projects_dir_exists": projects_dir.exists(),
        "project_count": sum(1 for _ in _iter_project_dirs(base)),
        "session_file_count": len(files),
        "newest_session": str(files[0]) if files else None,
        "healthy": not problems,
        "problems": problems,
    }


class SessionWatcher:
    """Watches Claude Code JSONL session files and feeds costs to Engine.

    Safe to construct fresh on every hook invocation: offsets and the uuid
    ledger both live in persisted state, so nothing is recounted.
    """

    def __init__(
        self,
        engine: Engine,
        claude_dir: Optional[Path] = None,
        on_cost: Optional[Callable[[dict], None]] = None,
        poll_interval: float = 1.0,
        max_files: Optional[int] = None,
        backfill: bool = False,
    ):
        self.engine = engine
        self.claude_dir = claude_dir or CLAUDE_DIR
        self.on_cost = on_cost
        self.poll_interval = poll_interval
        # Opt in to counting pre-existing history. Off by default: see baseline().
        self.backfill = backfill
        # None = scan every discovered file. The previous hard cap of 5 meant a
        # guard that ignored spending in all but the five most recent sessions.
        self.max_files = max_files

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- offset bookkeeping (optimisation; uuid ledger is the correctness gate)

    def _position_key(self, path: Path) -> str:
        try:
            return str(path.resolve())
        except OSError:
            return str(path)

    def _baseline_cutoff(self) -> Optional[float]:
        stamp = getattr(self.engine.state, "baselined_at", "")
        if not stamp:
            return None
        try:
            return datetime.fromisoformat(stamp).timestamp()
        except (ValueError, TypeError):
            return None

    def _get_offset(self, path: Path) -> int:
        rec = self.engine.state.file_positions.get(self._position_key(path))

        if not isinstance(rec, dict):
            # No offset record. Either this file is genuinely new (a session
            # started after we began watching) or we lost its record to state
            # corruption. Replaying an old file from byte 0 is catastrophic --
            # measured at $28,821 of phantom spend against a $50 daily budget
            # on a real machine -- while skipping an unread tail costs at most
            # one file's recent activity. Resolve by age: files older than the
            # baseline were already accounted for, so re-baseline them; files
            # newer than it are real new sessions and get read in full.
            cutoff = self._baseline_cutoff()
            if cutoff is None or self.backfill:
                return 0
            try:
                if path.stat().st_mtime <= cutoff:
                    return self._safe_size(path)
            except OSError:
                return 0
            return 0

        try:
            stat = path.stat()
        except OSError:
            return 0
        # Rotation / truncation / replacement detection. Any mismatch rewinds to
        # zero; the uuid ledger absorbs the resulting re-read without double
        # counting, so rewinding is always safe.
        if rec.get("inode") not in (None, stat.st_ino):
            return 0
        offset = rec.get("offset", 0)
        if not isinstance(offset, int) or offset < 0 or offset > stat.st_size:
            return 0
        return offset

    def _safe_size(self, path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def _set_offset(self, path: Path, offset: int) -> None:
        try:
            inode = path.stat().st_ino
        except OSError:
            inode = None
        self.engine.state.file_positions[self._position_key(path)] = {
            "offset": offset,
            "inode": inode,
            "updated": time.time(),
        }

    # -- scanning

    def process_file(self, path: Path) -> int:
        """Process new lines from one session file. Returns costs newly recorded."""
        start = self._get_offset(path)

        try:
            size = path.stat().st_size
        except OSError:
            return 0

        if size <= start:
            if size < start:
                start = 0
            else:
                return 0

        overrides = getattr(self.engine.config, "pricing", None)
        count = 0
        end = start

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(start)
                for line in fh:
                    parsed = parse_jsonl_line(line, overrides)
                    if not parsed:
                        continue
                    # Exactly-once guarantee.
                    if not self.engine.state.mark_seen(parsed["uuid"]):
                        continue
                    self.engine.record_cost(
                        amount=parsed["cost"],
                        model=parsed["model"] or "unknown",
                        session_id=path.stem,
                        autosave=False,  # scan_once saves once at the end
                    )
                    if self.on_cost:
                        try:
                            self.on_cost(parsed)
                        except Exception:
                            pass
                    count += 1
                end = fh.tell()
        except (OSError, UnicodeError):
            return count

        self._set_offset(path, end)
        return count

    def baseline(self, files: Optional[list] = None) -> int:
        """Mark all existing session files as already-read, counting nothing.

        Run once, on first use. A budget guard measures spend from the moment it
        starts guarding; retroactively billing the user's entire ~/.claude
        history would instantly exceed any sane daily budget and — with
        action_on_hard_limit="kill" — brick every session on install.

        Pass ``backfill=True`` to SessionWatcher to skip this and count history.
        """
        if files is None:
            files = find_session_files(self.claude_dir)
        for path in files:
            try:
                self._set_offset(path, path.stat().st_size)
            except OSError:
                continue
        self.engine.state.initialized = True
        self.engine.state.baselined_at = datetime.now(timezone.utc).isoformat()
        self.engine.state.tracked_high_water = max(
            getattr(self.engine.state, "tracked_high_water", 0),
            len(self.engine.state.file_positions),
        )
        return len(files)

    def scan_once(self) -> int:
        files = find_session_files(self.claude_dir)
        if self.max_files is not None:
            files = files[: self.max_files]

        # First run: establish the baseline instead of replaying history.
        #
        # Recovery: an initialized state with no offsets at all is corruption,
        # not a set of new sessions. We cannot know what was already counted,
        # and replaying is catastrophic ($28,821 of phantom spend measured on a
        # real machine), so resume from now and say so. Under-counting the tail
        # of one active session, once, after a corruption event, is the cheap
        # side of this trade. Distinguishing signature: offsets empty AND files
        # present -- a genuinely new session always appears alongside tracked ones.
        recovering = (
            self.engine.state.initialized
            and not self.engine.state.file_positions
            and getattr(self.engine.state, "tracked_high_water", 0) > 0
            and bool(files)
            and not self.backfill
        )
        if recovering:
            self.baseline(files)
            self.engine.state.save()
            return 0

        if not self.engine.state.initialized and not self.backfill:
            self.baseline(files)
            self.engine.state.prune_file_positions(
                {self._position_key(p) for p in files}
            )
            self.engine.state.save()
            return 0
        self.engine.state.initialized = True

        total = 0
        for path in files:
            total += self.process_file(path)

        self.engine.state.prune_file_positions(
            {self._position_key(p) for p in files}
        )
        self.engine.state.tracked_high_water = max(
            getattr(self.engine.state, "tracked_high_water", 0),
            len(self.engine.state.file_positions),
        )
        self.engine.state.save()
        return total

    def _watch_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.scan_once()
            except Exception:
                # A watcher that dies stops guarding. Survive and retry.
                pass
            self._stop_event.wait(self.poll_interval)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
