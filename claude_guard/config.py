"""Configuration management for claude-guard."""

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


DEFAULT_CONFIG_PATH = Path.home() / ".claude-guard.json"
DEFAULT_STATE_DIR = Path.home() / ".claude-guard"


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

        return config

    def save(self, path: Optional[Path] = None) -> None:
        path = path or DEFAULT_CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    def to_dict(self) -> dict:
        return asdict(self)
