"""Anomaly detection for claude-guard — spike alerts."""

import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .config import AnomalySettings


@dataclass
class AnomalyResult:
    is_anomaly: bool = False
    current_rate: float = 0.0    # $/min current
    average_rate: float = 0.0    # $/min rolling average
    multiplier: float = 0.0      # current / average
    message: str = ""


@dataclass
class CostSample:
    timestamp: float  # unix epoch
    amount: float


class AnomalyDetector:
    """Detects spending rate spikes using rolling window comparison."""

    def __init__(self, settings: Optional[AnomalySettings] = None):
        self.settings = settings or AnomalySettings()
        self._samples: list[CostSample] = []
        self._alert_cooldown: float = 0.0  # don't spam alerts

    @property
    def window_seconds(self) -> float:
        return self.settings.lookback_window_minutes * 60

    def record(self, amount: float, timestamp: Optional[float] = None) -> AnomalyResult:
        """Record a cost and check for anomaly. Returns result."""
        now = timestamp or time.time()
        self._samples.append(CostSample(timestamp=now, amount=amount))
        self._prune(now)
        return self._check(now)

    def _prune(self, now: float) -> None:
        """Remove samples outside the lookback window."""
        cutoff = now - self.window_seconds
        self._samples = [s for s in self._samples if s.timestamp >= cutoff]

    def _check(self, now: float) -> AnomalyResult:
        """Check if current spending rate is anomalous."""
        if len(self._samples) < 2:
            return AnomalyResult()

        # Split window: recent minute vs rest of window
        one_min_ago = now - 60
        recent = [s for s in self._samples if s.timestamp >= one_min_ago]
        older = [s for s in self._samples if s.timestamp < one_min_ago]

        recent_total = sum(s.amount for s in recent)

        if not older:
            # Not enough history — can't compare
            return AnomalyResult(current_rate=recent_total)

        # Calculate average rate over older window ($/min)
        older_total = sum(s.amount for s in older)
        oldest_ts = min(s.timestamp for s in older)
        older_duration_min = max((one_min_ago - oldest_ts) / 60, 0.1)  # avoid div/0
        avg_rate = older_total / older_duration_min

        current_rate = recent_total  # already per-minute (last 60s)

        if avg_rate <= 0.001:
            # Average is near zero — any spend looks like infinity multiplier
            # Only alert if current rate is meaningful
            if current_rate > 0.10:  # more than 10 cents/min from near-zero
                return AnomalyResult(
                    is_anomaly=True,
                    current_rate=current_rate,
                    average_rate=avg_rate,
                    multiplier=999.0,
                    message=f"Spending spike: ${current_rate:.2f}/min (was near zero)",
                )
            return AnomalyResult(current_rate=current_rate, average_rate=avg_rate)

        multiplier = current_rate / avg_rate

        if multiplier >= self.settings.spike_multiplier:
            # Check cooldown — don't alert more than once per minute
            if now - self._alert_cooldown < 60:
                return AnomalyResult(
                    current_rate=current_rate,
                    average_rate=avg_rate,
                    multiplier=multiplier,
                )

            self._alert_cooldown = now
            return AnomalyResult(
                is_anomaly=True,
                current_rate=current_rate,
                average_rate=avg_rate,
                multiplier=multiplier,
                message=f"Spending spike: ${current_rate:.2f}/min vs avg ${avg_rate:.2f}/min ({multiplier:.1f}x)",
            )

        return AnomalyResult(
            current_rate=current_rate,
            average_rate=avg_rate,
            multiplier=multiplier,
        )

    def get_rate_summary(self) -> dict:
        """Get current rate info for status display."""
        now = time.time()
        self._prune(now)

        if not self._samples:
            return {"current_rate": 0.0, "average_rate": 0.0, "status": "idle"}

        one_min_ago = now - 60
        recent = [s for s in self._samples if s.timestamp >= one_min_ago]
        current_rate = sum(s.amount for s in recent)

        total = sum(s.amount for s in self._samples)
        oldest_ts = min(s.timestamp for s in self._samples)
        duration_min = max((now - oldest_ts) / 60, 0.1)
        avg_rate = total / duration_min

        return {
            "current_rate": round(current_rate, 4),
            "average_rate": round(avg_rate, 4),
            "samples": len(self._samples),
            "status": "normal",
        }

    def alert(self, result: AnomalyResult) -> None:
        """Fire alert actions for an anomaly."""
        if not result.is_anomaly:
            return

        # Terminal bell
        sys.stderr.write(f"\a\n⚠ CLAUDE-GUARD ANOMALY: {result.message}\n")
        sys.stderr.flush()
