"""Regression tests for the loadouts probe restart guard.

Live evidence (logs/log-560.txt): once the probe thread finished with
status complete, every loop iteration restarted a NEW probe thread for
the same match (log spam plus polls of a dead pregame endpoint into
INGAME). The designed fix: a match_id that finished with status complete
or error enters a finished set and start_loadouts_probe() must never
relaunch it; results and final status stay readable.

Run with the py launcher from the repo root:

    py tests/test_probe_restart.py
"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.enemy_probe_loadouts import (
    get_enemy_puuids,
    get_loadouts_status,
    start_loadouts_probe,
)

MATCH_A = "11111111-2222-3333-4444-555555555555"
MATCH_B = "99999999-8888-7777-6666-555555555555"
MATCH_C = "abcdef01-2345-6789-abcd-ef0123456789"
ALLIES = [f"aaaaaaaa-0000-0000-0000-0000000000{i:02d}" for i in range(5)]
ENEMIES = [f"bbbbbbbb-0000-0000-0000-0000000000{i:02d}" for i in range(5)]


def quiet_log(*args, **kwargs):
    pass


def wait_until(predicate, timeout=6.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def complete_payload():
    entries = [{"Subject": subject} for subject in ALLIES + ENEMIES]
    return {"Loadouts": entries}


class StubRequests:
    """Thread-safe fetch counter; payload may be a dict or callable."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.lock = threading.Lock()

    def fetch(self, url_type=None, endpoint=None, method=None, *args, **kwargs):
        with self.lock:
            self.calls += 1
        if callable(self.payload):
            return self.payload()
        return self.payload


def start_and_wait_complete(match_id, payload, **kwargs):
    stub = StubRequests(payload)
    start_loadouts_probe(stub, match_id, ALLIES, quiet_log,
                         poll_seconds=0.01, budget_seconds=10, **kwargs)
    finished = wait_until(
        lambda: get_loadouts_status(match_id) == "complete")
    # Give the daemon thread a moment to exit and clear the active set.
    time.sleep(0.05)
    return stub, finished


def start_and_wait_error(match_id, error_code="PREGAME_MNF"):
    stub = StubRequests(lambda: {"errorCode": error_code})
    start_loadouts_probe(stub, match_id, ALLIES, quiet_log,
                         poll_seconds=0.01, budget_seconds=10)
    finished = wait_until(
        lambda: get_loadouts_status(match_id).startswith("error"))
    time.sleep(0.05)
    return stub, finished


class ProbeRestartTests(unittest.TestCase):
    def setUp(self):
        # Each test uses its own match ids, so the module-level result
        # store cannot leak state between cases.
        self.match_ids = [MATCH_A, MATCH_B, MATCH_C]

    def test_completed_probe_is_never_restarted(self):
        stub, finished = start_and_wait_complete(MATCH_A, complete_payload())
        self.assertTrue(finished)
        self.assertEqual(get_enemy_puuids(MATCH_A), ENEMIES)
        calls_before = stub.calls
        start_loadouts_probe(stub, MATCH_A, ALLIES, quiet_log,
                             poll_seconds=0.01, budget_seconds=10)
        time.sleep(0.15)
        self.assertEqual(stub.calls, calls_before,
                         "a finished probe must not be restarted (fetches)")

    def test_completed_probe_keeps_results_after_restart_attempt(self):
        start_and_wait_complete(MATCH_A, complete_payload())
        start_loadouts_probe(StubRequests(complete_payload()), MATCH_A,
                             ALLIES, quiet_log,
                             poll_seconds=0.01, budget_seconds=10)
        time.sleep(0.15)
        self.assertEqual(get_enemy_puuids(MATCH_A), ENEMIES)

    def test_errored_probe_is_never_restarted(self):
        stub, finished = start_and_wait_error(MATCH_B)
        self.assertTrue(finished)
        self.assertTrue(get_loadouts_status(MATCH_B).startswith("error"))
        calls_before = stub.calls
        start_loadouts_probe(stub, MATCH_B, ALLIES, quiet_log,
                             poll_seconds=0.01, budget_seconds=10)
        time.sleep(0.15)
        self.assertEqual(stub.calls, calls_before,
                         "an errored probe must not be restarted (fetches)")

    def test_final_status_survives_restart_attempt(self):
        start_and_wait_complete(MATCH_A, complete_payload())
        self.assertEqual(get_loadouts_status(MATCH_A), "complete")
        start_loadouts_probe(StubRequests(complete_payload()), MATCH_A,
                             ALLIES, quiet_log,
                             poll_seconds=0.01, budget_seconds=10)
        time.sleep(0.15)
        self.assertEqual(get_loadouts_status(MATCH_A), "complete",
                         "restart attempt must not reset the final status")

    def test_different_match_id_still_starts_fresh(self):
        stub_a, finished = start_and_wait_complete(MATCH_A, complete_payload())
        self.assertTrue(finished)
        stub_c = StubRequests(complete_payload())
        start_loadouts_probe(stub_c, MATCH_C, ALLIES, quiet_log,
                             poll_seconds=0.01, budget_seconds=10)
        drawn = wait_until(lambda: get_loadouts_status(MATCH_C) == "complete")
        self.assertTrue(drawn, "a new match id must still get a fresh probe")
        self.assertGreater(stub_c.calls, 0)
        self.assertEqual(get_enemy_puuids(MATCH_C), ENEMIES)


if __name__ == "__main__":
    unittest.main()
