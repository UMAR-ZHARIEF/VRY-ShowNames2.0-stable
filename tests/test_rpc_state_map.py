"""Tests for the rpc_state pin and the map override.

Every case is pure: fixtures stand in for the presence, the data mirror,
the map table, the gamemode table and the colors helper, so the card
builder runs with no I/O, no threads and no Discord connection.

Covered behaviour:
- forced 'lobby' / 'agent_select' / 'ingame' render their card regardless
  of the real presence state;
- the in-match card keeps its corner rank badge, the agent rides the
  details line, and score overrides apply while pinned;
- the map override replaces the big image on the agent-select and
  in-match cards only (the lobby keeps its game icon);
- without a map override the real map (when known) still shows;
- 'real' behaves exactly like an absent field: state follows presence
  and the timer resets on real transitions;
- invalid rpc_state / unknown map values warn once and fall back to real;
- from_config parses both new keys, including 'real'.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.rpc_payload import RpcOverrides, build_presence_payload


class FakeColors:
    agent_dict = {"e370fa57-4757-3459-eef8-4d69f2cf6e8e": "Sova"}


MAP_DICT = {
    "/game/maps/ascent/ascent": "Ascent",
    "/game/maps/haven/haven": "Haven",
    "/game/maps/therange/therange": "The Range",
}
GAMEMODES = {"competitive": "Competitive", "unrated": "Unrated"}

INGAME_PRESENCE = {
    "isValid": True,
    "sessionLoopState": "INGAME",
    "provisioningFlow": "Matchmaking",
    "queueId": "competitive",
    "partyOwnerMatchScoreAllyTeam": 4,
    "partyOwnerMatchScoreEnemyTeam": 9,
    "matchMap": "/game/maps/haven/haven",
}
MENUS_PRESENCE = {
    "isValid": True,
    "sessionLoopState": "MENUS",
    "queueId": "competitive",
    "partyAccessibility": "OPEN",
    "partySize": 1,
    "maxPartySize": 5,
}
DATA = {"agent": "e370fa57-4757-3459-eef8-4d69f2cf6e8e", "rank": 6, "rank_name": "Bronze 2 | 34rr"}


def build(presence, overrides, last_loop_state=None, start_time=100.0, now=200.0):
    return build_presence_payload(
        presence, DATA, overrides, MAP_DICT, GAMEMODES, FakeColors(),
        last_loop_state=last_loop_state, start_time=start_time, now=now,
    )


class ForcedStateTests(unittest.TestCase):
    def test_real_lobby_ingame_presence_shows_ingame_card(self):
        payload, _, _ = build(INGAME_PRESENCE, RpcOverrides())
        self.assertEqual(payload["details"].split(" // ")[0], "Competitive")

    def test_forced_lobby_beats_real_ingame(self):
        payload, _, _ = build(INGAME_PRESENCE, RpcOverrides(forced_state="MENUS"))
        self.assertIn("Lobby - Competitive", payload["details"])
        self.assertEqual(payload["large_image"], "game_icon")

    def test_forced_agent_select_from_real_menus(self):
        payload, _, _ = build(MENUS_PRESENCE, RpcOverrides(forced_state="PREGAME"))
        self.assertIn("Agent Select - Competitive", payload["details"])
        self.assertEqual(payload["large_image"], "game_icon")  # V icon, no map known from menus

    def test_forced_ingame_from_real_menus(self):
        payload, _, _ = build(MENUS_PRESENCE, RpcOverrides(forced_state="INGAME"))
        self.assertIn("Competitive // None - None", payload["details"])

    def test_pin_keeps_timer_counting_across_real_state_change(self):
        overrides = RpcOverrides(forced_state="INGAME")
        _, last_state, start = build(INGAME_PRESENCE, overrides, start_time=100.0, now=100.0)
        # Real state flips to MENUS while pinned: the in-match card stays.
        payload, last_state, start = build(MENUS_PRESENCE, overrides,
                                           last_loop_state=last_state,
                                           start_time=start, now=150.0)
        self.assertEqual(payload["state"], "In a Party (1 of 5)")
        self.assertEqual(start, 100.0)  # timer was NOT reset while pinned


class MapOverrideTests(unittest.TestCase):
    def test_map_override_replaces_ingame_splash(self):
        payload, _, _ = build(INGAME_PRESENCE, RpcOverrides(map_display="Ascent"))
        self.assertEqual(payload["large_image"], "game_icon")
        self.assertIn("Ascent", payload["large_text"])
        self.assertTrue(payload["state"].startswith("Ascent ·"))

    def test_map_override_replaces_agent_select_splash(self):
        payload, _, _ = build(MENUS_PRESENCE, RpcOverrides(forced_state="PREGAME",
                                                           map_display="Haven"))
        self.assertEqual(payload["large_image"], "game_icon")
        self.assertIn("Haven", payload["large_text"])
        self.assertTrue(payload["state"].startswith("Haven ·"))

    def test_lobby_card_keeps_game_icon_despite_map_override(self):
        payload, _, _ = build(MENUS_PRESENCE, RpcOverrides(map_display="Ascent"))
        self.assertEqual(payload["large_image"], "game_icon")

    def test_no_override_shows_real_map(self):
        payload, _, _ = build(INGAME_PRESENCE, RpcOverrides())
        self.assertEqual(payload["large_image"], "game_icon")
        self.assertIn("Haven", payload["large_text"])
        self.assertTrue(payload["state"].startswith("Haven ·"))


class ValidationTests(unittest.TestCase):
    def test_invalid_rpc_state_warns_and_falls_back(self):
        warnings = []
        overrides = RpcOverrides.from_config(
            {"rpc_overrides": {"rpc_state": "prison"}}, log=warnings.append)
        self.assertIsNone(overrides.forced_state)
        self.assertTrue(any("rpc_state" in line for line in warnings))

    def test_unknown_map_warns_and_falls_back(self):
        warnings = []
        overrides = RpcOverrides.from_config(
            {"rpc_overrides": {"map": "Notamap"}},
            log=warnings.append, map_dict=MAP_DICT)
        self.assertIsNone(overrides.map_display)
        self.assertTrue(any("map" in line for line in warnings))

    def test_from_config_parses_both_new_keys(self):
        overrides = RpcOverrides.from_config(
            {"rpc_overrides": {"rpc_state": "ingame", "map": "Ascent"}},
            log=None, map_dict=MAP_DICT)
        self.assertEqual(overrides.forced_state, "INGAME")
        self.assertEqual(overrides.map_display, "Ascent")

    def test_from_config_real_means_unpinned(self):
        overrides = RpcOverrides.from_config(
            {"rpc_overrides": {"rpc_state": "real", "map": "real"}},
            log=None, map_dict=MAP_DICT)
        self.assertIsNone(overrides.forced_state)
        self.assertIsNone(overrides.map_display)


class GameModeOverrideTests(unittest.TestCase):
    OVR = {"game_mode_display": "Unrated"}

    PREGAME_PRESENCE = {
        "isValid": True,
        "sessionLoopState": "PREGAME",
        "queueId": "competitive",
        "partyAccessibility": "CLOSED",
        "partySize": 1,
        "maxPartySize": 5,
    }

    def test_ingame_details_use_override(self):
        payload, _, _ = build(
            INGAME_PRESENCE, RpcOverrides(**self.OVR))
        self.assertIn("Unrated // 4 - 9", payload["details"])

    def test_menus_details_use_override(self):
        payload, _, _ = build(MENUS_PRESENCE, RpcOverrides(**self.OVR))
        self.assertEqual(payload["details"], " Lobby - Unrated")

    def test_pregame_details_use_override(self):
        payload, _, _ = build(self.PREGAME_PRESENCE, RpcOverrides(**self.OVR))
        self.assertEqual(payload["details"], "Agent Select - Unrated")
        self.assertEqual(payload["details"], "Agent Select - Unrated")

    def test_real_keeps_queue_mode(self):
        payload, _, _ = build(INGAME_PRESENCE, RpcOverrides())
        self.assertIn("Competitive // 4 - 9", payload["details"])

    def test_empty_value_warns_and_falls_back_to_real(self):
        warnings = []
        overrides = RpcOverrides.from_config(
            {"rpc_overrides": {"game_mode": "   "}}, log=warnings.append)
        self.assertIsNone(overrides.game_mode_display)
        self.assertTrue(any("game_mode" in line for line in warnings))
        payload, _, _ = build(INGAME_PRESENCE, overrides)
        self.assertIn("Competitive // 4 - 9", payload["details"])


if __name__ == "__main__":
    unittest.main()
