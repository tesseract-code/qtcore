import time
from collections import deque
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class PerfStats:
    """
    Immutable snapshot of performance metrics for a single monitor window.

    All timing values are in milliseconds; fps is in frames per second;
    cpu_usage_pct is a 0–100 percentage. Produced by
    PerformanceMonitor.update on every frame.
    """

    frame_count: int
    fps: float
    avg_processing_ms: float
    cpu_usage_pct: float
    frame_time_ms: float
    # Time between consecutive frame arrivals (wall-clock cadence).
    actual_frame_interval_ms: float
    # Slack time per frame: frame_time - processing_time, floored at 0.
    idle_time_ms: float


class PerformanceMonitor:
    """
    Rolling-window performance monitor for a frame-based processing pipeline.

    Tracks throughput (FPS), per-frame processing cost, and CPU utilisation
    over a sliding window of the most recent window_size frames. Call
    update() once per processed frame, passing the time spent on that
    frame; it returns a fresh PerfStats snapshot.

    Larger window sizes smooth out spikes; smaller values react faster to
    changes. Defaults to 30 frames.
    """

    def __init__(self, window_size: int = 30):
        self._window = window_size
        # Monotonic arrival timestamps (seconds) for FPS calculation.
        self._timestamps: deque[float] = deque(maxlen=window_size)
        # Per-frame processing durations (ms) for utilisation calculation.
        self._proc_times: deque[float] = deque(maxlen=window_size)
        # Wall-clock timestamp of the most recently processed frame.
        self.last_frame_timestamp: float | None = None
        # Cumulative frame counter; never resets.
        self.frame_count: int = 0

    def update(self, processing_time_ms: float) -> PerfStats:
        """
        Record a completed frame and return updated performance statistics.

        Should be called immediately after finishing work on each frame.
        The first call returns zeroed FPS/interval metrics because at least
        two timestamps are required to compute a rate.
        """
        self.frame_count += 1
        now = time.perf_counter()

        self._timestamps.append(now)
        self._proc_times.append(processing_time_ms)
        self.last_frame_timestamp = now

        # --- Throughput -------------------------------------------------------
        # FPS is derived from the span between the oldest and newest timestamp
        # in the window rather than from a fixed wall-clock period, so it
        # adapts automatically as the window fills and rotates.
        if len(self._timestamps) < 2:
            # Not enough data yet — return safe zero values.
            fps = 0.0
            avg_proc = processing_time_ms
            avg_interval = 0.0
        else:
            duration = self._timestamps[-1] - self._timestamps[0]  # seconds
            count = len(self._timestamps) - 1                       # intervals
            fps = count / duration if duration > 0 else 0.0
            avg_proc = sum(self._proc_times) / len(self._proc_times)
            # Convert the per-interval duration to milliseconds.
            avg_interval = (duration * 1000.0) / count if count > 0 else 0.0

        # --- Utilisation ------------------------------------------------------
        # CPU usage is the fraction of each frame slot consumed by processing.
        # idle_time is the remaining slack; both are derived from the same fps
        # figure so they are always consistent with each other.
        if fps > 0:
            avg_total_frame_time = 1000.0 / fps   # ms available per frame
            usage_pct = (avg_proc / avg_total_frame_time) * 100.0
            idle_time = avg_total_frame_time - avg_proc
        else:
            avg_total_frame_time = 0.0
            usage_pct = 0.0
            idle_time = 0.0

        return PerfStats(
            frame_count=self.frame_count,
            fps=fps,
            avg_processing_ms=avg_proc,
            # Cap at 100 % — overrun is already visible via idle_time = 0.
            cpu_usage_pct=min(usage_pct, 100.0),
            frame_time_ms=avg_total_frame_time,
            actual_frame_interval_ms=avg_interval,
            # Floor at 0 to stay consistent with the cpu_usage_pct cap above;
            # a negative idle time would imply > 100 % usage, which is already
            # represented by the cap.
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
        self.timeout = timeout           # Exposed for callers to read.
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
        stale = (time.time() - self.last_success) > (self.check_interval * self.max_failures)
        return not stale and self.consecutive_failures < self.max_failures

    def should_reconnect(self) -> bool:
        """Return True when a reconnection attempt is warranted."""
        # Delegating to is_healthy ensures the two methods stay consistent.
        return not self.is_healthy()

    def reset(self):
        """Reset all health state, as if the monitor were freshly constructed."""
        self.consecutive_failures = 0
        self.last_success = time.time()
