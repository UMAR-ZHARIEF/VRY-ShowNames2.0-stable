"""Offline tests for PlayerStats.get_level_with_fallback.

Covers the any-queue history fallback used for enemy account levels when
the newest competitive match-details no longer exist (Riot prunes old
matches). Stub Requests only: no network, no game. Run with the py
launcher from the repo root:

    py tests/test_level_fallback.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.player_stats import PlayerStats

HISTORY_PREFIX = "/match-history/v1/history/"
DETAILS_PREFIX = "/match-details/v1/matches/"
PUUID = "deadbeef-0000-0000-0000-000000000001"


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class StubRequests:
    """fetch() returns scripted responses by endpoint prefix and counts calls."""

    def __init__(self, responses=None, raise_on=None):
        self.responses = responses or {}
        self.raise_on = raise_on
        self.calls = []

    def fetch(self, url_type, endpoint, method):
        self.calls.append(endpoint)
        if self.raise_on and self.raise_on in endpoint:
            raise RuntimeError("network down")
        for prefix, response in self.responses.items():
            if endpoint.startswith(prefix):
                return response
        raise AssertionError(f"unscripted endpoint: {endpoint}")


def make_stats(requests):
    logs = []
    stats = PlayerStats(requests, lambda message: logs.append(message), None)
    return stats, logs


class GetLevelWithFallbackTests(unittest.TestCase):
    def test_case_a_walks_history_until_details_exist(self):
        requests = StubRequests({
            HISTORY_PREFIX: FakeResponse(200, {"History": [
                {"MatchID": "m1"}, {"MatchID": "m2"}, {"MatchID": "m3"},
            ]}),
            DETAILS_PREFIX + "m1": FakeResponse(404, {}),
            DETAILS_PREFIX + "m2": FakeResponse(404, {}),
            DETAILS_PREFIX + "m3": FakeResponse(200, {"players": [
                {"subject": PUUID, "accountLevel": 152},
            ]}),
        })
        stats, logs = make_stats(requests)

        self.assertEqual(stats.get_level_with_fallback(PUUID), 152)

        history_calls = [c for c in requests.calls if c.startswith(HISTORY_PREFIX)]
        self.assertEqual(len(history_calls), 1)
        self.assertIn("level fallback: deadbeef via older match", logs)

    def test_case_b_all_details_gone_caches_none_and_logs_once(self):
        requests = StubRequests({
            HISTORY_PREFIX: FakeResponse(200, {"History": [
                {"MatchID": "a"}, {"MatchID": "b"},
            ]}),
            DETAILS_PREFIX + "a": FakeResponse(404, {}),
            DETAILS_PREFIX + "b": FakeResponse(404, {}),
        })
        stats, logs = make_stats(requests)

        self.assertIsNone(stats.get_level_with_fallback(PUUID))
        miss_lines = [m for m in logs if "level unresolved: deadbeef" in m]
        self.assertEqual(len(miss_lines), 1)

        calls_before = len(requests.calls)
        self.assertIsNone(stats.get_level_with_fallback(PUUID))
        self.assertEqual(len(requests.calls), calls_before)
        miss_lines_after = [m for m in logs if "level unresolved" in m]
        self.assertEqual(len(miss_lines_after), 1)

    def test_case_c_known_match_with_cached_details_needs_no_history(self):
        requests = StubRequests({})
        stats, _logs = make_stats(requests)
        stats._last_match_id_by_puuid[PUUID] = "known"
        stats.match_details_cache["known"] = {"players": [
            {"subject": PUUID, "accountLevel": 87},
        ]}

        self.assertEqual(stats.get_level_with_fallback(PUUID), 87)
        history_calls = [c for c in requests.calls if c.startswith(HISTORY_PREFIX)]
        self.assertEqual(history_calls, [])

    def test_case_d_history_fetch_failure_returns_none(self):
        requests = StubRequests(raise_on=HISTORY_PREFIX)
        stats, logs = make_stats(requests)

        self.assertIsNone(stats.get_level_with_fallback(PUUID))
        self.assertTrue(any("level unresolved: deadbeef" in m for m in logs))
        self.assertIsNone(stats._level_fallback_cache[PUUID])


if __name__ == "__main__":
    unittest.main()
