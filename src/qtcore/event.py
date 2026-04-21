"""
event.py
Qt Event Loop Integration Layer.
"""
from __future__ import annotations

import asyncio
import sys
import time
from typing import Optional, Awaitable, Callable

from PyQt6 import QtCore
from PyQt6.QtCore import QObject, QTimer, pyqtSignal, QEventLoop
from PyQt6.QtWidgets import QApplication

from pycore.cpu import set_high_priority
from pycore.log.ctx import with_logger
from qtcore.utils import configure_high_dpi


class QtAsyncBridge(QObject):
    """Emits Qt signals for asyncio task completion on the main thread."""
    task_finished = pyqtSignal(str, object)
    task_failed = pyqtSignal(str, Exception)


@with_logger
class QtEventLoopManager:
    """
    Integrates asyncio with Qt.
    Uses a QTimer to 'pump' the asyncio loop periodically.
    """
    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.app: Optional[QApplication] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._timer: Optional[QTimer] = None
        self._bridge = QtAsyncBridge()
        # self.pool_manager = ... (Assumed external dependency)

    def initialize(self, app: Optional[QApplication] = None):
        """Initialize the Qt Application and the Asyncio Loop."""
        if self.app is not None:
            return
        configure_high_dpi()
        QtCore.QCoreApplication.setAttribute(
            QtCore.Qt.ApplicationAttribute.AA_UseDesktopOpenGL)

        set_high_priority(process_name="MAIN_QAPP")

        self.app = app or QApplication.instance() or QApplication(sys.argv)

        # 1. Setup Asyncio Loop Policy (Windows fix)
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(
                asyncio.WindowsProactorEventLoopPolicy())

        # 2. Create the Loop
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # 3. Inject loop (assuming pool_manager exists)
        # self.pool_manager.set_event_loop(self.loop)

        # 4. Setup Pump Timer
        # 5ms is aggressive but good for UI responsiveness
        self._timer = QTimer()
        self._timer.timeout.connect(self._process_asyncio_events)
        self._timer.start(5)

        self._logger.info("QtEventLoopManager initialized.")

    def _process_asyncio_events(self):
        """
        Manually pump the asyncio loop.
        """
        if not self.loop: return
        try:
            # Step the loop forward slightly.
            # This allows scheduled async tasks (like wait_until_async) to tick.
            self.loop.run_until_complete(asyncio.sleep(0))
        except Exception as e:
            self._logger.error(f"Event loop tick error: {e}")

    async def sleep_async(self, delay_sec: float):
        """
        Non-blocking sleep for use inside coroutines.
        The UI remains responsive because _process_asyncio_events yields control.
        """
        await asyncio.sleep(delay_sec)

    async def wait_until_async(self, condition: Callable[[], bool],
                               timeout: float = 5.0,
                               check_interval: float = 0.01):
        """
        [Performant & Advisable]
        Waits asynchronously for a condition to become True.
        Replaces 'wait_for_condition' busy-loops.

        :param condition: A lambda/function returning bool
        :param timeout: Max wait time in seconds
        :param check_interval: How often to check (seconds)
        """
        start_time = time.time()
        while not condition():
            if time.time() - start_time > timeout:
                raise TimeoutError("Condition not met within timeout")

            # This yields control back to the Qt Loop via _process_asyncio_events
            await asyncio.sleep(check_interval)
        return True

    def sleep_blocking(self, timeout_ms: int):
        """
        [Synchronous Legacy Support]
        Qt-native non-blocking sleep using a local QEventLoop.
        Use this ONLY if you cannot use async/await (e.g., inside a standard Qt slot).
        """
        if not self.app:
            self._logger.warning("App not initialized, doing standard sleep")
            time.sleep(timeout_ms / 1000)
            return

        loop = QEventLoop()
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()

    def run_coroutine(self, coro: Awaitable, job_id: str = None) -> str:
        """
        Run a coroutine on the MAIN thread's asyncio loop.
        """
        if not job_id:
            job_id = f"task_{time.time()}"

        async def wrapped():
            try:
                res = await coro
                self._bridge.task_finished.emit(job_id, res)
            except Exception as e:
                self._logger.error(f"Task {job_id} failed: {e}")
                self._bridge.task_failed.emit(job_id, e)

        # Because we are pumping the loop manually in the Main Thread,
        # run_coroutine_threadsafe allows us to inject tasks into that loop
        # safely from anywhere (including the main thread itself).
        asyncio.run_coroutine_threadsafe(wrapped(), self.loop)
        return job_id

    def shutdown(self):
        if self._timer:
            self._timer.stop()
        if self.loop and self.loop.is_running():
            self.loop.stop()
            self.loop.close()


# --- Helpers ---

def run_qt_app(main_window_class, *args, **kwargs):
    """Bootstrap helper."""
    manager = QtEventLoopManager.instance()
    manager.initialize()

    window = main_window_class(*args, **kwargs)
    window.show()

    return manager.app.exec()


def show_window(window):
    """Bootstrap helper."""
    manager = QtEventLoopManager.instance()
    manager.initialize()

    window.show()

    return manager.app.exec()
