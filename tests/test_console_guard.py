"""The console guard must keep a dead console from killing the app.

Regression background: the app died in production with OSError WinError 233
("No process is on the other end of the pipe") raised from rich console
writes after the console connection broke; the error reached the top-level
handler and terminated the process. src/console_guard.py wraps stdout so
the first OSError detaches the screen with exactly one log line, and every
later write/flush is a silent no-op.
"""
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from src.console_guard import ResilientStdout, install_console_guard  # noqa: E402


def pipe_error():
    """An OSError carrying winerror 233, as Windows raises on a dead pipe."""
    err = OSError(22, "No process is on the other end of the pipe")
    try:
        err.winerror = 233
    except AttributeError:
        pass  # non-Windows builds expose no winerror attribute
    return err


class BrokenStream:
    """File-like object whose write/flush fail like a detached console."""

    def __init__(self, fail_write=True, fail_flush=True):
        self.fail_write = fail_write
        self.fail_flush = fail_flush
        self.writes = []
        self.flushes = 0

    def write(self, text):
        self.writes.append(text)
        if self.fail_write:
            raise pipe_error()
        return len(text)

    def flush(self):
        self.flushes += 1
        if self.fail_flush:
            raise pipe_error()


class HealthyStream:
    def __init__(self):
        self.writes = []
        self.flushes = 0

    def write(self, text):
        self.writes.append(text)
        return len(text)

    def flush(self):
        self.flushes += 1
        return "flushed"

    def isatty(self):
        return False

    def fileno(self):
        return 7


class ConsoleGuardTest(unittest.TestCase):
    def setUp(self):
        self.logs = []

    def log(self, message):
        self.logs.append(message)

    def test_write_error_does_not_propagate_and_flips_to_noop(self):
        guard = ResilientStdout(BrokenStream(), self.log)
        self.assertFalse(guard.detached)
        returned = guard.write("hello")  # must not raise
        self.assertEqual(returned, len("hello"))
        self.assertTrue(guard.detached)

    def test_emits_exactly_one_log_line_with_error_and_continues(self):
        guard = ResilientStdout(BrokenStream(), self.log)
        guard.write("first")
        guard.write("second")
        guard.flush()
        self.assertEqual(len(self.logs), 1)
        line = self.logs[0]
        self.assertIn("console detached", line)
        self.assertIn("broken pipe on write", line)
        self.assertIn("233", line)
        self.assertIn("continuing without screen output", line)

    def test_later_writes_and_flushes_are_silent_noops(self):
        stream = BrokenStream()
        guard = ResilientStdout(stream, self.log)
        guard.write("boom")
        self.assertEqual(len(stream.writes), 1)  # only the failed attempt
        self.assertEqual(guard.write("x"), 1)
        self.assertIsNone(guard.flush())
        self.assertEqual(len(stream.writes), 1)
        self.assertEqual(len(self.logs), 1)

    def test_flush_error_also_detaches_after_healthy_write(self):
        stream = BrokenStream(fail_write=False, fail_flush=True)
        guard = ResilientStdout(stream, self.log)
        self.assertEqual(guard.write("visible"), 7)
        self.assertEqual(stream.writes, ["visible"])
        self.assertFalse(guard.detached)
        guard.flush()  # must not raise
        self.assertTrue(guard.detached)
        guard.write("swallowed")
        self.assertEqual(stream.writes, ["visible"])

    def test_healthy_passthrough_is_untouched(self):
        stream = HealthyStream()
        guard = ResilientStdout(stream, self.log)
        self.assertEqual(guard.write("abc"), 3)
        self.assertEqual(stream.writes, ["abc"])
        self.assertEqual(guard.flush(), "flushed")
        self.assertEqual(stream.flushes, 1)
        self.assertFalse(guard.isatty())
        self.assertEqual(guard.fileno(), 7)
        self.assertFalse(guard.detached)
        self.assertEqual(self.logs, [])

    def test_install_wraps_sys_stdout_and_is_idempotent(self):
        old_stdout = sys.stdout
        try:
            guarded = install_console_guard(self.log)
            self.assertIsInstance(sys.stdout, ResilientStdout)
            self.assertIs(sys.stdout, guarded)
            self.assertIs(install_console_guard(self.log), guarded)
        finally:
            sys.stdout = old_stdout

    def test_install_on_explicit_stream_leaves_sys_stdout_alone(self):
        old_stdout = sys.stdout
        stream = HealthyStream()
        try:
            guarded = install_console_guard(self.log, stream=stream)
            self.assertIsInstance(guarded, ResilientStdout)
            self.assertIs(sys.stdout, old_stdout)
        finally:
            sys.stdout = old_stdout


if __name__ == "__main__":
    unittest.main()
