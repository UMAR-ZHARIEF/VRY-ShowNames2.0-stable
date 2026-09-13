"""Tests for the "game_mode" override in RpcOverrides/build_presence_payload.

The game_mode override is free text (no fixed vocabulary): it replaces the
queue-derived mode text in all three cards when set, falls back to the real
mode on "real" or empty (with a warning), and never touches any other field.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.rpc_payload import RpcOverrides, build_presence_payload


class FakeColors:
    agent_dict = {}


def _section(**overrides):
    base = {"rank": "real", "agent": "real", "party_size": "real",
            "party_max": "real", "score_ally": "real", "score_enemy": "real",
            "rpc_state": "real", "map": "real"}
    base.update(overrides)
    return {"rpc_overrides": base}


def _ingame_presence(queue="competitive"):
    return {
        "isValid": True,
        "sessionLoopState": "INGAME",
        "provisioningFlow": "Matchmaking",
        "queueId": queue,
        "partyOwnerMatchScoreAllyTeam": 4,
        "partyOwnerMatchScoreEnemyTeam": 9,
    }


def _menus_presence(queue="competitive"):
    return {
        "isValid": True,
        "sessionLoopState": "MENUS",
        "queueId": queue,
        "partySize": 2,
        "maxPartySize": 5,
        "partyAccessibility": "OPEN",
    }


def _pregame_presence(queue="competitive"):
    return {
        "isValid": True,
        "sessionLoopState": "PREGAME",
        "queueId": queue,
    }


class GameModeOverrideTests(unittest.TestCase):
    def _build(self, presence, overrides):
        ov = RpcOverrides.from_config(_section(**overrides))
        payload, _, _ = build_presence_payload(
            presence, {"rank": 3, "rank_name": "Iron 1"},
            ov, {}, {"competitive": "Competitive", "deathmatch": "Deathmatch", "spike_rush": "Spike Rush"}, FakeColors(),
        )
        return payload

    def test_lobby_details_uses_override(self):
        payload = self._build(_menus_presence(), {"game_mode": "Unrated"})
        self.assertEqual(payload["details"], " Lobby - Unrated")

    def test_pregame_details_uses_override(self):
        payload = self._build(_pregame_presence(), {"game_mode": "Unrated"})
        self.assertEqual(payload["details"], "Agent Select - Unrated")

    def test_ingame_details_uses_override(self):
        payload = self._build(_ingame_presence(), {"game_mode": "Unrated"})
        self.assertEqual(payload["details"].startswith("Unrated // 4 - 9"), True)

    def test_real_keeps_queue_mode(self):
        presence = _ingame_presence(queue="deathmatch")
        presence["partyOwnerMatchScoreAllyTeam"] = 7
        presence["partyOwnerMatchScoreEnemyTeam"] = 3
        payload = self._build(presence, {"game_mode": "real"})
        self.assertEqual(payload["details"].startswith("Deathmatch // 7 - 3"), True)

    def test_custom_game_label_path_is_overridable(self):
        presence = _ingame_presence()
        presence["partyOwnerMatchScoreAllyTeam"] = 7
        presence["partyOwnerMatchScoreEnemyTeam"] = 3
        payload = self._build(presence, {"game_mode": "Spike Rush"})
        self.assertEqual(payload["details"].startswith("Spike Rush // 7 - 3"), True)

    def test_empty_game_mode_warns_and_uses_real(self):
        ov = RpcOverrides.from_config(_section(game_mode="   "))
        self.assertIsNone(ov.game_mode_display)
        payload = self._build(_menus_presence(), {"game_mode": "   "})
        self.assertEqual(payload["details"], " Lobby - Competitive")

    def test_from_config_parses_game_mode_real(self):
        ov = RpcOverrides.from_config(_section(game_mode="real"))
        self.assertIsNone(ov.game_mode_display)

    def test_override_does_not_touch_other_fields(self):
        payload = self._build(
            _menus_presence(),
            {"game_mode": "Unrated", "rank": "Immortal 1",
             "party_size": "4", "party_max": "5"},
        )
        self.assertEqual(payload["state"], "Open Party (4 of 5)")
        self.assertEqual(payload["small_image"], "24")
        self.assertEqual(payload["details"], " Lobby - Unrated")

    def test_real_menus_card_unchanged_when_no_override(self):
        payload = self._build(_menus_presence(), {})
        self.assertEqual(payload["details"], " Lobby - Competitive")
        self.assertIsNone(payload["game_mode_display"]) if "game_mode_display" in payload else None


if __name__ == "__main__":
    unittest.main()
