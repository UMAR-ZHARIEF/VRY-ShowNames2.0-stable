"""Offline tests for the shared valorant-api.com content cache.

Run with the py launcher from the repo root:

    py tests/test_static_content.py
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import static_content


class StaticContentTests(unittest.TestCase):
    def setUp(self):
        static_content._reset_cache_for_tests()
        self.calls = []
        self.payload = {"status": 200, "data": [{"uuid": "x", "name": "Vandal"}]}

    def _fake_response(self, payload):
        return mock.Mock(json=lambda: dict(payload))

    def test_second_call_hits_cache_with_zero_new_fetches(self):
        with mock.patch.object(
            static_content.requests, "get",
            lambda url, timeout=None: (self.calls.append(url) or self._fake_response(self.payload)),
        ):
            first = static_content.get_json("/v1/weapons")
            second = static_content.get_json("/v1/weapons")
            third = static_content.get_json("/v1/weapons")
        self.assertEqual(len(self.calls), 1, "cache hit must not refetch")
        self.assertEqual(first["data"], [{"uuid": "x", "name": "Vandal"}])
        self.assertIs(first, second)
        self.assertIs(second, third)

    def test_failure_returns_none_then_retry_succeeds(self):
        payload_ok = {"status": 200, "data": []}
        outcomes = [RuntimeError("boom"), self._fake_response(payload_ok)]

        def flaky_get(url, timeout=None):
            self.calls.append(url)
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with mock.patch.object(static_content.requests, "get", flaky_get):
            first = static_content.get_json("/v1/version")
            self.assertIsNone(first, "a failed fetch returns None")
            second = static_content.get_json("/v1/version")
        self.assertEqual(len(self.calls), 2, "failures must not be cached")
        self.assertEqual(second, payload_ok)

    def test_distinct_paths_are_cached_separately(self):
        with mock.patch.object(
            static_content.requests, "get",
            lambda url, timeout=None: (self.calls.append(url) or self._fake_response({"data": [url]})),
        ):
            static_content.get_json("/v1/weapons")
            static_content.get_json("/v1/weapons?isPlayableCharacter=true")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1].endswith("isPlayableCharacter=true"), True)


if __name__ == "__main__":
    unittest.main()
