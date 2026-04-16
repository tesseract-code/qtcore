"""
threadpool.py
Integrated Qt Async Thread Pool Management System.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import Future
from typing import Any, Callable, Dict, List, Optional, Union, Awaitable

from PyQt6.QtCore import QObject, QThreadPool, QRunnable, QThread

from pycore.event import EventTransport, CallbackTransport, EventPayload
from pycore.jobs import JobStatus, ExecutionMode, JobMetrics
from qtcore.meta import QSingletonMeta
from qtcore.worker import AsyncWorker, SyncWorker

logger = logging.getLogger(__name__)


class ThreadPoolManager(QObject, metaclass=QSingletonMeta):
    """
    Thread pool manager that executes both sync (QRunnable) and async (asyncio) jobs.
    Provides job tracking, cancellation, metrics, and event notifications.
    """

    _instance_lock = threading.RLock()

    def __init__(self, max_thread_count: int = 8) -> None:
        """Initialize the thread pool manager."""
        # The metaclass ensures __init__ is called only once per singleton.
        # We guard against re-initialization with a flag.
        if hasattr(self, "_initialized") and self._initialized:
            return

        super().__init__()
        self._sync_pool = QThreadPool.globalInstance()
        self._sync_pool.setMaxThreadCount(max_thread_count)
        # Removed conflicting priority/QoS settings (use defaults for stability)

        self._async_workers: List[AsyncWorker] = []
        self._jobs: Dict[
            str, tuple[Union[SyncWorker, AsyncWorker], JobMetrics]] = {}
        self._jobs_lock = threading.RLock()  # Protects _jobs dict
        self._async_workers_lock = threading.RLock()  # Protects _async_workers list
        self._event_handlers: Dict[str, List[EventTransport]] = defaultdict(
            list)
        self._external_loop: Optional[asyncio.AbstractEventLoop] = None

        self._initialized = True

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Explicitly set the loop used for event dispatching."""
        self._external_loop = loop

    def submit(self, func: Callable, *args, job_id: str = None,
               **kwargs) -> str:
        """
        Submit a synchronous (blocking) function to the thread pool.

        Args:
            func: Callable to execute.
            *args: Positional arguments for func.
            job_id: Optional custom ID; auto-generated if None.
            **kwargs: Keyword arguments for func.

        Returns:
            Unique job ID.
        """
        job_id = job_id or f"sync_{uuid.uuid4()}"
        worker = SyncWorker(job_id, func, *args, **kwargs)
        metrics = JobMetrics(job_id, execution_mode=ExecutionMode.SYNC)

        self._connect_signals(worker)

        with self._jobs_lock:
            self._jobs[job_id] = (worker, metrics)

        self._emit_event("job.submitted", job_id, {"mode": "sync"})
        self._sync_pool.start(worker)
        return job_id

    def submit_async(self, coro: Awaitable, job_id: str = None) -> tuple[
        str, Future]:
        """
        Submit an asynchronous coroutine for execution.

        Args:
            coro: Awaitable coroutine object.
            job_id: Optional custom ID; auto-generated if None.

        Returns:
            Tuple of (job_id, concurrent.futures.Future) that will complete when the coroutine finishes.
        """
        job_id = job_id or f"async_{time.time()}"
        future = Future()
        worker = AsyncWorker(job_id, future, coro)
        metrics = JobMetrics(job_id, execution_mode=ExecutionMode.ASYNC)

        self._connect_signals(worker)

        with self._jobs_lock:
            self._jobs[job_id] = (worker, metrics)

        with self._async_workers_lock:
            self._async_workers.append(worker)

        self._emit_event("job.submitted", job_id, {"mode": "async"})
        worker.start()
        worker.finished.connect(lambda: self._cleanup_async_worker(worker))
        return job_id, future

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a job by its ID. Returns True if the job existed and was cancelled."""
        with self._jobs_lock:
            if job_id not in self._jobs:
                return False
            worker, metrics = self._jobs[job_id]

        self._update_metric(job_id, status=JobStatus.CANCELLED)
        worker.cancel()
        return True

    def get_job_status(self, job_id: str) -> Optional[JobStatus]:
        """Return the current status of a job, or None if the job does not exist."""
        with self._jobs_lock:
            entry = self._jobs.get(job_id)
            return entry[1].status if entry else None

    def get_job_metrics(self, job_id: str) -> Optional[JobMetrics]:
        """Return a copy of the job's metrics, or None if the job does not exist."""
        with self._jobs_lock:
            entry = self._jobs.get(job_id)
            return entry[1] if entry else None

    def register_observer(self, event_type: str, callback: Callable) -> None:
        """Register a callback to receive events of a specific type."""
        self._event_handlers[event_type].append(CallbackTransport(callback))

    # --- Internal Helpers ---

    def _cleanup_async_worker(self, worker: AsyncWorker) -> None:
        """Remove a finished async worker from the tracking list."""
        with self._async_workers_lock:
            if worker in self._async_workers:
                self._async_workers.remove(worker)

    def _cleanup_job(self, job_id: str) -> None:
        """Remove a completed/cancelled/failed job from the jobs dictionary."""
        with self._jobs_lock:
            self._jobs.pop(job_id, None)

    def _connect_signals(self, worker: QRunnable | QThread) -> None:
        """
        Connect worker signals to manager slots.
        Assumes the worker has a `signals` attribute with Qt signals.
        """
        worker.signals.started.connect(self._on_started)
        worker.signals.finished.connect(self._on_finished)
        worker.signals.error.connect(self._on_error)
        worker.signals.status_changed.connect(self._on_status_changed)

    # --- Slots ---

    def _on_started(self, job_id: str) -> None:
        self._update_metric(job_id, start_time=time.time())

    def _on_status_changed(self, job_id: str, status: JobStatus) -> None:
        self._update_metric(job_id, status=status)

    def _on_finished(self, job_id: str, result: Any) -> None:
        self._update_metric(job_id, result=result, end_time=time.time(),
                            status=JobStatus.COMPLETED)
        self._emit_event("job.completed", job_id, {"result": result})
        self._cleanup_job(job_id)

    def _on_error(self, job_id: str, error: Exception) -> None:
        self._update_metric(job_id, error=error, end_time=time.time(),
                            status=JobStatus.FAILED)
        self._emit_event("job.failed", job_id, {"error": str(error)})
        self._cleanup_job(job_id)

    def _update_metric(self, job_id: str, **kwargs) -> None:
        """Update job metrics with the given keyword arguments."""
        with self._jobs_lock:
            entry = self._jobs.get(job_id)
            if not entry:
                return
            _, metrics = entry
            for k, v in kwargs.items():
                setattr(metrics, k, v)
            if "status" in kwargs:
                self._emit_event("job.status_changed", job_id,
                                 {"status": kwargs["status"]})

    def _get_target_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """Resolve the best event loop to schedule events on."""
        if self._external_loop and not self._external_loop.is_closed():
            return self._external_loop
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    def _emit_event(self, event_type: str, job_id: str, data: Any) -> None:
        """
        Dispatch an event payload to all registered observers.
        Uses the target loop for async transports, falls back to sync callbacks.
        """
        payload = EventPayload(event_type, job_id, data)
        target_loop = self._get_target_loop()

        for transport in self._event_handlers[event_type]:
            coro = transport.send(payload)

            if target_loop and not target_loop.is_closed():
                try:
                    asyncio.run_coroutine_threadsafe(coro, target_loop)
                except Exception as e:
                    logger.error(
                        f"Failed to schedule event {event_type} for job {job_id}: {e}")
                    # Best effort: close coroutine to avoid warnings
                    coro.close()
            else:
                # No available asyncio loop – fallback to synchronous execution if possible
                coro.close()
                if isinstance(transport, CallbackTransport):
                    if not inspect.iscoroutinefunction(transport.callback):
                        try:
                            transport.callback(payload)
                        except Exception as e:
                            logger.error(
                                f"Sync fallback failed for event {event_type}: {e}")
                else:
                    logger.warning(
                        f"Event {event_type} dropped: no event loop and transport {type(transport)} "
                        "does not support sync fallback"
                    )


def get_pool_manager() -> ThreadPoolManager:
    """Return the global singleton instance of ThreadPoolManager."""
    return ThreadPoolManager()
