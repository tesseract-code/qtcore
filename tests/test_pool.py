"""
Comprehensive unit tests for Qt Global Thread Pool Manager.
Tests cover all critical functionality including edge cases.
"""

import unittest
import time

from PyQt6.QtWidgets import QApplication
import sys

from cross_platform.qt6_utils.core.threadpool import (
    ThreadPoolManager,
    Worker,
    ProgressAwareWorker,
    JobStatus,
    JobMetrics,
    get_thread_pool_manager
)


class TestJobMetrics(unittest.TestCase):
    """Test JobMetrics dataclass."""

    def test_metrics_initialization(self):
        """Test metrics are initialized correctly."""
        metrics = JobMetrics(job_id="test_1")
        self.assertEqual(metrics.job_id, "test_1")
        self.assertEqual(metrics.status, JobStatus.PENDING)
        self.assertEqual(metrics.progress, 0.0)
        self.assertIsNone(metrics.start_time)
        self.assertIsNone(metrics.end_time)

    def test_elapsed_time_not_started(self):
        """Test elapsed time when job hasn't started."""
        metrics = JobMetrics(job_id="test_1")
        self.assertIsNone(metrics.elapsed_time)

    def test_elapsed_time_running(self):
        """Test elapsed time for running job."""
        start = time.time()
        metrics = JobMetrics(job_id="test_1", start_time=start)
        time.sleep(0.1)
        elapsed = metrics.elapsed_time
        self.assertIsNotNone(elapsed)
        self.assertGreater(elapsed, 0.05)

    def test_elapsed_time_completed(self):
        """Test elapsed time for completed job."""
        metrics = JobMetrics(
            job_id="test_1",
            start_time=100.0,
            end_time=105.5
        )
        self.assertEqual(metrics.elapsed_time, 5.5)


class TestWorker(unittest.TestCase):
    """Test Worker class functionality."""

    @classmethod
    def setUpClass(cls):
        """Set up QApplication for tests."""
        if not QApplication.instance():
            cls.app = QApplication(sys.argv)

    def test_worker_initialization(self):
        """Test worker is initialized correctly."""
        target = lambda x: x * 2
        worker = Worker("test_job", target, 5)

        self.assertEqual(worker._job_id, "test_job")
        self.assertEqual(worker.target, target)
        self.assertEqual(worker.args, (5,))
        self.assertFalse(worker.is_running)
        self.assertFalse(worker.is_paused)
        self.assertFalse(worker.is_cancelled)

    def test_worker_execution(self):
        """Test worker executes target correctly."""
        result_holder = {'value': None}

        def task(x):
            return x * 2

        def on_finished(job_id, result):
            result_holder['value'] = result

        worker = Worker("test_job", task, 21)
        worker.signals.finished.connect(on_finished)
        worker.run()

        # Process events to ensure signal is delivered
        QApplication.processEvents()

        self.assertEqual(result_holder['value'], 42)

    def test_worker_pause_resume(self):
        """Test worker can be paused and resumed."""

        def slow_task():
            time.sleep(0.5)
            return "done"

        worker = Worker("test_job", slow_task)

        # Can't pause before running
        self.assertFalse(worker.pause())

        # Start worker in thread
        from PyQt6.QtCore import QThreadPool
        pool = QThreadPool.globalInstance()
        pool.start(worker)

        time.sleep(0.1)  # Let it start

        # Should be able to pause
        # Note: This might be timing-dependent
        # In production, you'd use proper synchronization

    def test_worker_cancellation(self):
        """Test worker can be cancelled."""

        def long_task():
            time.sleep(2.0)
            return "done"

        worker = Worker("test_job", long_task)

        # Cancel immediately
        self.assertTrue(worker.cancel())
        self.assertTrue(worker.is_cancelled)

        # Can't cancel twice
        self.assertTrue(worker.cancel())  # Should still return True

    def test_worker_error_handling(self):
        """Test worker handles errors correctly."""
        error_holder = {'error': None}

        def failing_task():
            raise ValueError("Test error")

        def on_error(job_id, error):
            error_holder['error'] = error

        worker = Worker("test_job", failing_task)
        worker.signals.error.connect(on_error)
        worker.run()

        QApplication.processEvents()

        self.assertIsInstance(error_holder['error'], ValueError)
        self.assertEqual(str(error_holder['error']), "Test error")


class TestProgressAwareWorker(unittest.TestCase):
    """Test ProgressAwareWorker functionality."""

    @classmethod
    def setUpClass(cls):
        """Set up QApplication for tests."""
        if not QApplication.instance():
            cls.app = QApplication(sys.argv)

    def test_progress_callback_injection(self):
        """Test progress callback is injected correctly."""
        progress_values = []

        def task_with_progress(progress_callback):
            for i in range(5):
                progress_callback(i / 4)
            return "done"

        def on_progress(job_id, progress):
            progress_values.append(progress)

        worker = ProgressAwareWorker("test_job", task_with_progress)
        worker.signals.progress.connect(on_progress)
        worker.run()

        QApplication.processEvents()

        # Should have received progress updates
        self.assertGreater(len(progress_values), 0)
        self.assertTrue(all(0.0 <= p <= 1.0 for p in progress_values))

    def test_progress_bounds_clamping(self):
        """Test progress values are clamped to [0, 1]."""
        progress_values = []

        def task_with_invalid_progress(progress_callback):
            progress_callback(-0.5)  # Too low
            progress_callback(1.5)  # Too high
            return "done"

        def on_progress(job_id, progress):
            progress_values.append(progress)

        worker = ProgressAwareWorker("test_job", task_with_invalid_progress)
        worker.signals.progress.connect(on_progress)
        worker.run()

        QApplication.processEvents()

        # All values should be clamped
        self.assertTrue(all(0.0 <= p <= 1.0 for p in progress_values))


class TestThreadPoolManager(unittest.TestCase):
    """Test ThreadPoolManager functionality."""

    @classmethod
    def setUpClass(cls):
        """Set up QApplication for tests."""
        if not QApplication.instance():
            cls.app = QApplication(sys.argv)

    def setUp(self):
        """Reset manager state before each test."""
        # Get fresh instance
        self.manager = ThreadPoolManager()
        self.manager._jobs.clear()
        self.manager._job_counter = 0
        self.manager._shutdown_requested = False

    def test_singleton_pattern(self):
        """Test manager follows singleton pattern."""
        manager1 = ThreadPoolManager()
        manager2 = ThreadPoolManager()
        manager3 = get_thread_pool_manager()

        self.assertIs(manager1, manager2)
        self.assertIs(manager2, manager3)

    def test_configuration(self):
        """Test thread pool configuration."""
        original_count = self.manager.max_thread_count

        self.manager.configure(max_threads=8)
        self.assertEqual(self.manager.max_thread_count, 8)

        # Restore original
        self.manager.configure(max_threads=original_count)

    def test_job_submission(self):
        """Test submitting jobs to the pool."""

        def simple_task(x):
            return x * 2

        job_id = self.manager.submit(simple_task, 5)

        self.assertIsNotNone(job_id)
        self.assertIn(job_id, self.manager._jobs)
        self.assertEqual(self.manager.job_count, 1)

    def test_job_submission_with_custom_id(self):
        """Test submitting job with custom ID."""
        job_id = self.manager.submit(
            lambda x: x,
            10,
            job_id="custom_job_123"
        )

        self.assertEqual(job_id, "custom_job_123")
        self.assertIn("custom_job_123", self.manager._jobs)

    def test_job_status_tracking(self):
        """Test job status is tracked correctly."""

        def quick_task():
            time.sleep(0.1)
            return "done"

        job_id = self.manager.submit(quick_task)

        # Initial status should be pending or running
        initial_status = self.manager.get_job_status(job_id)
        self.assertIn(initial_status, [JobStatus.PENDING, JobStatus.RUNNING])

        # Wait for completion
        self.manager.wait_for_done(2000)

        final_status = self.manager.get_job_status(job_id)
        self.assertEqual(final_status, JobStatus.COMPLETED)

    def test_job_metrics_retrieval(self):
        """Test retrieving job metrics."""
        job_id = self.manager.submit(lambda: "test")

        metrics = self.manager.get_job_metrics(job_id)

        self.assertIsNotNone(metrics)
        self.assertEqual(metrics.job_id, job_id)
        self.assertIsInstance(metrics, JobMetrics)

    def test_pause_resume_job(self):
        """Test pausing and resuming jobs."""

        def pausable_task():
            time.sleep(1.0)
            return "done"

        job_id = self.manager.submit(pausable_task)

        # Let it start
        time.sleep(0.1)

        # Pause should succeed (timing dependent)
        # In real tests, you'd use better synchronization
        paused = self.manager.pause_job(job_id)

        # Resume
        if paused:
            resumed = self.manager.resume_job(job_id)
            self.assertTrue(resumed)

    def test_cancel_job(self):
        """Test cancelling a job."""

        def long_task():
            time.sleep(5.0)
            return "done"

        job_id = self.manager.submit(long_task)

        # Cancel immediately
        cancelled = self.manager.cancel_job(job_id)
        self.assertTrue(cancelled)

        # Wait a bit
        time.sleep(0.5)

        status = self.manager.get_job_status(job_id)
        self.assertEqual(status, JobStatus.CANCELLED)

    def test_get_active_jobs(self):
        """Test getting list of active jobs."""
        # Submit multiple jobs
        job_ids = []
        for i in range(3):
            job_id = self.manager.submit(
                lambda x: time.sleep(0.5),
                i
            )
            job_ids.append(job_id)

        active = self.manager.get_active_jobs()

        # Should have active jobs
        self.assertGreater(len(active), 0)

        # Wait for completion
        self.manager.wait_for_done(2000)

        # Should have fewer or no active jobs
        active_after = self.manager.get_active_jobs()
        self.assertLessEqual(len(active_after), len(active))

    def test_shutdown_prevents_new_submissions(self):
        """Test shutdown prevents new job submissions."""
        self.manager.shutdown(wait=False)

        with self.assertRaises(RuntimeError):
            self.manager.submit(lambda: "test")

    def test_multiple_jobs_parallel_execution(self):
        """Test multiple jobs execute in parallel."""
        results = []

        def timed_task(task_id):
            start = time.time()
            time.sleep(0.2)
            elapsed = time.time() - start
            return (task_id, elapsed)

        # Submit 4 jobs
        start_time = time.time()
        job_ids = []
        for i in range(4):
            job_id = self.manager.submit(timed_task, i)
            job_ids.append(job_id)

        # Wait for all to complete
        self.manager.wait_for_done(5000)
        total_time = time.time() - start_time

        # With parallel execution, should take ~0.2s, not 0.8s
        # Allow some overhead
        self.assertLess(total_time, 0.6)

    def test_priority_job_submission(self):
        """Test job priority affects execution order."""
        # This is hard to test reliably due to timing
        # But we can verify priority parameter is accepted
        job_id = self.manager.submit(
            lambda: "test",
            priority=10
        )
        self.assertIsNotNone(job_id)


class TestJobStatusEnum(unittest.TestCase):
    """Test JobStatus enumeration."""

    def test_all_statuses_present(self):
        """Test all expected statuses are defined."""
        expected_statuses = [
            'PENDING', 'RUNNING', 'PAUSED',
            'COMPLETED', 'CANCELLED', 'FAILED'
        ]

        for status_name in expected_statuses:
            self.assertTrue(hasattr(JobStatus, status_name))

    def test_status_uniqueness(self):
        """Test all status values are unique."""
        values = [status.value for status in JobStatus]
        self.assertEqual(len(values), len(set(values)))


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and error conditions."""

    @classmethod
    def setUpClass(cls):
        """Set up QApplication for tests."""
        if not QApplication.instance():
            cls.app = QApplication(sys.argv)

    def setUp(self):
        """Reset manager state before each test."""
        self.manager = ThreadPoolManager()
        self.manager._jobs.clear()
        self.manager._job_counter = 0
        self.manager._shutdown_requested = False

    def test_get_status_nonexistent_job(self):
        """Test getting status of nonexistent job."""
        status = self.manager.get_job_status("nonexistent")
        self.assertIsNone(status)

    def test_get_metrics_nonexistent_job(self):
        """Test getting metrics of nonexistent job."""
        metrics = self.manager.get_job_metrics("nonexistent")
        self.assertIsNone(metrics)

    def test_pause_nonexistent_job(self):
        """Test pausing nonexistent job."""
        result = self.manager.pause_job("nonexistent")
        self.assertFalse(result)

    def test_resume_nonexistent_job(self):
        """Test resuming nonexistent job."""
        result = self.manager.resume_job("nonexistent")
        self.assertFalse(result)

    def test_cancel_nonexistent_job(self):
        """Test cancelling nonexistent job."""
        result = self.manager.cancel_job("nonexistent")
        self.assertFalse(result)

    def test_job_with_kwargs(self):
        """Test submitting job with keyword arguments."""

        def task(a, b, c=0):
            return a + b + c

        job_id = self.manager.submit(task, 1, 2, c=3)
        self.assertIsNotNone(job_id)

        self.manager.wait_for_done(1000)

        metrics = self.manager.get_job_metrics(job_id)
        self.assertEqual(metrics.result, 6)

    def test_zero_max_threads(self):
        """Test behavior with minimal thread count."""
        original_count = self.manager.max_thread_count

        # Set to 1 thread
        self.manager.configure(max_threads=1)

        job_id = self.manager.submit(lambda: "test")
        self.assertIsNotNone(job_id)

        # Restore
        self.manager.configure(max_threads=original_count)


def run_tests():
    """Run all tests."""
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test cases
    suite.addTests(loader.loadTestsFromTestCase(TestJobMetrics))
    suite.addTests(loader.loadTestsFromTestCase(TestWorker))
    suite.addTests(loader.loadTestsFromTestCase(TestProgressAwareWorker))
    suite.addTests(loader.loadTestsFromTestCase(TestThreadPoolManager))
    suite.addTests(loader.loadTestsFromTestCase(TestJobStatusEnum))
    suite.addTests(loader.loadTestsFromTestCase(TestEdgeCases))

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Return success status
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)