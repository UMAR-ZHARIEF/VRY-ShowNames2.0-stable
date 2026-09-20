"""Offline contract tests for the phone/overlay websocket server (server.py).

The public page https://vry.netlify.app/matchLoadouts is the only intended
client. Its bundle hardcodes `new WebSocket("ws://localhost:1100/")` and never
sends any message, so the server contract it depends on is: any path is
admitted, nothing is required from the client, and it receives a "version"
payload plus replayed cached payloads on connect. Adding a token or PIN here
would silently break the page (evidence: research/readonly-hardening-w4-phone.md).

These tests pin that page-compatibility contract with a fake transport
(no sockets are opened), so a future change that gates admission would fail
here instead of silently killing the phone feature.
"""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server import Server

SERVER_SOURCE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "server.py"
)


class FakeError:
    def PortError(self, port):
        pass


class FakeTransport:
    """Stands in for websocket_server.WebsocketServer (mocked socket layer)."""

    def __init__(self):
        self.sent = []

    def send_message_to_all(self, message):
        self.sent.append(message)


class FakeHandler:
    """Minimal stand-in for WebSocketHandler; the real library discards the
    request path, so request_path here only documents what a client dialed."""

    def __init__(self, request_path="/"):
        self.request_path = request_path


def make_page_like_client(request_path="/"):
    """A client exactly like the netlify page: any path, no credentials,
    and it never sends anything (the server has no message handler)."""
    return {
        "id": 1,
        "handler": FakeHandler(request_path),
        "address": ("192.168.1.50", 51000),
    }


def make_server():
    server = Server(log=lambda msg: None, Error=FakeError())
    server.server = FakeTransport()
    return server


class PhonePageContractTests(unittest.TestCase):
    def test_matchloadouts_page_connection_is_admitted_unconditionally(self):
        server = make_server()
        server.handle_new_client(make_page_like_client(), server.server)
        types = [json.loads(message)["type"] for message in server.server.sent]
        self.assertEqual(types, ["version"])

    def test_connect_replays_cached_heartbeat_and_matchloadout_after_version(self):
        server = make_server()
        server.send_payload("heartbeat", {"time": 1})
        server.send_payload("matchLoadout", {"Players": {}})
        server.server.sent.clear()

        server.handle_new_client(make_page_like_client(), server.server)

        types = [json.loads(message)["type"] for message in server.server.sent]
        # version first, then the replay the page shows instantly; "chat" and
        # "version" are excluded from replay per the existing implementation.
        self.assertEqual(types, ["version", "heartbeat", "matchLoadout"])

    def test_any_request_path_is_admitted(self):
        server = make_server()
        server.handle_new_client(
            make_page_like_client(request_path="/some/scanner/probe"), server.server
        )
        self.assertEqual(len(server.server.sent), 1)

    def test_send_payload_broadcasts_and_caches_for_replay(self):
        server = make_server()
        server.send_payload("heartbeat", {"time": 7})
        broadcast = json.loads(server.server.sent[0])
        self.assertEqual(broadcast["type"], "heartbeat")
        self.assertEqual(broadcast["time"], 7)
        self.assertEqual(server.lastMessages["heartbeat"], server.server.sent[0])

    def test_server_source_keeps_page_compatibility_contract(self):
        with open(SERVER_SOURCE_PATH, "r", encoding="utf-8") as handle:
            source = handle.read()
        # Binding must stay on all interfaces (phone access over Wi-Fi).
        self.assertIsNotNone(re.search(r'host\s*=\s*"0\.0\.0\.0"', source))
        # No per-launch secret machinery (page cannot carry a token).
        # (Only code counts; the constraint comment legitimately mentions the
        # word "token" in prose.)
        self.assertNotIn("import secrets", source)
        self.assertIsNone(re.search(r"secrets\.|urandom|token_urlsafe|uuid", source))
        # No client-message handler (page never sends anything; a PIN gate
        # would deadlock every page connection).
        self.assertNotIn("set_fn_message_received", source)


if __name__ == "__main__":
    unittest.main()
