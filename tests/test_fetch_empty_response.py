"""Empty and non-JSON response bodies must never crash fetch() callers.

Regression background: in production a glz endpoint returned 200 with an
empty body at a match boundary. fetch() called response.json(), caught
the resulting error and returned None; callers then crashed on
None.get(...) or on the missing "Loadouts" key in src/Loadouts.py.
src/requestsV.py now parses every body through _parse_json_body (None
means empty, non-JSON, or a literal "null" body), guards each BAD_CLAIMS
check with isinstance(body, dict), logs one compact
"empty/non-JSON body, skipping" line and returns None. src/Loadouts.py
get_match_loadouts degrades a non-dict fetch result to empty loadouts
instead of crashing. These tests pin all of that offline (no network).
"""
import json
import os
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from src.requestsV import Requests, _parse_json_body  # noqa: E402
from src.Loadouts import Loadouts  # noqa: E402


def mock_response(status_code=200, ok=True, json_value=None, json_error=None,
                  text=""):
    """A stand-in requests.Response; json_error makes .json() raise."""
    response = mock.MagicMock(name="response")
    response.status_code = status_code
    response.ok = ok
    response.text = text
    if json_error is not None:
        response.json.side_effect = json_error
    else:
        response.json.return_value = json_value
    return response


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


class ParseJsonBodyTest(unittest.TestCase):
    """_parse_json_body: None means empty, non-JSON, or a "null" body."""

    def test_empty_body_returns_none(self):
        response = mock_response(
            json_error=ValueError("Expecting value: line 1 column 1 (char 0)"))
        self.assertIsNone(_parse_json_body(response))

    def test_null_body_returns_none(self):
        # A literal "null" body: .json() returns None without raising
        # (json.loads("null") is None); that must map to the same None.
        response = mock_response(json_value=json.loads("null"))
        self.assertIsNone(_parse_json_body(response))

    def test_valid_dict_is_returned_unchanged(self):
        body = {"errorCode": "MATCH_ENDED"}
        response = mock_response(json_value=body)
        self.assertIs(_parse_json_body(response), body)


class GlzFetchEmptyBodyTest(unittest.TestCase):
    """A glz 200 with an empty body: return None, log one compact skip
    line, no BAD_CLAIMS check and no retry recursion."""

    def setUp(self):
        self.logs = []
        self.instance = bare_requests(self.logs.append)
        self.instance.headers = {"Authorization": "Bearer initial"}
        self.instance.get_headers = lambda: {"Authorization": "Bearer initial"}

    def test_returns_none_logs_once_no_recursion(self):
        empty = mock_response(status_code=200, ok=True,
                              json_error=ValueError("Expecting value"))
        with mock.patch("src.requestsV.requests.request",
                        return_value=empty) as request_mock:
            result = self.instance.fetch(
                "glz", "/core-game/v1/matches/m1/loadouts", "get")
        self.assertIsNone(result)
        self.assertEqual(request_mock.call_count, 1)  # no recursion
        self.assertNotIn("detected bad claims", self.logs)
        skip_lines = [line for line in self.logs
                      if "empty/non-JSON body, skipping" in line]
        self.assertEqual(len(skip_lines), 1)
        self.assertIn("endpoint: /core-game/v1/matches/m1/loadouts",
                      skip_lines[0])


class GlzFetchBadClaimsTest(unittest.TestCase):
    """A dict body with errorCode BAD_CLAIMS must still reset the headers
    and re-fetch exactly once (guard untouched by the empty-body fix)."""

    def test_resets_headers_and_refetches_once(self):
        logs = []
        bad = mock_response(status_code=200, ok=True,
                            json_value={"errorCode": "BAD_CLAIMS"})
        good = mock_response(status_code=200, ok=True,
                             json_value={"matchEnded": True})
        instance = bare_requests(logs.append)
        instance.headers = {"Authorization": "Bearer initial"}
        header_state_at_call = []

        def stub_get_headers():
            header_state_at_call.append(dict(instance.headers))
            return {"Authorization": "Bearer stub"}

        instance.get_headers = stub_get_headers
        with mock.patch("src.requestsV.requests.request",
                        side_effect=[bad, good]) as request_mock:
            result = instance.fetch(
                "glz", "/core-game/v1/matches/m1/loadouts", "get")
        self.assertEqual(result, {"matchEnded": True})
        self.assertEqual(request_mock.call_count, 2)  # exactly one re-fetch
        self.assertIn("detected bad claims", logs)
        # headers were reset to {} before the recursive fetch ran
        self.assertEqual(header_state_at_call[0],
                         {"Authorization": "Bearer initial"})
        self.assertEqual(header_state_at_call[1], {})


class LoadoutsEmptyFetchTest(unittest.TestCase):
    """get_match_loadouts degrades a None fetch result (empty/non-JSON
    loadouts body) to the empty-loadouts structure instead of crashing."""

    def test_none_fetch_returns_empty_loadouts_without_raising(self):
        logs = []
        fetch_calls = []

        class StubRequests:
            def fetch(self, *args, **kwargs):
                fetch_calls.append(args)
                return None

        loadouts = Loadouts(StubRequests(), logs.append, colors=None,
                            Server=None, current_map="Ascent")
        players = [
            {"Subject": "puuid-1", "TeamID": "Blue"},
            {"Subject": "puuid-2", "TeamID": "Red"},
        ]
        with mock.patch("src.static_content.get_json",
                        return_value={"data": []}) as get_json_mock:
            result = loadouts.get_match_loadouts(
                "match-1", players, "Vandal", None, None)

        self.assertEqual(get_json_mock.call_count, 1)
        self.assertEqual(
            fetch_calls, [("glz", "/core-game/v1/matches/match-1/loadouts",
                           "get")])
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], {})
        self.assertEqual(result[1], {})
        state = result[2]
        self.assertEqual(state["map"], "Ascent")
        self.assertIsInstance(state["time"], int)
        self.assertEqual(sorted(state["Players"]), ["puuid-1", "puuid-2"])
        for subject, team in (("puuid-1", "Blue"), ("puuid-2", "Red")):
            self.assertEqual(
                state["Players"][subject],
                {"Agent": None, "Team": team, "Sprays": None, "Title": None,
                 "PlayerCard": None, "Weapons": None})
        self.assertTrue(any("using empty loadouts" in line for line in logs))


class NullBodyNeverGetTest(unittest.TestCase):
    """Regression shape: a body of None (response.json() == None) must
    never reach a .get() call anywhere inside fetch(); any such call
    would raise AttributeError and fail these tests."""

    def test_glz_null_body_returns_none_without_get(self):
        logs = []
        instance = bare_requests(logs.append)
        instance.headers = {"Authorization": "Bearer initial"}
        instance.get_headers = lambda: {"Authorization": "Bearer initial"}
        response = mock_response(status_code=200, ok=True,
                                 json_value=json.loads("null"))
        with mock.patch("src.requestsV.requests.request",
                        return_value=response) as request_mock:
            result = instance.fetch("glz", "/match-history/v1/m1", "get")
        self.assertIsNone(result)
        self.assertEqual(request_mock.call_count, 1)

    def test_pd_null_body_returns_response_without_get(self):
        logs = []
        instance = bare_requests(logs.append)
        instance.headers = {"Authorization": "Bearer initial"}
        instance.get_headers = lambda: {"Authorization": "Bearer initial"}
        response = mock_response(status_code=200, ok=True,
                                 json_value=json.loads("null"))
        with mock.patch("src.requestsV.requests.request",
                        return_value=response):
            result = instance.fetch("pd", "/mmr/v1/players/puuid-test", "get")
        # pd fetch keeps its contract of returning the Response itself
        self.assertIs(result, response)
        self.assertTrue(any("empty/non-JSON body, skipping" in line
                            for line in logs))


if __name__ == "__main__":
    unittest.main()
