"""Contract tests for the presence 'private' blob decode (src/presences.py).

Regression context: the Valorant client sometimes delivers the 'private'
blob DOUBLE-ENCODED (base64 of a JSON string that itself contains the JSON
object), so json.loads returned a str and dict.get("isValid") crashed with
AttributeError 'str' object has no attribute 'get' (production logs, one
account only). decode_presence had a commented-out '# try:' where a guard
used to be. Now both decode sites in Presences tolerate the double encoding
and any malformed blob: decode_presence returns the existing
invalid-placeholder dict, get_private_presence returns None, and failures
are logged rate-limited (first failure, then every 50th) instead of spamming.

Pure stdlib, no network: Presences is driven with stub Requests and a
list-appending log callable.
"""
import base64
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.presences import Presences

PLACEHOLDER = {
    "isValid": False,
    "partyId": 0,
    "partySize": 0,
    "partyVersion": 0,
}

VALID_PRESENCE = {
    "isValid": True,
    "partyId": "party-123",
    "partySize": 3,
    "partyVersion": 7,
    "sessionLoopState": "MENUS",
}


def _b64(text_or_obj):
    raw = text_or_obj if isinstance(text_or_obj, str) else json.dumps(text_or_obj)
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


class _StubRequests:
    puuid = "self-puuid"

    def fetch(self, *args, **kwargs):  # never called by these tests
        return None


def _make_presences(logs):
    return Presences(_StubRequests(), logs.append)


class DecodePresenceTests(unittest.TestCase):
    """decode_presence: the dict when valid, the placeholder dict otherwise."""

    def setUp(self):
        self.logs = []
        self.presences = _make_presences(self.logs)

    def test_single_encoded_valid_presence_returns_dict(self):
        blob = _b64(VALID_PRESENCE)
        self.assertEqual(self.presences.decode_presence(blob), VALID_PRESENCE)

    def test_double_encoded_presence_returns_dict(self):
        # base64 of a JSON string that itself contains the JSON object.
        blob = _b64(json.dumps(VALID_PRESENCE))
        self.assertEqual(self.presences.decode_presence(blob), VALID_PRESENCE)

    def test_production_double_encoded_json_string_returns_dict(self):
        # True production double-encode shape: the blob's utf-8 content is a
        # JSON string (json.dumps of the JSON object text), not the object
        # itself, so the first json.loads returns a str and a second parse
        # yields the dict.
        object_text = json.dumps(VALID_PRESENCE)
        blob = _b64(json.dumps(object_text))
        first_pass = json.loads(base64.b64decode(blob).decode("utf-8"))
        self.assertIsInstance(first_pass, str)
        self.assertEqual(json.loads(first_pass), VALID_PRESENCE)
        result = self.presences.decode_presence(blob)
        self.assertEqual(result, VALID_PRESENCE)
        self.assertTrue(result["isValid"])

    def test_empty_private_returns_placeholder(self):
        self.assertEqual(self.presences.decode_presence(""), PLACEHOLDER)

    def test_garbage_base64_returns_placeholder(self):
        self.assertEqual(self.presences.decode_presence("not-base64!!!"), PLACEHOLDER)

    def test_base64_of_non_json_text_returns_placeholder(self):
        blob = _b64("hello world, not json")
        self.assertEqual(self.presences.decode_presence(blob), PLACEHOLDER)

    def test_double_encoded_non_dict_inner_returns_placeholder(self):
        # Double-encoded, but the inner JSON value is a list, not a dict.
        blob = _b64(json.dumps([1, 2, 3]))
        self.assertEqual(self.presences.decode_presence(blob), PLACEHOLDER)

    def test_single_encoded_isvalid_false_returns_placeholder(self):
        blob = _b64({"isValid": False, "partyId": "x"})
        self.assertEqual(self.presences.decode_presence(blob), PLACEHOLDER)

    def test_placeholder_shape_is_exact(self):
        result = self.presences.decode_presence("")
        self.assertEqual(list(result.keys()), list(PLACEHOLDER.keys()))
        self.assertEqual(result, PLACEHOLDER)

    def test_valid_decode_logs_nothing(self):
        for _ in range(5):
            self.presences.decode_presence(_b64(VALID_PRESENCE))
        self.assertEqual(self.logs, [])


class GetPrivatePresenceDecodeTests(unittest.TestCase):
    """get_private_presence: same tolerance, returns None on failure."""

    def setUp(self):
        self.logs = []
        self.presences = _make_presences(self.logs)

    def _own_presence(self, private):
        return [{"puuid": "self-puuid", "private": private}]

    def test_double_encoded_presence_returns_dict(self):
        blob = _b64(json.dumps(VALID_PRESENCE))
        self.assertEqual(
            self.presences.get_private_presence(self._own_presence(blob)),
            VALID_PRESENCE,
        )

    def test_production_double_encoded_json_string_returns_dict(self):
        # True production double-encode shape: the blob's utf-8 content is a
        # JSON string (json.dumps of the JSON object text), not the object
        # itself, so the first json.loads returns a str and a second parse
        # yields the dict.
        object_text = json.dumps(VALID_PRESENCE)
        blob = _b64(json.dumps(object_text))
        first_pass = json.loads(base64.b64decode(blob).decode("utf-8"))
        self.assertIsInstance(first_pass, str)
        self.assertEqual(json.loads(first_pass), VALID_PRESENCE)
        result = self.presences.get_private_presence(self._own_presence(blob))
        self.assertEqual(result, VALID_PRESENCE)
        self.assertTrue(result["isValid"])

    def test_single_encoded_presence_returns_dict(self):
        self.assertEqual(
            self.presences.get_private_presence(self._own_presence(_b64(VALID_PRESENCE))),
            VALID_PRESENCE,
        )

    def test_empty_private_returns_none(self):
        self.assertIsNone(self.presences.get_private_presence(self._own_presence("")))

    def test_garbage_base64_returns_none(self):
        self.assertIsNone(
            self.presences.get_private_presence(self._own_presence("not-base64!!!"))
        )

    def test_double_encoded_non_dict_inner_returns_none(self):
        blob = _b64(json.dumps("just a string"))
        self.assertIsNone(self.presences.get_private_presence(self._own_presence(blob)))

    def test_other_players_presence_ignored(self):
        blob = _b64(VALID_PRESENCE)
        others = [{"puuid": "other-puuid", "private": blob}]
        self.assertIsNone(self.presences.get_private_presence(others))

    def test_league_of_legends_presence_returns_none(self):
        presence = {"puuid": "self-puuid", "private": "", "product": "league_of_legends"}
        self.assertIsNone(self.presences.get_private_presence([presence]))


class DecodeFailureLoggingTests(unittest.TestCase):
    """Failures log once, then only every 50th time."""

    def test_first_failure_logged_then_every_fiftieth(self):
        logs = []
        presences = _make_presences(logs)
        for _ in range(50):
            presences.decode_presence("not-base64!!!")
        self.assertEqual(len(logs), 2)
        self.assertIn("decode_presence", logs[0])
        self.assertIn("#1", logs[0])
        self.assertIn("#50", logs[1])

    def test_failure_counter_is_per_instance(self):
        logs_a, logs_b = [], []
        presences_a = _make_presences(logs_a)
        presences_b = _make_presences(logs_b)
        presences_a.decode_presence("not-base64!!!")
        presences_b.decode_presence(_b64("still not json"))
        self.assertEqual(len(logs_a), 1)
        self.assertEqual(len(logs_b), 1)

    def test_site_label_reported_for_get_private_presence(self):
        logs = []
        presences = _make_presences(logs)
        presences.get_private_presence([{"puuid": "self-puuid", "private": "!!!"}])
        self.assertEqual(len(logs), 1)
        self.assertIn("get_private_presence", logs[0])


if __name__ == "__main__":
    unittest.main()
