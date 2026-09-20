"""Tests for the single-instance guard (src/single_instance.py)."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.single_instance as si


class SingleInstanceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.old_path = si.LOCK_PATH
        si.LOCK_PATH = os.path.join(self._tmp.name, "vry_shownames.lock")
        si._acquired = False

    def tearDown(self):
        si.release_single_instance_lock()
        si.LOCK_PATH = self.old_path

    def test_fresh_acquire_true_and_pid_written(self):
        self.assertTrue(si.acquire_single_instance_lock())
        with open(si.LOCK_PATH) as lock_file:
            self.assertEqual(int(lock_file.read()), os.getpid())

    def test_second_acquire_in_same_process_is_true(self):
        self.assertTrue(si.acquire_single_instance_lock())
        self.assertTrue(si.acquire_single_instance_lock())

    def test_live_foreign_pid_returns_false(self):
        import subprocess
        import time
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
        self.addCleanup(sleeper.wait)
        try:
            with open(si.LOCK_PATH, "w") as lock_file:
                lock_file.write(str(sleeper.pid))
            self.assertFalse(si.acquire_single_instance_lock())
        finally:
            sleeper.terminate()

    def test_stale_dead_pid_is_taken_over(self):
        with open(si.LOCK_PATH, "w") as lock_file:
            lock_file.write("99999999")  # PID that cannot exist
        self.assertTrue(si.acquire_single_instance_lock())
        with open(si.LOCK_PATH) as lock_file:
            self.assertEqual(int(lock_file.read()), os.getpid())

    def test_corrupt_lock_is_taken_over(self):
        with open(si.LOCK_PATH, "w") as lock_file:
            lock_file.write("not-a-number")
        self.assertTrue(si.acquire_single_instance_lock())

    def test_release_removes_lock_and_allows_reacquire(self):
        self.assertTrue(si.acquire_single_instance_lock())
        si.release_single_instance_lock()
        self.assertFalse(os.path.exists(si.LOCK_PATH))
        self.assertTrue(si.acquire_single_instance_lock())


class MainWiringTests(unittest.TestCase):
    def test_main_wires_guard_before_heavy_init(self):
        main_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"
        )
        with open(main_path, encoding="utf-8") as source_file:
            source = source_file.read()
        self.assertIn("acquire_single_instance_lock", source)
        self.assertIn("os._exit(0)", source)
        guard_pos = source.index("acquire_single_instance_lock()")
        heavy_pos = source.index("Logging = Logging()")
        self.assertLess(guard_pos, heavy_pos)


if __name__ == "__main__":
    unittest.main()
