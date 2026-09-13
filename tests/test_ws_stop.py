"""Offline contract tests for the restored per-cycle Ws (snapshot websocket.py).

The phase-2 persistent listener (start/stop/wait_for_event) was retired when
main.py returned to the per-cycle conntect_to_websocket pacing, so the old
persistent-contract tests no longer apply. These tests pin the API main.py
actually uses now, with no network access:

- Ws constructs with the exact argument shape main.py passes.
- recconect_to_websocket stays a coroutine (main loop awaits it per cycle).
- handle() ignores empty/garbage/foreign-uri messages.
- handle() derives the game state from a flat presence payload and returns it
  only when the state changed.
"""
import asyncio
import base64
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.websocket import Ws


class FakeRequests:
    def __init__(self):
        self.log = lambda msg: None
        self.puuid = "test-puuid"


class FakeCfg:
    chat_limit = 5

    def get_feature_flag(self, name):
        return False


def make_ws():
    return Ws(
        lockfile={"port": "2999", "password": "x"},
        Requests=FakeRequests(),
        cfg=FakeCfg(),
        colors=None,
        hide_names=False,
        server=None,
        rpc=None,
        name_cache=None,
    )


def presence_message(state, own_puuid="test-puuid"):
    private = json.dumps({"sessionLoopState": state})
    presence = {
        "puuid": own_puuid,
        "product": "valorant",
        "private": base64.b64encode(private.encode()).decode(),
    }
    return json.dumps(
        [
            8,
            "OnJsonApiEvent_chat_v4_presences",
            {"uri": "/chat/v4/presences", "data": {"presences": [presence]}},
        ]
    )


class WsPerCycleTests(unittest.TestCase):
    def test_constructs_with_main_loop_argument_shape(self):
        ws = make_ws()
        self.assertIsInstance(ws, Ws)

    def test_recconect_to_websocket_is_a_coroutine(self):
        self.assertTrue(asyncio.iscoroutinefunction(Ws.recconect_to_websocket))

    def test_handle_ignores_empty_short_and_garbage_messages(self):
        ws = make_ws()
        self.assertIsNone(ws.handle(None, "MENUS"))
        self.assertIsNone(ws.handle("", "MENUS"))
        self.assertIsNone(ws.handle("[1,2]", "MENUS"))
        self.assertIsNone(ws.handle("not json at all", "MENUS"))

    def test_handle_ignores_foreign_uri(self):
        ws = make_ws()
        message = json.dumps([8, "OnJsonApiEvent_help", {"uri": "/help", "data": {}}])
        self.assertIsNone(ws.handle(message, "MENUS"))

    def test_handle_returns_changed_flat_presence_state(self):
        ws = make_ws()
        self.assertEqual(ws.handle(presence_message("MENUS"), "INGAME"), "MENUS")

    def test_handle_returns_none_when_state_unchanged(self):
        ws = make_ws()
        self.assertIsNone(ws.handle(presence_message("MENUS"), "MENUS"))

    def test_handle_ignores_other_players_presences(self):
        ws = make_ws()
        self.assertIsNone(ws.handle(presence_message("MENUS", own_puuid="someone-else"), "INGAME"))


if __name__ == "__main__":
    unittest.main()
