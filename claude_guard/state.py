"""State persistence for claude-guard budget tracking."""

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import DEFAULT_STATE_DIR


STATE_FILE = DEFAULT_STATE_DIR / "state.json"
MAX_HISTORY_ENTRIES = 500


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
    hourly_costs: dict = field(default_factory=dict)   # "2026-04-11T14" -> float
    daily_costs: dict = field(default_factory=dict)     # "2026-04-11" -> float
    monthly_costs: dict = field(default_factory=dict)   # "2026-04" -> float
    last_updated: str = ""
    cost_history: list = field(default_factory=list)     # list of CostEntry dicts

    def record_cost(self, amount: float, model: str, session_id: str) -> None:
        now = datetime.now(timezone.utc)
        self.last_updated = now.isoformat()

        # Session tracking
        if session_id != self.current_session_id:
            self.current_session_id = session_id
            self.current_session_cost = 0.0
        self.current_session_cost += amount

        # Time-window aggregation
        hour_key = now.strftime("%Y-%m-%dT%H")
        day_key = now.strftime("%Y-%m-%d")
        month_key = now.strftime("%Y-%m")

        self.hourly_costs[hour_key] = self.hourly_costs.get(hour_key, 0.0) + amount
        self.daily_costs[day_key] = self.daily_costs.get(day_key, 0.0) + amount
        self.monthly_costs[month_key] = self.monthly_costs.get(month_key, 0.0) + amount

        # History for anomaly detection
        entry = CostEntry(
            timestamp=now.isoformat(), amount=amount,
            model=model, session_id=session_id
        )
        self.cost_history.append(entry.to_dict())
        if len(self.cost_history) > MAX_HISTORY_ENTRIES:
            self.cost_history = self.cost_history[-MAX_HISTORY_ENTRIES:]

        # Prune old windows (keep last 48 hours, 60 days, 24 months)
        self._prune_windows(now)

    def get_current_hour_cost(self) -> float:
        hour_key = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
        return self.hourly_costs.get(hour_key, 0.0)

    def get_current_day_cost(self) -> float:
        day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.daily_costs.get(day_key, 0.0)

    def get_current_month_cost(self) -> float:
        month_key = datetime.now(timezone.utc).strftime("%Y-%m")
        return self.monthly_costs.get(month_key, 0.0)

    def _prune_windows(self, now: datetime) -> None:
        # Keep only last 48 hourly entries
        if len(self.hourly_costs) > 48:
            sorted_keys = sorted(self.hourly_costs.keys())
            for k in sorted_keys[:-48]:
                del self.hourly_costs[k]

        # Keep only last 60 daily entries
        if len(self.daily_costs) > 60:
            sorted_keys = sorted(self.daily_costs.keys())
            for k in sorted_keys[:-60]:
                del self.daily_costs[k]

        # Keep only last 24 monthly entries
        if len(self.monthly_costs) > 24:
            sorted_keys = sorted(self.monthly_costs.keys())
            for k in sorted_keys[:-24]:
                del self.monthly_costs[k]

    def save(self, path: Optional[Path] = None) -> None:
        path = path or STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "State":
        path = path or STATE_FILE
        if not path.exists():
            return cls()

        try:
            with open(path, "r") as f:
                raw = json.load(f)
            state = cls()
            for k, v in raw.items():
                if hasattr(state, k):
                    setattr(state, k, v)
            return state
        except (json.JSONDecodeError, KeyError):
            return cls()

    def reset_session(self) -> None:
        self.current_session_id = ""
        self.current_session_cost = 0.0

    def reset_daily(self) -> None:
        day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.daily_costs[day_key] = 0.0
