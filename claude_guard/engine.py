"""Budget enforcement engine for claude-guard."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .config import Config
from .state import State


class BudgetStatus(Enum):
    OK = 0
    WARN = 1
    DENY = 2
    KILL = 3


@dataclass
class BudgetCheckResult:
    status: BudgetStatus
    reason: str = ""
    current: float = 0.0
    limit: float = 0.0
    category: str = ""  # "session", "hourly", "daily", "monthly"

    @property
    def should_block(self) -> bool:
        return self.status in (BudgetStatus.DENY, BudgetStatus.KILL)

    @property
    def should_kill(self) -> bool:
        return self.status == BudgetStatus.KILL


class Engine:
    def __init__(self, config: Optional[Config] = None, state: Optional[State] = None):
        self.config = config or Config.load()
        self.state = state or State.load()

    def record_cost(
        self, amount: float, model: str, session_id: str, autosave: bool = True
    ) -> None:
        self.state.record_cost(amount, model, session_id)
        # Batch callers (the watcher, replaying many lines in one scan) pass
        # autosave=False and save once at the end. Taking the state lock per
        # record would stall a hook on a large file.
        if autosave:
            self.state.save()

    def check_budget(self) -> BudgetCheckResult:
        checks = [
            ("session", self.state.current_session_cost, self.config.budgets.session),
            ("hourly", self.state.get_current_hour_cost(), self.config.budgets.hourly),
            ("daily", self.state.get_current_day_cost(), self.config.budgets.daily),
            ("monthly", self.state.get_current_month_cost(), self.config.budgets.monthly),
        ]

        worst = BudgetCheckResult(status=BudgetStatus.OK)

        for category, current, limit in checks:
            if limit <= 0:
                continue  # disabled

            ratio = current / limit

            if ratio >= 1.0:
                # Over budget — check action
                action = self.config.action_on_hard_limit if ratio >= 1.5 else self.config.action_on_limit
                status = BudgetStatus.KILL if action == "kill" else BudgetStatus.DENY

                result = BudgetCheckResult(
                    status=status,
                    reason=f"{category.title()} budget exceeded: ${current:.2f} / ${limit:.2f}",
                    current=current, limit=limit, category=category
                )
                # Return worst (KILL > DENY > WARN > OK)
                if result.status.value > worst.status.value or (
                    result.should_kill and not worst.should_kill
                ):
                    worst = result

            elif ratio >= self.config.alerts.warn_at_percent / 100.0:
                result = BudgetCheckResult(
                    status=BudgetStatus.WARN,
                    reason=f"{category.title()} budget warning: ${current:.2f} / ${limit:.2f} ({ratio:.0%})",
                    current=current, limit=limit, category=category
                )
                if worst.status == BudgetStatus.OK:
                    worst = result

        return worst

    def get_summary(self) -> dict:
        return {
            "session": {
                "current": self.state.current_session_cost,
                "limit": self.config.budgets.session,
                "percent": _pct(self.state.current_session_cost, self.config.budgets.session),
            },
            "hourly": {
                "current": self.state.get_current_hour_cost(),
                "limit": self.config.budgets.hourly,
                "percent": _pct(self.state.get_current_hour_cost(), self.config.budgets.hourly),
            },
            "daily": {
                "current": self.state.get_current_day_cost(),
                "limit": self.config.budgets.daily,
                "percent": _pct(self.state.get_current_day_cost(), self.config.budgets.daily),
            },
            "monthly": {
                "current": self.state.get_current_month_cost(),
                "limit": self.config.budgets.monthly,
                "percent": _pct(self.state.get_current_month_cost(), self.config.budgets.monthly),
            },
            "status": self.check_budget().status.name.lower(),
        }


def _pct(current: float, limit: float) -> float:
    if limit <= 0:
        return 0.0
    return min(current / limit * 100, 999.9)
