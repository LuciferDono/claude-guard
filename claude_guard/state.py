"""State persistence for claude-guard budget tracking.

Durability contract
-------------------
* Writes are atomic (temp file + replace). A crash mid-write leaves the previous
  good state, never a truncated file.
* Concurrent writers are serialised by an advisory lock file. Claude Code fires
  hooks per tool call and several can overlap; without this, read-modify-write
  races silently lose recorded cost.
* A corrupt or unreadable state file degrades to empty state rather than raising.
  A cost guard that crashes on startup stops guarding.
"""

import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import DEFAULT_STATE_DIR


_STATE_ENV = os.environ.get("CLAUDE_GUARD_STATE")
STATE_FILE = Path(_STATE_ENV) if _STATE_ENV else (DEFAULT_STATE_DIR / "state.json")
LOCK_FILE = STATE_FILE.parent / (STATE_FILE.name + ".lock")

MAX_HISTORY_ENTRIES = 500

# Bound on the deduplication ledger. File offsets handle the common path; this
# ledger is the backstop that makes double-counting structurally impossible when
# an offset is rewound (rotation, truncation, a fresh install, a cleared state).
# It must comfortably exceed the number of cost records in a single large
# session file, since a rewind re-reads one from the beginning.
MAX_SEEN_UUIDS = 25_000

# Lock acquisition budget. Exceeding it proceeds anyway: a delayed write is
# better than a hook that hangs and blocks the user's tool call.
LOCK_TIMEOUT_SECONDS = 5.0
LOCK_STALE_SECONDS = 30.0


@contextmanager
def _state_lock(lock_path: Optional[Path] = None):
    """Advisory cross-platform lock via atomic O_EXCL create.

    Portable across Windows and POSIX without fcntl/msvcrt divergence. Stale
    locks (from a killed process) are reclaimed after LOCK_STALE_SECONDS.
    """
    path = lock_path or LOCK_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        yield False
        return

    deadline = time.time() + LOCK_TIMEOUT_SECONDS
    fd = None
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
                if age > LOCK_STALE_SECONDS:
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.time() >= deadline:
                # Proceed unlocked rather than hang the caller's tool call.
                yield False
                return
            time.sleep(0.02)
        except OSError:
            yield False
            return

    try:
        yield True
    finally:
        try:
            if fd is not None:
                os.close(fd)
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


@dataclass
class CostEntry:
    timestamp: str
    amount: float
    model: str
    session_id: str

    def to_dict(self) -> dict:
        return {"timestamp": self.timestamp, "amount": self.amount,
                "model": self.model, "session_id": self.session_id}

    @classmethod
    def from_dict(cls, d: dict) -> "CostEntry":
        return cls(
            timestamp=d["timestamp"], amount=d["amount"],
            model=d.get("model", "unknown"), session_id=d.get("session_id", "")
        )


@dataclass
class State:
    current_session_id: str = ""
    current_session_cost: float = 0.0
    hourly_costs: dict = field(default_factory=dict)    # "2026-09-21T14" -> float
    daily_costs: dict = field(default_factory=dict)     # "2026-09-21" -> float
    monthly_costs: dict = field(default_factory=dict)   # "2026-09" -> float
    last_updated: str = ""
    cost_history: list = field(default_factory=list)    # list of CostEntry dicts

    # Byte offset per session file: {resolved_path: {offset, inode, updated}}.
    # An optimisation so each scan reads only new bytes.
    file_positions: dict = field(default_factory=dict)

    # Ordered ledger of record uuids already counted. The exactly-once guarantee.
    seen_uuids: list = field(default_factory=list)

    # False until the first scan has established a baseline. On a fresh install
    # claude-guard seeks every existing session file to EOF and counts nothing,
    # so it measures spend from the moment it started guarding rather than
    # retroactively billing the user's entire history. Without this, a first run
    # against a mature ~/.claude records tens of thousands of dollars of past
    # spend, instantly exceeds the default $50 daily budget, and — with
    # action_on_hard_limit="kill" — terminates every session on install.
    initialized: bool = False
    baselined_at: str = ""

    # High-water mark of how many files we have ever tracked offsets for.
    # Distinguishes "offsets lost to corruption" (high-water > 0, now 0) from
    # "installed on a machine that had no sessions yet" (high-water == 0, so a
    # file appearing later is genuinely new and must be read in full).
    tracked_high_water: int = 0

    def __post_init__(self) -> None:
        # In-memory index for O(1) membership; rebuilt on load, never persisted.
        self._seen_index: set = set(self.seen_uuids)

    # -- deduplication ------------------------------------------------------

    def mark_seen(self, uid: Optional[str]) -> bool:
        """Record a uuid as counted. Returns True if it is new.

        A record with no uuid cannot be deduplicated. It is counted, because
        under-counting defeats a cost guard, and the caller is responsible for
        not re-reading bytes it has already consumed.
        """
        if not uid:
            return True
        if not hasattr(self, "_seen_index"):
            self._seen_index = set(self.seen_uuids)
        if uid in self._seen_index:
            return False
        self._seen_index.add(uid)
        self.seen_uuids.append(uid)
        if len(self.seen_uuids) > MAX_SEEN_UUIDS:
            dropped = self.seen_uuids[:-MAX_SEEN_UUIDS]
            self.seen_uuids = self.seen_uuids[-MAX_SEEN_UUIDS:]
            self._seen_index.difference_update(dropped)
        return True

    def has_seen(self, uid: Optional[str]) -> bool:
        if not uid:
            return False
        if not hasattr(self, "_seen_index"):
            self._seen_index = set(self.seen_uuids)
        return uid in self._seen_index

    def prune_file_positions(self, keep: set) -> None:
        """Drop offsets for files that no longer exist."""
        if not self.file_positions:
            return
        for key in [k for k in self.file_positions if k not in keep]:
            del self.file_positions[key]

    # -- cost recording -----------------------------------------------------

    def record_cost(self, amount: float, model: str, session_id: str) -> None:
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return
        if amount != amount or amount < 0:  # NaN or negative
            return

        now = datetime.now(timezone.utc)
        self.last_updated = now.isoformat()

        if session_id != self.current_session_id:
            self.current_session_id = session_id
            self.current_session_cost = 0.0
        self.current_session_cost += amount

        hour_key = now.strftime("%Y-%m-%dT%H")
        day_key = now.strftime("%Y-%m-%d")
        month_key = now.strftime("%Y-%m")

        self.hourly_costs[hour_key] = self.hourly_costs.get(hour_key, 0.0) + amount
        self.daily_costs[day_key] = self.daily_costs.get(day_key, 0.0) + amount
        self.monthly_costs[month_key] = self.monthly_costs.get(month_key, 0.0) + amount

        entry = CostEntry(
            timestamp=now.isoformat(), amount=amount,
            model=model, session_id=session_id
        )
        self.cost_history.append(entry.to_dict())
        if len(self.cost_history) > MAX_HISTORY_ENTRIES:
            self.cost_history = self.cost_history[-MAX_HISTORY_ENTRIES:]

        self._prune_windows(now)

    def get_current_hour_cost(self) -> float:
        return self.hourly_costs.get(
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H"), 0.0
        )

    def get_current_day_cost(self) -> float:
        return self.daily_costs.get(
            datetime.now(timezone.utc).strftime("%Y-%m-%d"), 0.0
        )

    def get_current_month_cost(self) -> float:
        return self.monthly_costs.get(
            datetime.now(timezone.utc).strftime("%Y-%m"), 0.0
        )

    def _prune_windows(self, now: datetime) -> None:
        for bucket, keep in (
            (self.hourly_costs, 48),
            (self.daily_costs, 60),
            (self.monthly_costs, 24),
        ):
            if len(bucket) > keep:
                for k in sorted(bucket.keys())[:-keep]:
                    del bucket[k]

    # -- persistence --------------------------------------------------------

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_seen_index", None)
        return d

    def save(self, path: Optional[Path] = None) -> None:
        path = path or STATE_FILE
        lock_path = path.parent / (path.name + ".lock")
        with _state_lock(lock_path):
            try:
                _atomic_write_json(path, self.to_dict())
            except OSError:
                pass  # never let a failed write break the caller's tool call

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "State":
        path = path or STATE_FILE
        if not path.exists():
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError, ValueError, UnicodeError):
            return cls()

        if not isinstance(raw, dict):
            return cls()

        state = cls()
        for k, v in raw.items():
            if k.startswith("_"):
                continue
            if hasattr(state, k):
                setattr(state, k, v)

        # Re-normalise anything the file may have corrupted.
        for name in ("hourly_costs", "daily_costs", "monthly_costs", "file_positions"):
            if not isinstance(getattr(state, name, None), dict):
                setattr(state, name, {})
        for name in ("cost_history", "seen_uuids"):
            if not isinstance(getattr(state, name, None), list):
                setattr(state, name, [])
        try:
            state.current_session_cost = float(state.current_session_cost)
        except (TypeError, ValueError):
            state.current_session_cost = 0.0

        state._seen_index = set(state.seen_uuids)
        return state

    def reset_session(self) -> None:
        self.current_session_id = ""
        self.current_session_cost = 0.0

    def reset_daily(self) -> None:
        self.daily_costs[datetime.now(timezone.utc).strftime("%Y-%m-%d")] = 0.0
