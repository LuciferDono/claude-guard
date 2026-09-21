"""Configuration management for claude-guard."""

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


# CLAUDE_GUARD_HOME relocates config and state. Needed for isolated testing
# (hook scripts run as subprocesses and cannot be monkeypatched), for CI, and
# for anyone keeping several independent budgets.
_GUARD_HOME = os.environ.get("CLAUDE_GUARD_HOME")
_BASE = Path(_GUARD_HOME) if _GUARD_HOME else Path.home()

DEFAULT_CONFIG_PATH = (_BASE / "config.json") if _GUARD_HOME else (_BASE / ".claude-guard.json")
DEFAULT_STATE_DIR = (_BASE / "state") if _GUARD_HOME else (_BASE / ".claude-guard")

# Finer-grained overrides, honoured above CLAUDE_GUARD_HOME.
if os.environ.get("CLAUDE_GUARD_CONFIG"):
    DEFAULT_CONFIG_PATH = Path(os.environ["CLAUDE_GUARD_CONFIG"])
if os.environ.get("CLAUDE_GUARD_STATE"):
    DEFAULT_STATE_DIR = Path(os.environ["CLAUDE_GUARD_STATE"]).parent


@dataclass
class BudgetLimits:
    session: float = 5.00
    hourly: float = 10.00
    daily: float = 50.00
    monthly: float = 500.00


@dataclass
class AlertSettings:
    warn_at_percent: int = 80
    sound: bool = True
    log_file: str = str(DEFAULT_STATE_DIR / "history.jsonl")


@dataclass
class AnomalySettings:
    spike_multiplier: float = 3.0
    lookback_window_minutes: int = 30


@dataclass
class Config:
    budgets: BudgetLimits = field(default_factory=BudgetLimits)
    alerts: AlertSettings = field(default_factory=AlertSettings)
    anomaly: AnomalySettings = field(default_factory=AnomalySettings)
    action_on_limit: str = "deny"       # "deny" = block tools, "warn" = warn only
    action_on_hard_limit: str = "kill"  # "kill" = terminate session
    # Optional per-model price overrides, keyed by exact model id. Each value is
    # a dict of USD-per-million-token rates: input, output, cache_read,
    # cache_write_5m, cache_write_1h. Lets a user correct the bundled pricing
    # table without waiting for a release when vendor prices change.
    pricing: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Config":
        path = path or DEFAULT_CONFIG_PATH
        if not path.exists():
            return cls()

        with open(path, "r") as f:
            raw = json.load(f)

        config = cls()
        if "budgets" in raw:
            for k, v in raw["budgets"].items():
                if hasattr(config.budgets, k):
                    setattr(config.budgets, k, float(v))
        if "alerts" in raw:
            for k, v in raw["alerts"].items():
                if hasattr(config.alerts, k):
                    setattr(config.alerts, k, v)
        if "anomaly" in raw:
            for k, v in raw["anomaly"].items():
                if hasattr(config.anomaly, k):
                    setattr(config.anomaly, k, v)
        if "action_on_limit" in raw:
            config.action_on_limit = raw["action_on_limit"]
        if "action_on_hard_limit" in raw:
            config.action_on_hard_limit = raw["action_on_hard_limit"]
        if isinstance(raw.get("pricing"), dict):
            config.pricing = raw["pricing"]

        return config

    def save(self, path: Optional[Path] = None) -> None:
        path = path or DEFAULT_CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    def to_dict(self) -> dict:
        return asdict(self)
