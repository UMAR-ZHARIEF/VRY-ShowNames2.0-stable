"""Contract tests for Presences.wait_for_presence (src/presences.py).

Speedup context (2026-09-20): production logs (log-615/log-616) show the wait
timing out on nearly every table build ("wait_for_presence: timeout, N puuids
still missing") while the app proceeds fine on other data, so the default cap
dropped 10s -> 2s. These tests pin the new default, the return contract (True
when every puuid is seen, False at the deadline) and the 0.5s polling rhythm
(no busy-loop).

Pure stdlib, no network and no real sleeps: the module's time module is
replaced with a fake clock that only advances inside sleep().
"""
import inspect
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.presences import Presences


class _StubRequests:
    puuid = "self-puuid"

    def fetch(self, *args, **kwargs):  # never called by these tests
        return None


class _FakeClock:
    """Stands in for the time module: time() returns a fake now, sleep()
    records the requested pause and advances the fake clock."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _make_presences():
    return Presences(_StubRequests(), lambda _message: None)


class WaitForPresenceTimeoutTests(unittest.TestCase):
    """Default cap is 2s; poll rhythm and return contract unchanged."""

    def test_default_timeout_is_two_seconds(self):
        default = inspect.signature(Presences.wait_for_presence).parameters[
            "timeout_seconds"
        ].default
        self.assertEqual(default, 2)

    def test_never_satisfied_poll_returns_false_within_two_seconds(self):
        presences = _make_presences()
        clock = _FakeClock()
        polls = []

        def _never_finds():
            polls.append(1)
            return [{"puuid": "someone-else"}]

        with mock.patch("src.presences.time", clock):
            with mock.patch.object(presences, "get_presence", _never_finds):
                result = presences.wait_for_presence(["missing-puuid"])

        self.assertFalse(result)
        self.assertEqual(clock.now - 1000.0, 2.0)  # deadline hit, not exceeded
        self.assertEqual(clock.sleeps, [0.5, 0.5, 0.5, 0.5])  # rhythm kept, bounded
        self.assertLessEqual(len(polls), 5)  # polled, not a busy-loop

    def test_all_found_returns_true_immediately(self):
        presences = _make_presences()
        clock = _FakeClock()

        with mock.patch("src.presences.time", clock):
            with mock.patch.object(
                presences,
                "get_presence",
                lambda: [{"puuid": "missing-puuid"}],
            ):
                result = presences.wait_for_presence(["missing-puuid"])

        self.assertTrue(result)
        self.assertEqual(clock.sleeps, [])  # no sleep once everything is found


if __name__ == "__main__":
    unittest.main()
