from __future__ import annotations

import asyncio
from concurrent.futures import Future
from typing import Callable, Awaitable

from PyQt6.QtCore import (
    QObject, QRunnable, QThread, pyqtSignal,
    QMutex, QMutexLocker
)

from pycore.jobs import JobStatus


class WorkerSignals(QObject):
    started = pyqtSignal(str)
    finished = pyqtSignal(str, object)
    error = pyqtSignal(str, Exception)
    progress = pyqtSignal(str, float)
    status_changed = pyqtSignal(str, object)
    cancelled = pyqtSignal(str)


class SyncWorker(QRunnable):
    """Executes a synchronous function in QThreadPool."""

    def __init__(self, job_id: str, target: Callable, *args, **kwargs):
        super().__init__()
        self.job_id = job_id
        self.target = target
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self.setAutoDelete(True)
        self._mutex = QMutex()
        self._is_cancelled = False

    def run(self):
        try:
            with QMutexLocker(self._mutex):
                if self._is_cancelled: return

            self.signals.started.emit(self.job_id)
            self.signals.status_changed.emit(self.job_id, JobStatus.RUNNING)

            result = self.target(*self.args, **self.kwargs)

            with QMutexLocker(self._mutex):
                if not self._is_cancelled:
                    self.signals.finished.emit(self.job_id, result)
                    self.signals.status_changed.emit(self.job_id,
                                                     JobStatus.COMPLETED)
        except Exception as e:
            self.signals.error.emit(self.job_id, e)
            self.signals.status_changed.emit(self.job_id, JobStatus.FAILED)

    def cancel(self):
        with QMutexLocker(self._mutex):
            self._is_cancelled = True
            self.signals.cancelled.emit(self.job_id)
            self.signals.status_changed.emit(self.job_id, JobStatus.CANCELLED)


class AsyncWorker(QThread):
    """Runs an asyncio coroutine in a dedicated QThread."""

    def __init__(self, job_id: str, future: Future, coro: Awaitable):
        super().__init__()
        self.job_id = job_id
        self.future = future
        self.coro = coro
        self.signals = WorkerSignals()
        self._loop = None
        self._task = None
        self._is_cancelled = False

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self.signals.started.emit(self.job_id)
            self.signals.status_changed.emit(self.job_id, JobStatus.RUNNING)

            self._task = self._loop.create_task(self.coro)
            result = self._loop.run_until_complete(self._task)

            if not self._is_cancelled:
                self.future.set_result(result)
                self.signals.finished.emit(self.job_id, result)
                self.signals.status_changed.emit(self.job_id,
                                                 JobStatus.COMPLETED)

        except asyncio.CancelledError:
            self.signals.cancelled.emit(self.job_id)
            self.signals.status_changed.emit(self.job_id, JobStatus.CANCELLED)
        except Exception as e:
            if not self.future.done():
                self.future.set_exception(e)
            self.signals.error.emit(self.job_id, e)
            self.signals.status_changed.emit(self.job_id, JobStatus.FAILED)
        finally:
            try:
                tasks = asyncio.all_tasks(self._loop)
                for t in tasks: t.cancel()
                if tasks:
                    self._loop.run_until_complete(
                        asyncio.gather(*tasks, return_exceptions=True))
                self._loop.close()
            except Exception:
                pass

    def cancel(self):
        self._is_cancelled = True
        if self._loop and self._loop.is_running() and self._task:
            self._loop.call_soon_threadsafe(self._task.cancel)
        self.quit()
        self.wait()
