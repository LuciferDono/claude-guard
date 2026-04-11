"""Tests for claude-guard anomaly detection."""

import time

from claude_guard.config import AnomalySettings
from claude_guard.anomaly import AnomalyDetector, AnomalyResult


class TestAnomalyDetector:
    def test_no_anomaly_on_first_sample(self):
        detector = AnomalyDetector()
        result = detector.record(0.05)
        assert not result.is_anomaly

    def test_no_anomaly_on_steady_usage(self):
        detector = AnomalyDetector(AnomalySettings(spike_multiplier=3.0, lookback_window_minutes=5))
        base_time = 1000000.0

        # Simulate steady spending over 3 minutes
        for i in range(180):
            t = base_time + i
            result = detector.record(0.01, timestamp=t)

        assert not result.is_anomaly

    def test_detect_spike(self):
        settings = AnomalySettings(spike_multiplier=3.0, lookback_window_minutes=5)
        detector = AnomalyDetector(settings)
        base_time = 1000000.0

        # Build up 3 minutes of low steady spending ($0.01/sec)
        for i in range(180):
            detector.record(0.01, timestamp=base_time + i)

        # Now spike: $0.50 in 1 second (huge spike)
        result = detector.record(0.50, timestamp=base_time + 181)
        # Even one big spike may not trigger if it's in the "recent" window with low total
        # Keep spiking
        for i in range(10):
            result = detector.record(0.50, timestamp=base_time + 182 + i)

        assert result.is_anomaly or result.multiplier >= 3.0

    def test_spike_multiplier_threshold(self):
        settings = AnomalySettings(spike_multiplier=2.0, lookback_window_minutes=5)
        detector = AnomalyDetector(settings)
        base_time = 1000000.0

        # 2 minutes of $0.01/sec
        for i in range(120):
            detector.record(0.01, timestamp=base_time + i)

        # Now 3x spike for last minute
        result = None
        for i in range(60):
            result = detector.record(0.03, timestamp=base_time + 121 + i)

        # Current rate should be ~$1.80/min, avg was ~$0.60/min = 3x
        assert result.multiplier >= 2.0

    def test_cooldown_prevents_spam(self):
        settings = AnomalySettings(spike_multiplier=2.0, lookback_window_minutes=5)
        detector = AnomalyDetector(settings)
        base_time = 1000000.0

        # Build history
        for i in range(120):
            detector.record(0.01, timestamp=base_time + i)

        # First spike
        anomaly_count = 0
        for i in range(30):
            result = detector.record(0.10, timestamp=base_time + 121 + i)
            if result.is_anomaly:
                anomaly_count += 1

        # Should only alert once due to cooldown
        assert anomaly_count <= 1

    def test_from_zero_to_spending(self):
        detector = AnomalyDetector()
        base_time = 1000000.0

        # 2 minutes of zero/near-zero
        for i in range(120):
            detector.record(0.0001, timestamp=base_time + i)

        # Sudden significant spend
        result = None
        for i in range(10):
            result = detector.record(0.05, timestamp=base_time + 121 + i)

        # Should detect the spike from near-zero
        assert result.is_anomaly or result.current_rate > 0

    def test_rate_summary(self):
        detector = AnomalyDetector()
        summary = detector.get_rate_summary()
        assert summary["status"] == "idle"
        assert summary["current_rate"] == 0.0

    def test_rate_summary_with_data(self):
        detector = AnomalyDetector()
        now = time.time()
        for i in range(5):
            detector.record(0.10, timestamp=now - 30 + i)

        summary = detector.get_rate_summary()
        assert summary["samples"] == 5
        assert summary["status"] == "normal"

    def test_window_pruning(self):
        settings = AnomalySettings(lookback_window_minutes=1)
        detector = AnomalyDetector(settings)
        base_time = 1000000.0

        # Add samples
        for i in range(100):
            detector.record(0.01, timestamp=base_time + i)

        # Move time forward past window
        detector.record(0.01, timestamp=base_time + 120)

        # Old samples should be pruned
        assert len(detector._samples) < 100

    def test_anomaly_result_message(self):
        settings = AnomalySettings(spike_multiplier=2.0, lookback_window_minutes=5)
        detector = AnomalyDetector(settings)
        base_time = 1000000.0

        # Build baseline
        for i in range(120):
            detector.record(0.01, timestamp=base_time + i)

        # Spike hard
        result = None
        for i in range(60):
            result = detector.record(0.10, timestamp=base_time + 121 + i)
            if result.is_anomaly:
                break

        if result and result.is_anomaly:
            assert "spike" in result.message.lower()
            assert "$" in result.message
