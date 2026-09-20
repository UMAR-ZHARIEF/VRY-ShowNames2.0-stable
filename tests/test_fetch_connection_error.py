"""Remote transport failures must never escape fetch() as a crash.

Regression background: in production (log-50, 20:57:20) Riot's glz server
closed a mid-poll connection at a match boundary without answering
(ConnectionError('Connection aborted.', RemoteDisconnected(...))). The
exception propagated fetch() -> Coregame.get_coregame_match_id -> the main
polling loop -> the top-level FATAL handler and killed the process.
src/requestsV.py now wraps the request call in the glz, pd, and custom
branches with a handler for the three network-flavored failure types
(ConnectionError, Timeout, ChunkedEncodingError): one compact
"skipping cycle" log line and a None result. The None flows to callers
that now degrade safely: the cycle guards in src/states/ skip the cycle,
Rank.get_rank returns its zeroed failure dict (RankNoneResponseTest),
and Content.get_act_episode_from_act_id returns its default act/episode
shape (ContentNoneToleranceTest). No retry, no recursion.
The local 127.0.0.1 branch keeps its own 3-attempt retry loop and is
pinned here to prove it still retries. All tests are offline (no network).
"""
import http.client
import os
import sys
import unittest
from unittest import mock

from requests.exceptions import ConnectionError, ChunkedEncodingError, Timeout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from src.requestsV import Requests  # noqa: E402


def bare_requests(log):
    """A Requests instance without running __init__ (no lockfile reads,
    no entitlements call, no network). The glz/pd fetch branches only
    need urls, headers, log, and the get_headers stub each test sets."""
    instance = Requests.__new__(Requests)
    instance.Error = None
    instance.version = "test-version"
    instance.headers = {}
    instance.log = log
    instance.lockfile = {"name": "ShooterGame", "PID": "1", "port": "2999",
                         "password": "pw", "protocol": "https"}
    instance.region = "na"
    instance.pd_url = "https://pd.na.a.pvp.net"
    instance.glz_url = "https://glz-na-1.na.a.pvp.net"
    instance.puuid = "puuid-test"
    return instance


def ready_instance(logs):
    instance = bare_requests(logs.append)
    instance.headers = {"Authorization": "Bearer initial"}
    instance.get_headers = lambda: {"Authorization": "Bearer initial"}
    return instance


def skipping_cycle_lines(logs):
    return [line for line in logs if "skipping cycle" in line]


class GlzConnectionErrorTest(unittest.TestCase):
    """(a) A glz ConnectionError: fetch returns None, logs exactly one
    compact skipping-cycle line, raises nothing, never retries."""

    def test_returns_none_one_log_line_no_retry(self):
        logs = []
        instance = ready_instance(logs)
        with mock.patch("src.requestsV.requests.request",
                        side_effect=ConnectionError("Connection aborted.")
                        ) as request_mock:
            result = instance.fetch(
                "glz", "/core-game/v1/matches/m1/loadouts", "get")
        self.assertIsNone(result)
        self.assertEqual(request_mock.call_count, 1)  # no retry, no recursion
        self.assertEqual(len(skipping_cycle_lines(logs)), 1)
        line = skipping_cycle_lines(logs)[0]
        self.assertIn("url: 'glz'", line)
        self.assertIn("endpoint: /core-game/v1/matches/m1/loadouts", line)
        self.assertIn("ConnectionError", line)
        self.assertNotIn("detected bad claims", logs)


class GlzTimeoutTest(unittest.TestCase):
    """(b) A glz Timeout: same None + one compact line contract."""

    def test_timeout_returns_none_one_log_line(self):
        logs = []
        instance = ready_instance(logs)
        with mock.patch("src.requestsV.requests.request",
                        side_effect=Timeout("The read operation timed out.")
                        ) as request_mock:
            result = instance.fetch("glz", "/match-1/players", "get")
        self.assertIsNone(result)
        self.assertEqual(request_mock.call_count, 1)
        self.assertEqual(len(skipping_cycle_lines(logs)), 1)
        self.assertIn("Timeout", skipping_cycle_lines(logs)[0])

    def test_chunked_encoding_error_returns_none(self):
        # Third class in the contract tuple (peer broke a chunked body
        # mid-transfer); must take the same None path, not propagate.
        logs = []
        instance = ready_instance(logs)
        with mock.patch("src.requestsV.requests.request",
                        side_effect=ChunkedEncodingError("Connection broken")
                        ) as request_mock:
            result = instance.fetch("glz", "/match-1/players", "get")
        self.assertIsNone(result)
        self.assertEqual(request_mock.call_count, 1)
        self.assertIn("ChunkedEncodingError", skipping_cycle_lines(logs)[0])


class PdConnectionErrorTest(unittest.TestCase):
    """The pd branch gets the same wrap: None result, one compact line."""

    def test_pd_connection_error_returns_none(self):
        logs = []
        instance = ready_instance(logs)
        with mock.patch("src.requestsV.requests.request",
                        side_effect=ConnectionError("Connection aborted.")
                        ) as request_mock:
            result = instance.fetch("pd", "/mmr/v1/players/puuid-test", "get")
        self.assertIsNone(result)
        self.assertEqual(request_mock.call_count, 1)
        self.assertEqual(len(skipping_cycle_lines(logs)), 1)
        line = skipping_cycle_lines(logs)[0]
        self.assertIn("url: 'pd'", line)
        self.assertIn("endpoint: /mmr/v1/players/puuid-test", line)


class CustomConnectionErrorTest(unittest.TestCase):
    """(c) The custom branch (shared content-service host) gets the same
    wrap: None result, one compact line."""

    def test_custom_connection_error_returns_none(self):
        logs = []
        instance = ready_instance(logs)
        with mock.patch("src.requestsV.requests.request",
                        side_effect=ConnectionError("Connection aborted.")
                        ) as request_mock:
            result = instance.fetch(
                "custom",
                "https://shared.na.a.pvp.net/content-service/v3/content",
                "get")
        self.assertIsNone(result)
        self.assertEqual(request_mock.call_count, 1)
        self.assertEqual(len(skipping_cycle_lines(logs)), 1)
        line = skipping_cycle_lines(logs)[0]
        self.assertIn("url: 'custom'", line)
        self.assertIn("endpoint: https://shared.na.a.pvp.net"
                      "/content-service/v3/content", line)


class ProductionShapeRegressionPinTest(unittest.TestCase):
    """(d) The exact production shape (log-50, 20:57:20): glz core-game
    players URL, ConnectionError('Connection aborted.',
    RemoteDisconnected('Remote end closed connection without response')).
    The exception must not escape fetch()."""

    def test_production_connection_error_does_not_escape_fetch(self):
        logs = []
        instance = ready_instance(logs)
        production_error = ConnectionError(
            "Connection aborted.",
            http.client.RemoteDisconnected(
                "Remote end closed connection without response"))
        with mock.patch("src.requestsV.requests.request",
                        side_effect=production_error) as request_mock:
            try:
                result = instance.fetch(
                    "glz", "/core-game/v1/players/puuid-test", "get")
            except Exception as escaped:  # pragma: no cover - failure mode
                self.fail(f"production ConnectionError escaped fetch(): "
                          f"{type(escaped).__name__}: {escaped}")
        self.assertIsNone(result)
        self.assertEqual(request_mock.call_count, 1)
        self.assertEqual(len(skipping_cycle_lines(logs)), 1)
        self.assertIn("endpoint: /core-game/v1/players/puuid-test",
                      skipping_cycle_lines(logs)[0])


class LocalBranchRetryUnchangedTest(unittest.TestCase):
    """(e) The local 127.0.0.1 branch keeps its own 3-attempt retry loop:
    a connection error retries (no None-on-first-error) and only returns
    None after all attempts fail."""

    def test_local_retries_three_times_before_none(self):
        logs = []
        instance = bare_requests(logs.append)
        with mock.patch("src.requestsV.requests.request",
                        side_effect=ConnectionError("Connection aborted.")
                        ) as request_mock, \
             mock.patch("src.requestsV.time.sleep") as sleep_mock:
            result = instance.fetch("local", "/chat/v4/presences", "get")
        self.assertEqual(request_mock.call_count, 3)  # not None-on-first-error
        self.assertEqual(sleep_mock.call_count, 3)
        self.assertIsNone(result)  # only after all 3 attempts failed
        for attempt in (1, 2, 3):
            self.assertTrue(any(
                f"Retrying... ({attempt}/3)" in line for line in logs))
        self.assertTrue(any(
            "Failed to connect to local client after 3 attempts." in line
            for line in logs))
        # The local retry log shape is distinct from the remote skip line.
        self.assertEqual(skipping_cycle_lines(logs), [])

    def test_local_success_on_second_attempt_returns_response(self):
        logs = []
        instance = bare_requests(logs.append)
        good = mock.MagicMock(name="response")
        good.status_code = 200
        good.json.return_value = {"presences": []}
        with mock.patch("src.requestsV.requests.request",
                        side_effect=[ConnectionError("Connection aborted."),
                                     good]) as request_mock, \
             mock.patch("src.requestsV.time.sleep"):
            result = instance.fetch("local", "/chat/v4/presences", "get")
        self.assertEqual(request_mock.call_count, 2)
        self.assertEqual(result, {"presences": []})


class RankNoneResponseTest(unittest.TestCase):
    """(f) The pd rank caller: get_rank must return its zeroed failure
    dict when fetch() returns None, not crash on response.ok with an
    AttributeError. One compact log line, failure semantics identical to
    the existing KeyError/TypeError paths (act/episode still resolved
    from content, status flags reflect the absent response)."""

    def test_get_rank_none_response_returns_safe_defaults(self):
        from src.rank import Rank

        logs = []
        requests_stub = mock.MagicMock(name="Requests")
        requests_stub.fetch.return_value = None
        content_stub = mock.MagicMock(name="Content")
        content_stub.get_act_episode_from_act_id.return_value = {
            "act": 2, "episode": 8}

        rank = Rank(requests_stub, logs.append, content_stub, ranks_before={})
        result = rank.get_rank("puuid-test", "season-1")

        requests_stub.fetch.assert_called_once_with(
            "pd", "/mmr/v1/players/puuid-test", "get")
        self.assertEqual(result["rank"], 0)
        self.assertEqual(result["rr"], 0)
        self.assertEqual(result["leaderboard"], 0)
        self.assertEqual(result["peakrank"], 0)
        self.assertEqual(result["wr"], "N/a")
        self.assertEqual(result["numberofgames"], 0)
        self.assertEqual(result["peakrankact"], 2)
        self.assertEqual(result["peakrankep"], 8)
        self.assertIs(result["statusgood"], False)
        self.assertIsNone(result["statuscode"])
        self.assertEqual(len(logs), 1)  # one compact line, no traceback


class ContentNoneToleranceTest(unittest.TestCase):
    """(g) The startup season lookup family tolerates the new None world:
    a failed content fetch (self.content None or still {}) or a None act
    id (missing season) returns the default act/episode shape instead of
    raising; an unknown non-None act id keeps today's no-match behavior."""

    SEASONS = [
        {"ID": "act-1", "Name": "EPISODE 8 ACT 1", "Type": "act",
         "IsActive": True},
        {"ID": "ep-8", "Name": "EPISODE 8", "Type": "episode",
         "IsActive": False},
    ]

    def _instance(self, content):
        from src.content import Content

        instance = Content.__new__(Content)
        instance.Requests = None
        instance.log = lambda *args, **kwargs: None
        instance.content = content
        return instance

    def test_none_content_returns_default_shape(self):
        instance = self._instance(None)  # fetch() transport failure
        self.assertEqual(instance.get_act_episode_from_act_id("act-1"),
                         {"act": None, "episode": None})

    def test_empty_content_returns_default_shape(self):
        instance = self._instance({})  # Content() before any successful fetch
        self.assertEqual(instance.get_act_episode_from_act_id("act-1"),
                         {"act": None, "episode": None})

    def test_none_act_id_returns_default_shape(self):
        instance = self._instance({"Seasons": self.SEASONS})
        self.assertEqual(instance.get_act_episode_from_act_id(None),
                         {"act": None, "episode": None})

    def test_unknown_act_id_keeps_today_behavior(self):
        instance = self._instance({"Seasons": self.SEASONS})
        self.assertEqual(instance.get_act_episode_from_act_id("unknown-id"),
                         {"act": None, "episode": None})


if __name__ == "__main__":
    unittest.main()
