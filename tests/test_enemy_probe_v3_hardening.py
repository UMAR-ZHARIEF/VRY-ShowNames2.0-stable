"""Hardening guards for src/enemy_probe_v3.py (2026-09-19 strip).

The readonly-hardening pass removed the raw-socket XMPP path (SASL auth,
bind/session IQs, presence, roster/disco, every MUC join flavor, clean-exit
presence), the host-discovery helpers it used (clientconfig fetch, netstat
port scan, JWT-claim hosts, PAS SASL token minting) and the three POST
entries in the chattoken request table. The w5 pass removed the now-uncalled
``_v3_fetch_chattokens`` helper (and its chattoken request table) entirely.
These tests pin the hardened surface: source-scan style guards (same pattern
as RequestsVerifyGuardTest in test_secrets_redaction.py), an import-level
entry-point signature pin, and traffic-dump redaction checks (auth
headers first, then w6b: secret-bearing bodies — PAS token-mint
responses and the local remoting credentials).

Run with the py launcher from the repo root:

    py tests/test_enemy_probe_v3_hardening.py
"""
import base64
import inspect
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import src.enemy_probe_v3 as v3mod  # noqa: E402

SOURCE_PATH = os.path.join(REPO_ROOT, "src", "enemy_probe_v3.py")


def _source():
    with open(SOURCE_PATH, encoding="utf-8") as handle:
        return handle.read()


class RemovedVectorsSourceTests(unittest.TestCase):
    """Source scan: the stripped XMPP machinery and POST targets stay gone."""

    def test_no_post_targets_in_request_table(self):
        source = _source()
        # Every remaining _req call must be GET/PUT only: no quoted "POST"
        # literal may exist anywhere in the module (the chattoken request
        # table that once held POST entries was removed entirely in w5).
        self.assertNotIn('"POST"', source)
        self.assertNotIn("'POST'", source)
        self.assertNotIn('.post(', source)
        self.assertNotIn('requests.post', source)

    def test_no_xmpp_socket_symbols_remain(self):
        source = _source()
        for symbol in (
            "socket.create_connection",
            "import socket",
            "import ssl",
            "ssl.",
            "import subprocess",
            "xmpp_probe_v3_async",
            "_xmpp_run",
            "_xmpp_get_pas_token",
            "_xmpp_candidate_hosts",
            "_netstat_active_port",
            "_v3_get_clientconfig",
            "_extract_service_hosts",
            "_CLIENTCONFIG_CACHE",
            "_XMPP_REAL_HOST",
            "X-Riot-RSO-PAS",
            "sasl",
            "SASL",
            "5223",
            "clientconfig.player.riotgames.com",
            # w5: the uncalled chattoken prober and its request table
            "_v3_fetch_chattokens",
            "pregame-chattoken-GET",
            "pregame-teamchatmuctoken",
            "pregame-allchatmuctoken",
            "pregame-muctoken-GET",
            "coregame-allchatmuctoken-GET",
            "coregame-teamchatmuctoken-GET",
            "parties-v2-muctoken",
            "parties-v1-voicetoken",
        ):
            self.assertNotIn(symbol, source,
                             f"removed symbol reappeared: {symbol}")


class KeptEntryPointTests(unittest.TestCase):
    """main.py / src.enemy_probes.py rely on these exact public shapes."""

    def test_kept_entry_point_signatures_unchanged(self):
        expected = {
            "get_confirmed_names": "(match_id)",
            "get_status": "(match_id)",
            "coregame_race_async": (
                "(requests_obj, match_id, ally_puuids, own_puuid, "
                "log, budget_seconds=70)"),
            "probe_v3_main": (
                "(requests_obj, pregame_match_id, own_puuid, "
                "ally_puuids, log)"),
            "probe_v3_main_async": (
                "(requests_obj, pregame_match_id, own_puuid, "
                "ally_puuids, log)"),
        }
        for name, expected_sig in expected.items():
            fn = getattr(v3mod, name, None)
            self.assertTrue(callable(fn), f"missing kept entry point: {name}")
            actual = str(inspect.signature(fn)).replace(" ", "")
            self.assertEqual(actual, expected_sig.replace(" ", ""),
                             f"signature drift on {name}")

    def test_removed_entry_point_is_gone(self):
        self.assertFalse(hasattr(v3mod, "xmpp_probe_v3_async"))
        for name in ("_xmpp_run", "_xmpp_get_pas_token",
                     "_xmpp_candidate_hosts", "_v3_get_clientconfig",
                     "_netstat_active_port", "_extract_service_hosts"):
            self.assertFalse(hasattr(v3mod, name),
                             f"removed helper still importable: {name}")


class TrafficDumpRedactionTests(unittest.TestCase):
    """The JSONL traffic dump must never contain auth credential values."""

    def test_auth_header_values_are_redacted_on_write(self):
        bearer = "Bearer eyJrs0bearer.secret.value"
        entitlements = "eyJentitlements.jwt.value"
        basic = "Basic " + base64.b64encode(b"riot:lockpw").decode()
        req_headers = {
            "Authorization": bearer,
            "X-Riot-Entitlements-JWT": entitlements,
            "X-Riot-ClientPlatform": "platform-blob",
        }
        resp_headers = {"WWW-Authenticate": basic}
        with tempfile.TemporaryDirectory() as tmp:
            dump_path = os.path.join(tmp, "dump.jsonl")
            with mock.patch.object(v3mod, "_TRAFFIC_DUMP_PATH", dump_path):
                v3mod._dump_traffic("GET", "https://example/v1/x",
                                    req_headers, None, 200,
                                    resp_headers, "body")
            with open(dump_path, encoding="utf-8") as handle:
                text = handle.read()
        record = json.loads(text)
        # Header names stay, values become ***.
        self.assertEqual(record["req_headers"]["Authorization"], "***")
        self.assertEqual(
            record["req_headers"]["X-Riot-Entitlements-JWT"], "***")
        self.assertEqual(
            record["req_headers"]["X-Riot-ClientPlatform"], "platform-blob")
        self.assertEqual(record["resp_headers"]["WWW-Authenticate"],
                         "Basic ***")
        # No credential material anywhere in the dumped line.
        self.assertNotIn("eyJrs0bearer.secret.value", text)
        self.assertNotIn(entitlements, text)
        self.assertNotIn(base64.b64encode(b"riot:lockpw").decode(), text)

    def test_lowercase_header_names_are_redacted_too(self):
        redacted = v3mod._redact_dump_headers(
            {"authorization": "Bearer abc", "x-riot-entitlements-jwt": "t",
             "Accept": "application/json"})
        self.assertEqual(redacted["authorization"], "***")
        self.assertEqual(redacted["x-riot-entitlements-jwt"], "***")
        self.assertEqual(redacted["Accept"], "application/json")


class DumpBodyRedactionTests(unittest.TestCase):
    """_dump_traffic must also redact secret-bearing BODIES: the PAS
    service endpoints answer with raw JWTs and the local remoting
    vector's bodies embed the remoting-auth-token. Every other body
    (e.g. a normal glz response) must stay verbatim."""

    def _dump_to_temp(self, url, req_headers=None, req_body=None,
                      resp_text=""):
        with tempfile.TemporaryDirectory() as tmp:
            dump_path = os.path.join(tmp, "dump.jsonl")
            with mock.patch.object(v3mod, "_TRAFFIC_DUMP_PATH", dump_path):
                v3mod._dump_traffic("GET", url, req_headers, req_body,
                                    200, {}, resp_text)
            with open(dump_path, encoding="utf-8") as handle:
                return handle.read()

    def test_pas_token_body_is_replaced_with_placeholder(self):
        pas_jwt = "eyJhbGciOiJSUzI1NiJ9.pas-service-jwt-payload.sig"
        text = self._dump_to_temp(
            "https://riot-geo.pas.si.riotgames.com/pas/v1/service/chat",
            req_headers={"Authorization": "Bearer rso-token"},
            resp_text=pas_jwt)
        record = json.loads(text)
        self.assertEqual(record["resp_text"], "<redacted: token body>")
        self.assertNotIn(pas_jwt, text)
        self.assertNotIn("pas-service-jwt-payload", text)

    def test_normal_glz_body_is_preserved(self):
        body = json.dumps({"Teams": [{"Players": []}]})
        text = self._dump_to_temp(
            "https://glz.na-1.na.a.pvp.net/pregame/v1/matches/match-1",
            resp_text=body)
        record = json.loads(text)
        self.assertEqual(record["resp_text"], body)

    def test_remoting_token_strings_never_appear_in_dump_body(self):
        raw_token = "remotingtoken0123456789abcdef"
        basic = base64.b64encode(f"riot:{raw_token}".encode()).decode()
        body = ('{"launchConfiguration":{"arguments":['
                '"-remoting-app-port=2999",'
                f'"-remoting-auth-token={raw_token}"]]}}'
                f" echo Authorization: Basic {basic}")
        with mock.patch.object(v3mod, "_DUMP_BODY_SECRETS", set()):
            v3mod._register_dump_body_secret(raw_token)
            v3mod._register_dump_body_secret(basic)
            text = self._dump_to_temp("https://127.0.0.1:2999/help",
                                      resp_text=body)
        self.assertNotIn(raw_token, text)
        self.assertNotIn(basic, text)
        self.assertIn("-remoting-auth-token=<redacted>", text)

    def test_launch_arg_token_redacted_even_before_registration(self):
        # The product-session response is dumped BEFORE its token can
        # be parsed and registered; the static launch-arg pattern must
        # already redact it there.
        raw_token = "abcdef0123456789abcdef01"
        body = f'"-remoting-auth-token={raw_token}"'
        text = self._dump_to_temp(
            "https://127.0.0.1:1234/product-session/v1/sessions",
            resp_text=body)
        self.assertNotIn(raw_token, text)
        self.assertIn("-remoting-auth-token=<redacted>", text)


if __name__ == "__main__":
    unittest.main()
