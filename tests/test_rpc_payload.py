"""Contract tests for the pure Discord presence payload builder
(src/rpc_payload.py: RpcOverrides + build_presence_payload), the shared
home for every Discord-card appearance rule.

All inputs are stubs: no threads, no Discord, no network. Run with the
py launcher from the repo root."""
import copy
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.constants import DEFAULT_CONFIG, NUMBERTORANKS
from src.rpc_payload import RpcOverrides, build_presence_payload


def _tier_for(name):
    """Tier index whose plain rank name matches (case-insensitive).
    NUMBERTORANKS entries are colr-wrapped, so ANSI escapes are stripped
    first (same approach as src/rpc_payload._plain_rank_names)."""
    ansi = re.compile(r"(?:\x1B[@-_]|[\x80-\x9F])[0-?]*[ -/]*[@-~]")
    wanted = name.lower()
    for index, entry in enumerate(NUMBERTORANKS):
        plain = ansi.sub("", str(entry)).strip().lower()
        if plain == wanted:
            return index
    raise AssertionError(f"rank name not in NUMBERTORANKS: {name!r}")


_ALL_REAL_SECTION = {"rank": "real", "agent": "real", "party_size": "real",
                     "party_max": "real", "score_ally": "real",
                     "score_enemy": "real"}

_MAP_DICT = {"/game/maps/ascent/ascent": "Ascent",
             "/game/maps/shootingrange/shootingrange": "The Range"}
_GAMEMODES = {"competitive": "Competitive"}


class _StubColors:
    agent_dict = {"jett": "Jett", "reyna": "Reyna", "phoenix": "Phoenix"}


class _StubLog:
    def __init__(self):
        self.lines = []

    def __call__(self, message):
        self.lines.append(message)


def _ingame_presence(nested=True, in_range=False):
    match_map = "shootingrange" if in_range else "ascent"
    presence = {
        "isValid": True,
        "sessionLoopState": "INGAME",
        "matchMap": f"/Game/Maps/{match_map.upper()}/{match_map.upper()}",
        "partySize": 2, "maxPartySize": 5,
        "partyAccessibility": "OPEN", "partyState": "MATCHMADE",
        "provisioningFlow": "Matchmaking",
        "queueId": "competitive",
        "partyOwnerMatchScoreAllyTeam": 7,
        "partyOwnerMatchScoreEnemyTeam": 5,
    }
    if nested:
        presence = {
            "isValid": True,
            "matchPresenceData": {
                "sessionLoopState": "INGAME",
                "matchMap": presence["matchMap"],
            },
            "partyPresenceData": {
                "partySize": 2, "maxPartySize": 5,
                "partyAccessibility": "OPEN", "partyState": "MATCHMADE",
            },
            "provisioningFlow": "Matchmaking",
            "queueId": "competitive",
            "partyOwnerMatchScoreAllyTeam": 7,
            "partyOwnerMatchScoreEnemyTeam": 5,
        }
    return presence


def _menus_presence(nested=True):
    presence = {
        "isValid": True,
        "sessionLoopState": "MENUS",
        "partySize": 2, "maxPartySize": 5,
        "partyAccessibility": "OPEN", "partyState": "MATCHMADE",
        "queueId": "competitive",
    }
    if nested:
        presence = {
            "isValid": True,
            "matchPresenceData": {"sessionLoopState": "MENUS"},
            "partyPresenceData": {
                "partySize": 2, "maxPartySize": 5,
                "partyAccessibility": "OPEN", "partyState": "MATCHMADE",
            },
            "queueId": "competitive",
        }
    return presence


def _pregame_presence(nested=True):
    presence = {
        "isValid": True,
        "sessionLoopState": "PREGAME",
        "matchMap": "/Game/Maps/Ascent/Ascent",
        "partySize": 2, "maxPartySize": 5,
        "partyAccessibility": "OPEN", "partyState": "MATCHMADE",
        "queueId": "competitive",
    }
    if nested:
        presence = {
            "isValid": True,
            "matchPresenceData": {
                "sessionLoopState": "PREGAME",
                "matchMap": presence["matchMap"],
            },
            "partyPresenceData": {
                "partySize": 2, "maxPartySize": 5,
                "partyAccessibility": "OPEN", "partyState": "MATCHMADE",
            },
            "queueId": "competitive",
        }
    return presence


_DATA_REAL = {"agent": "reyna", "rank": str(_tier_for("Immortal 3")),
              "rank_name": "Immortal 3"}
_COLORS = _StubColors()


def _overrides(section=None):
    cfg = dict(DEFAULT_CONFIG)
    cfg["rpc_overrides"] = dict(_ALL_REAL_SECTION, **(section or {}))
    return RpcOverrides.from_config(cfg, agent_dict=_StubColors.agent_dict)


class AllRealIngamingTests(unittest.TestCase):
    def test_flat_ingame_all_real(self):
        payload, _, _ = build_presence_payload(
            _ingame_presence(nested=False), dict(_DATA_REAL),
            _overrides(), _MAP_DICT, _GAMEMODES, _COLORS,
        )
        # agreed swap: corner is the rank badge, agent moved to the text
        self.assertEqual(payload["small_image"], _DATA_REAL["rank"])
        self.assertEqual(payload["small_text"], "Immortal 3")
        self.assertEqual(payload["details"], "Competitive // 7 - 5 · Reyna")
        self.assertEqual(payload["state"], "In a Party (2 of 5)")
        self.assertEqual(payload["large_image"], "splash_ascent_square")

    def test_nested_ingame_all_real(self):
        payload, _, _ = build_presence_payload(
            _ingame_presence(nested=True), dict(_DATA_REAL),
            _overrides(), _MAP_DICT, _GAMEMODES, _COLORS,
        )
        self.assertEqual(payload["small_image"], _DATA_REAL["rank"])
        self.assertEqual(payload["details"], "Competitive // 7 - 5 · Reyna")


class RankOverrideTests(unittest.TestCase):
    TIER = str(_tier_for("Diamond 2"))

    def test_rank_override_applies_to_all_three_corners(self):
        overrides = _overrides({"rank": "Diamond 2"})
        for presence in (_menus_presence(), _pregame_presence(),
                         _ingame_presence(nested=False)):
            payload, _, _ = build_presence_payload(
                presence, dict(_DATA_REAL), overrides,
                _MAP_DICT, _GAMEMODES, _COLORS,
            )
            self.assertEqual(payload["small_image"], self.TIER)
            self.assertEqual(payload["small_text"], "Diamond 2")

    def test_rank_override_with_case_and_spaces_still_resolves(self):
        overrides = _overrides({"rank": "  diamond 2  "})
        payload, _, _ = build_presence_payload(
            _ingame_presence(nested=False), dict(_DATA_REAL),
            overrides, _MAP_DICT, _GAMEMODES, _COLORS,
        )
        self.assertEqual(payload["small_image"], self.TIER)


class AgentOverrideTests(unittest.TestCase):
    def test_agent_override_changes_only_the_details_text(self):
        overrides = _overrides({"agent": "Phoenix"})
        payload, _, _ = build_presence_payload(
            _ingame_presence(nested=False), dict(_DATA_REAL),
            overrides, _MAP_DICT, _GAMEMODES, _COLORS,
        )
        self.assertEqual(payload["details"], "Competitive // 7 - 5 · Phoenix")
        self.assertEqual(payload["small_image"], _DATA_REAL["rank"])

    def test_agent_override_does_not_touch_menus_or_pregame(self):
        overrides = _overrides({"agent": "Phoenix"})
        menus, _, _ = build_presence_payload(
            _menus_presence(), dict(_DATA_REAL), overrides,
            _MAP_DICT, _GAMEMODES, _COLORS,
        )
        self.assertEqual(menus["small_text"], "Immortal 3")


class PartyOverrideTests(unittest.TestCase):
    def test_party_numbers_apply_to_all_three_state_lines(self):
        overrides = _overrides({"party_size": 4, "party_max": 5})
        expected_states = ["Open Party (4 of 5)",
                           "In a Party (4 of 5)",
                           "In a Party (4 of 5)"]
        for presence, expected_state in zip(
                (_menus_presence(), _pregame_presence(),
                 _ingame_presence(nested=False)), expected_states):
            payload, _, _ = build_presence_payload(
                presence, dict(_DATA_REAL), overrides,
                _MAP_DICT, _GAMEMODES, _COLORS,
            )
            self.assertEqual(payload["state"], expected_state)


class ScoreOverrideTests(unittest.TestCase):
    def test_score_overrides_apply_to_ingame_details_only(self):
        overrides = _overrides({"score_ally": 12, "score_enemy": 3})
        ingame, _, _ = build_presence_payload(
            _ingame_presence(nested=False), dict(_DATA_REAL),
            overrides, _MAP_DICT, _GAMEMODES, _COLORS,
        )
        self.assertEqual(ingame["details"], "Competitive // 12 - 3 · Reyna")
        menus, _, _ = build_presence_payload(
            _menus_presence(), dict(_DATA_REAL), overrides,
            _MAP_DICT, _GAMEMODES, _COLORS,
        )
        self.assertEqual(menus["details"], " Lobby - Competitive")
        self.assertNotIn("12", menus["details"])


class InvalidValueTests(unittest.TestCase):
    def test_invalid_rank_warns_and_falls_back_to_data_rank(self):
        log = _StubLog()
        cfg = dict(DEFAULT_CONFIG)
        cfg["rpc_overrides"] = dict(_ALL_REAL_SECTION, rank="NotARank")
        overrides = RpcOverrides.from_config(cfg, log, _StubColors.agent_dict)
        self.assertIsNone(overrides.rank_tier)
        payload, _, _ = build_presence_payload(
            _ingame_presence(nested=False), dict(_DATA_REAL),
            overrides, _MAP_DICT, _GAMEMODES, _COLORS,
        )
        self.assertEqual(payload["small_image"], _DATA_REAL["rank"])
        self.assertTrue(any("NotARank" in line for line in log.lines))

    def test_unknown_agent_warns_and_details_keep_base_text(self):
        log = _StubLog()
        cfg = dict(DEFAULT_CONFIG)
        cfg["rpc_overrides"] = {"agent": "NotAnAgent"}
        overrides = RpcOverrides.from_config(cfg, log, _StubColors.agent_dict)
        self.assertIsNone(overrides.agent_display)
        self.assertTrue(any("NotAnAgent" in line for line in log.lines))

    def test_non_int_party_warns_and_stays_real(self):
        log = _StubLog()
        cfg = dict(DEFAULT_CONFIG)
        cfg["rpc_overrides"] = {"party_size": "abc"}
        overrides = RpcOverrides.from_config(cfg, log, _StubColors.agent_dict)
        self.assertIsNone(overrides.party_size)
        self.assertTrue(any("party_size" in line for line in log.lines))

    def test_unknown_section_keys_are_ignored(self):
        log = _StubLog()
        cfg = dict(DEFAULT_CONFIG)
        cfg["rpc_overrides"] = {"rank": "real", "bogus": 1}
        overrides = RpcOverrides.from_config(cfg, log, _StubColors.agent_dict)
        self.assertEqual(overrides.rank_tier, None)
        self.assertFalse(any("bogus" in line for line in log.lines))


class RangeVariantTests(unittest.TestCase):
    def test_range_shows_rank_corner_and_agent_text(self):
        overrides = _overrides({"rank": "Diamond 2"})
        payload, _, _ = build_presence_payload(
            _ingame_presence(nested=False, in_range=True), dict(_DATA_REAL),
            overrides, _MAP_DICT, _GAMEMODES, _COLORS,
        )
        self.assertEqual(payload["details"], "in Range · Reyna")
        self.assertEqual(payload["small_image"], str(_tier_for("Diamond 2")))


class TimerResetTests(unittest.TestCase):
    def test_timer_resets_when_state_changes(self):
        payload, loop_state, start = build_presence_payload(
            _ingame_presence(nested=False), dict(_DATA_REAL),
            _overrides(), _MAP_DICT, _GAMEMODES, _COLORS,
            last_loop_state="MENUS", start_time=111.0, now=222.5,
        )
        self.assertEqual(start, 222.5)
        self.assertEqual(loop_state, "INGAME")

    def test_timer_kept_when_state_unchanged(self):
        payload, loop_state, start = build_presence_payload(
            _ingame_presence(nested=False), dict(_DATA_REAL),
            _overrides(), _MAP_DICT, _GAMEMODES, _COLORS,
            last_loop_state="INGAME", start_time=111.0, now=222.5,
        )
        self.assertEqual(start, 111.0)


class MissingRankDataTests(unittest.TestCase):
    """None-safety: missing real rank data must omit the corner badge
    (payload small_image/small_text None, dropped by rpc.py's None-filter)
    instead of emitting the invalid asset name "None" (log-570 evidence)."""

    def test_missing_rank_and_name_omit_corner(self):
        payload, _, _ = build_presence_payload(
            _ingame_presence(nested=False), {"agent": "reyna"},
            _overrides(), _MAP_DICT, _GAMEMODES, _COLORS,
        )
        self.assertIsNone(payload["small_image"])
        self.assertIsNone(payload["small_text"])
        self.assertIn("· Reyna", payload["details"])

    def test_rank_override_applies_even_without_data_rank(self):
        overrides = _overrides({"rank": "Diamond 2"})
        payload, _, _ = build_presence_payload(
            _menus_presence(), {"agent": "reyna"},
            overrides, _MAP_DICT, _GAMEMODES, _COLORS,
        )
        self.assertEqual(payload["small_image"], str(_tier_for("Diamond 2")))
        self.assertEqual(payload["small_text"], "Diamond 2")

    def test_real_rank_present_pins_badge_and_hover(self):
        payload, _, _ = build_presence_payload(
            _menus_presence(), dict(_DATA_REAL), _overrides(),
            _MAP_DICT, _GAMEMODES, _COLORS,
        )
        self.assertEqual(payload["small_image"], _DATA_REAL["rank"])
        self.assertEqual(payload["small_text"], "Immortal 3")


class EmptyPresenceSkipTests(unittest.TestCase):
    def test_invalid_presence_returns_none_payload(self):
        payload, _, _ = build_presence_payload(
            {"isValid": False}, dict(_DATA_REAL), _overrides(),
            _MAP_DICT, _GAMEMODES, _COLORS,
        )
        self.assertIsNone(payload)


class MenuIdleVariantTests(unittest.TestCase):
    def test_idle_flag_swaps_to_yellow_idle_icon(self):
        presence = _menus_presence()
        presence["isIdle"] = True
        payload, _, _ = build_presence_payload(
            presence, dict(_DATA_REAL), _overrides(),
            _MAP_DICT, _GAMEMODES, _COLORS,
        )
        self.assertEqual(payload["large_image"], "game_icon_yellow")
        self.assertEqual(payload["large_text"], "VALORANT - Idle")

    def test_online_lobby_keeps_normal_icon(self):
        payload, _, _ = build_presence_payload(
            _menus_presence(), dict(_DATA_REAL), _overrides(),
            _MAP_DICT, _GAMEMODES, _COLORS,
        )
        self.assertEqual(payload["large_image"], "game_icon")
        self.assertEqual(payload["large_text"], "VALORANT - Online")


class ClosedPartyWordingTests(unittest.TestCase):
    def test_closed_party_wording_in_menus(self):
        presence = _menus_presence()
        presence["partyPresenceData"]["partyAccessibility"] = "CLOSED"
        payload, _, _ = build_presence_payload(
            presence, dict(_DATA_REAL), _overrides(),
            _MAP_DICT, _GAMEMODES, _COLORS,
        )
        self.assertEqual(payload["state"], "Closed Party (2 of 5)")


class CustomGameLabelTests(unittest.TestCase):
    def test_custom_game_label_in_menus_and_ingame(self):
        for base_presence in (_menus_presence(), _ingame_presence(nested=False)):
            presence = dict(base_presence)
            if "partyPresenceData" in presence:
                presence["partyPresenceData"]["partyState"] = "CUSTOM_GAME_SETUP"
            presence["provisioningFlow"] = "CustomGame"
            payload, _, _ = build_presence_payload(
                presence, dict(_DATA_REAL), _overrides(),
                _MAP_DICT, _GAMEMODES, _COLORS,
            )
            self.assertIn("Custom Game", payload["details"])


class ScorePassthroughTests(unittest.TestCase):
    def test_real_scores_flow_into_ingame_details(self):
        payload, _, _ = build_presence_payload(
            _ingame_presence(nested=False), dict(_DATA_REAL), _overrides(),
            _MAP_DICT, _GAMEMODES, _COLORS,
        )
        self.assertEqual(payload["details"], "Competitive // 7 - 5 · Reyna")


if __name__ == "__main__":
    unittest.main()
