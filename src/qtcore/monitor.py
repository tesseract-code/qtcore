import time
from collections import deque
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class PerfStats:
    """
    Immutable snapshot of performance metrics for a processing pipeline.

    All timing values are in milliseconds; throughput is in items per second;
    utilisation_pct is a 0–100 percentage. Produced by PerformanceMonitor.update
    after each processed item.
    """

    total_items: int  # Total number of items processed so far
    rate: float  # Items per second (throughput)
    avg_processing_ms: float  # Average processing time per item (ms)
    utilisation_pct: float  # Fraction of each cycle spent processing (0–100)
    cycle_time_ms: float  # Total time per item based on current rate (ms)
    actual_interval_ms: float  # Average wall‑clock time between consecutive completions (ms)
    idle_time_ms: float  # Slack per cycle = cycle_time - processing_time (>=0)


class PerformanceMonitor:
    """
    Rolling‑window performance monitor for any discrete processing pipeline.

    Tracks throughput (items/second), per‑item processing cost, and utilisation
    over a sliding window of the most recent `window_size` items. Call
    `update()` once after finishing each item, passing the time spent on that
    item; it returns a fresh `PerfStats` snapshot.

    Larger window sizes smooth out spikes; smaller values react faster to
    changes. Default window size is 30 items.
    """

    def __init__(self, window_size: int = 30):
        self._window = window_size
        # Monotonic completion timestamps (seconds) for rate calculation.
        self._timestamps: deque[float] = deque(maxlen=window_size)
        # Per‑item processing durations (ms).
        self._proc_times: deque[float] = deque(maxlen=window_size)
        # Wall‑clock timestamp of the most recently processed item.
        self.last_timestamp: float | None = None
        # Cumulative item counter; never resets.
        self.item_count: int = 0

    def update(self, processing_time_ms: float) -> PerfStats:
        """
        Record a completed item and return updated performance statistics.

        Should be called immediately after finishing work on each item.
        The first call returns zero rate/interval metrics because at least
        two timestamps are required to compute a rate.
        """
        self.item_count += 1
        now = time.perf_counter()

        self._timestamps.append(now)
        self._proc_times.append(processing_time_ms)
        self.last_timestamp = now

        # Rate is derived from the span between the oldest and newest timestamp
        # in the window, so it adapts automatically as the window fills.
        if len(self._timestamps) < 2:
            # Not enough data yet — return safe zero values.
            rate = 0.0
            avg_proc = processing_time_ms
            avg_interval = 0.0
        else:
            duration = self._timestamps[-1] - self._timestamps[0]  # seconds
            count = len(self._timestamps) - 1  # intervals
            rate = count / duration if duration > 0 else 0.0
            avg_proc = sum(self._proc_times) / len(self._proc_times)
            avg_interval = (duration * 1000.0) / count if count > 0 else 0.0

        # Utilisation is the fraction of each cycle slot consumed by processing.
        # idle_time is the remaining slack; both are derived from the same rate.
        if rate > 0:
            avg_cycle_time = 1000.0 / rate  # ms available per item
            utilisation = (avg_proc / avg_cycle_time) * 100.0
            idle_time = avg_cycle_time - avg_proc
        else:
            avg_cycle_time = 0.0
            utilisation = 0.0
            idle_time = 0.0

        return PerfStats(
            total_items=self.item_count,
            rate=rate,
            avg_processing_ms=avg_proc,
            utilisation_pct=min(utilisation, 100.0),
            cycle_time_ms=avg_cycle_time,
            actual_interval_ms=avg_interval,
            idle_time_ms=max(idle_time, 0.0),
        )


class HealthMonitor:
    """
    Failure-count and staleness-based health tracker for an external source.

    Health is declared bad when either of two independent conditions is met:

      - Failure count: record_failure() has been called max_failures or more
        times without an intervening record_success().
      - Staleness: no success has been recorded within
        check_interval * max_failures seconds, which catches sources that
        silently stop sending data and therefore never trigger record_failure().

    Typical usage:
        monitor = HealthMonitor()
        try:
            result = do_operation()
            monitor.record_success()
        except Exception:
            monitor.record_failure()

        if monitor.should_reconnect():
            reconnect()
    """

    def __init__(self,
                 check_interval: float = 2.0,
                 timeout: float = 1.0,
                 max_failures: int = 3):
        self.check_interval = check_interval
        self.timeout = timeout  # Exposed for callers to read.
        self.last_success = time.time()  # Initialised so the first window is fair.
        self.consecutive_failures = 0
        self.max_failures = max_failures

    def record_success(self):
        """Record a successful operation, resetting the failure counter."""
        self.last_success = time.time()
        self.consecutive_failures = 0

    def record_failure(self):
        """Record a failed operation, incrementing the consecutive failure count."""
        self.consecutive_failures += 1

    def is_healthy(self) -> bool:
        """
        Return True if the source appears healthy.

        Healthy means both fewer than max_failures consecutive failures, and
        a success recorded within check_interval * max_failures seconds.
        """
        # A source is stale if it has been silent for longer than we would
        # expect given the normal check cadence.
        stale = (time.time() - self.last_success) > (
                    self.check_interval * self.max_failures)
        return not stale and self.consecutive_failures < self.max_failures

    def should_reconnect(self) -> bool:
        """Return True when a reconnection attempt is warranted."""
        # Delegating to is_healthy ensures the two methods stay consistent.
        return not self.is_healthy()

    def reset(self):
        """Reset all health state, as if the monitor were freshly constructed."""
        self.consecutive_failures = 0
        self.last_success = time.time()
