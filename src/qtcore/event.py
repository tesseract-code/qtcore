"""
event.py
Qt Event Loop Integration Layer.
"""
from __future__ import annotations

import asyncio
import sys
import time
import uuid
from typing import Awaitable, Callable, Optional

from PyQt6 import QtCore
from PyQt6.QtCore import QObject, QTimer, pyqtSignal, QEventLoop
from PyQt6.QtWidgets import QApplication

from cross_platform.qt6_utils.qtgui.utils import configure_high_dpi
from pycore.cpu_utils import set_high_priority
from pycore.log.instance import with_logger

# ---------------------------------------------------------------------------
# Pump tuning
# ---------------------------------------------------------------------------
# When tasks are pending the loop is polled at PUMP_ACTIVE_MS.
# When idle the rate drops to PUMP_IDLE_MS to reduce CPU load.
PUMP_ACTIVE_MS: int = 5   # ~200 Hz  — keeps UI responsive under async load
PUMP_IDLE_MS:   int = 50  # ~20 Hz   — background tick when nothing is queued


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class QtAsyncBridge(QObject):
    """
    Emits Qt signals on the main thread when an asyncio task completes or
    fails.  Must only be constructed after QApplication exists.
    """
    task_finished = pyqtSignal(str, object)    # (job_id, result)
    task_failed   = pyqtSignal(str, Exception) # (job_id, exception)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

@with_logger
class QtEventLoopManager:
    """
    Drives an asyncio event loop from a Qt application without a separate
    thread, using a QTimer to call loop._run_once() periodically.

    Lifecycle
    ---------
    1. Call initialize() once, before showing any windows.
    2. Schedule coroutines with run_coroutine() / run_coroutine_from_thread().
    3. Call shutdown() when the Qt application is about to exit (handled
       automatically by the run_qt_app / show_window helpers).

    Thread safety
    -------------
    initialize(), run_coroutine(), and shutdown() must be called from the
    main thread.  run_coroutine_from_thread() is the only method safe to
    call from a worker thread.
    """

    _instance: Optional["QtEventLoopManager"] = None

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def instance(cls) -> "QtEventLoopManager":
        if cls._instance is None:
            cls._instance = cls.__new__(cls)
            cls._instance._init_fields()
        return cls._instance

    def __init__(self) -> None:  # noqa: D107
        raise RuntimeError(
            "QtEventLoopManager is a singleton — use QtEventLoopManager.instance()."
        )

    def _init_fields(self) -> None:
        """Called once by instance() in place of __init__."""
        self.app:   Optional[QApplication] = None
        self.loop:  Optional[asyncio.AbstractEventLoop] = None
        self._timer:  Optional[QTimer] = None
        self._bridge: Optional[QtAsyncBridge] = None
        # job_id -> asyncio.Task, for cancellation and lifecycle tracking
        self._tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def initialize(self, app: Optional[QApplication] = None) -> None:
        """
        Set up the QApplication and the asyncio event loop.

        Must be called before any coroutines are scheduled and before any
        windows are shown.  Safe to call multiple times; subsequent calls
        are no-ops.

        Parameters
        ----------
        app:
            An existing QApplication instance to adopt.  If None, one is
            created (or the existing singleton is retrieved).
        """
        if self.app is not None:
            return

        # --- Qt application attributes must be set BEFORE QApplication ---
        # configure_high_dpi() must not create the QApplication internally.
        configure_high_dpi()
        QtCore.QCoreApplication.setAttribute(
            QtCore.Qt.ApplicationAttribute.AA_UseDesktopOpenGL)

        self.app = app or QApplication.instance() or QApplication(sys.argv)

        # --- QObject construction is safe now that QApplication exists ----
        self._bridge = QtAsyncBridge()

        # --- Asyncio loop -------------------------------------------------
        # WindowsProactorEventLoopPolicy must be set before creating the loop.
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # --- Pump timer ---------------------------------------------------
        # _run_once() drives one iteration of the asyncio loop without
        # blocking.  Unlike run_until_complete(), it does not start or stop
        # the loop and is not re-entrant.
        self._timer = QTimer()
        self._timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._pump)
        self._timer.start(PUMP_ACTIVE_MS)

        set_high_priority(process_name="MAIN_QAPP")
        self._logger.info("QtEventLoopManager initialized.")

    # ------------------------------------------------------------------
    # Pump
    # ------------------------------------------------------------------

    def _pump(self) -> None:
        """
        Drive the asyncio loop for exactly one iteration.

        loop._run_once() is a private but stable CPython API that runs the
        selector, fires ready callbacks, and returns immediately.  It does
        not set loop._running, so is_running() remains False and there is no
        re-entrancy risk.

        The timer interval is throttled to PUMP_IDLE_MS when no tasks are
        pending to reduce idle CPU usage.
        """
        if not self.loop:
            return
        try:
            self.loop._run_once()  # type: ignore[attr-defined]
        except Exception as exc:
            self._logger.error("Event loop pump error: %s", exc)
            return

        pending = len(asyncio.all_tasks(self.loop))
        target  = PUMP_ACTIVE_MS if pending else PUMP_IDLE_MS
        if self._timer and self._timer.interval() != target:
            self._timer.setInterval(target)

    # ------------------------------------------------------------------
    # Coroutine scheduling
    # ------------------------------------------------------------------

    def run_coroutine(self, coro: Awaitable, job_id: Optional[str] = None) -> str:
        """
        Schedule a coroutine on the main-thread asyncio loop.

        Must be called from the main thread.  For worker threads use
        run_coroutine_from_thread() instead.

        Emits bridge.task_finished(job_id, result) on success and
        bridge.task_failed(job_id, exc) on failure.

        Returns the job_id so callers can cancel via cancel_coroutine().
        """
        if not self.loop:
            raise RuntimeError("QtEventLoopManager has not been initialized.")

        job_id = job_id or uuid.uuid4().hex

        async def _wrapped() -> None:
            try:
                result = await coro
                self._bridge.task_finished.emit(job_id, result)
            except asyncio.CancelledError:
                self._logger.debug("Task %s was cancelled.", job_id)
            except Exception as exc:
                self._logger.error("Task %s failed: %s", job_id, exc)
                self._bridge.task_failed.emit(job_id, exc)
            finally:
                await self._tasks.pop(job_id, None)

        # create_task() is the correct API when already on the loop's thread.
        # run_coroutine_threadsafe() is only appropriate for cross-thread use.
        task = self.loop.create_task(_wrapped(), name=job_id)
        self._tasks[job_id] = task
        return job_id

    def run_coroutine_from_thread(
        self, coro: Awaitable, job_id: Optional[str] = None
    ) -> str:
        """
        Schedule a coroutine from a worker thread.

        Thread-safe.  Uses asyncio.run_coroutine_threadsafe() which is the
        correct cross-thread primitive.  The returned Future is discarded
        here; result/error reporting still flows through QtAsyncBridge
        signals on the main thread.
        """
        if not self.loop:
            raise RuntimeError("QtEventLoopManager has not been initialized.")

        job_id = job_id or uuid.uuid4().hex

        async def _wrapped() -> None:
            try:
                result = await coro
                self._bridge.task_finished.emit(job_id, result)
            except asyncio.CancelledError:
                self._logger.debug("Task %s was cancelled.", job_id)
            except Exception as exc:
                self._logger.error("Task %s failed: %s", job_id, exc)
                self._bridge.task_failed.emit(job_id, exc)
            finally:
                await self._tasks.pop(job_id, None)

        future = asyncio.run_coroutine_threadsafe(_wrapped(), self.loop)
        # Store a thin wrapper so cancel_coroutine() works uniformly.
        # We cannot store the Future directly because _tasks expects Tasks;
        # map the cancel call through the Future instead.
        task = self.loop.create_future()
        task.cancel = future.cancel  # type: ignore[method-assign]
        self._tasks[job_id] = task   # type: ignore[assignment]
        return job_id

    def cancel_coroutine(self, job_id: str) -> bool:
        """
        Cancel a running coroutine by job_id.

        Returns True if the task existed and cancellation was requested,
        False if the job_id was not found (already completed or never started).
        """
        task = self._tasks.get(job_id)
        if task is None:
            return False
        task.cancel()
        return True

    @property
    def bridge(self) -> QtAsyncBridge:
        """The signal bridge; connect to task_finished / task_failed here."""
        if self._bridge is None:
            raise RuntimeError("QtEventLoopManager has not been initialized.")
        return self._bridge

    # ------------------------------------------------------------------
    # Async helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def sleep_async(delay_sec: float) -> None:
        """Non-blocking sleep for use inside coroutines."""
        await asyncio.sleep(delay_sec)

    @staticmethod
    async def wait_until_async(
        condition: Callable[[], bool],
        timeout:        float = 5.0,
        check_interval: float = 0.01,
    ) -> bool:
        """
        Poll a condition asynchronously until it returns True.

        Replaces busy-wait loops.  Yields control back to the pump on every
        iteration so the Qt event loop remains responsive.

        Parameters
        ----------
        condition:
            Zero-argument callable returning bool.  Also accepts an async
            callable (coroutine function).
        timeout:
            Maximum wait time in seconds.  Raises TimeoutError on expiry.
        check_interval:
            How frequently to re-evaluate the condition, in seconds.

        Returns
        -------
        True when the condition is met.

        Raises
        ------
        TimeoutError if timeout is exceeded.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        is_coro  = asyncio.iscoroutinefunction(condition)

        while True:
            result = (await condition()) if is_coro else condition()
            if result:
                return True
            if asyncio.get_event_loop().time() >= deadline:
                raise TimeoutError(
                    f"Condition not met within {timeout}s timeout."
                )
            await asyncio.sleep(check_interval)

    # ------------------------------------------------------------------
    # Synchronous (legacy) helper
    # ------------------------------------------------------------------

    def sleep_blocking(self, timeout_ms: int) -> None:
        """
        Synchronous non-blocking sleep using a nested QEventLoop.

        Warning:
            This enters a nested Qt event loop, which can cause slots to fire
            out of order and interact badly with the asyncio pump timer.
            Use wait_until_async() / sleep_async() instead wherever possible.
            Do not call this from within a paint, resize, or move event handler.
        """
        self._logger.warning(
            "sleep_blocking() entered — nested QEventLoop active for %dms. "
            "Prefer wait_until_async() in async contexts.",
            timeout_ms,
        )
        if not self.app:
            self._logger.warning("No QApplication — falling back to time.sleep().")
            time.sleep(timeout_ms / 1000)
            return

        loop = QEventLoop()
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """
        Cancel all pending tasks, stop the pump timer, and close the loop.

        Called automatically by the run_qt_app / show_window helpers.
        Safe to call more than once.
        """
        if self._timer:
            self._timer.stop()
            self._timer = None

        if self.loop and not self.loop.is_closed():
            # Cancel every tracked task and give the loop one final pump so
            # CancelledError propagates through any awaiting coroutines.
            for task in list(self._tasks.values()):
                task.cancel()
            if self._tasks:
                try:
                    # Drain cancellations — run_until_complete is valid here
                    # because the loop is not running at this point.
                    self.loop.run_until_complete(
                        asyncio.gather(*self._tasks.values(), return_exceptions=True)
                    )
                except Exception as exc:
                    self._logger.warning("Error draining tasks on shutdown: %s", exc)

            self.loop.close()

        self._tasks.clear()
        self._logger.info("QtEventLoopManager shut down.")


# ---------------------------------------------------------------------------
# Bootstrap helpers
# ---------------------------------------------------------------------------

def run_qt_app(main_window_class: type, *args, **kwargs) -> int | None:
    """
    Initialize the manager, instantiate and show a window, run the event
    loop, then shut down cleanly.

    Returns the exit code from QApplication.exec().
    """
    manager = QtEventLoopManager.instance()
    manager.initialize()

    window = main_window_class(*args, **kwargs)
    window.show()

    try:
        return manager.app.exec()
    finally:
        manager.shutdown()


def show_window(window: object) -> int | None:
    """
    Show an already-constructed window and run the Qt event loop.

    Returns the exit code from QApplication.exec().
    """
    manager = QtEventLoopManager.instance()
    manager.initialize()

    window.show()

    try:
        return manager.app.exec()
    finally:
        manager.shutdown()