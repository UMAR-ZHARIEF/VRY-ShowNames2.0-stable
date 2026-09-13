"""Tests for src.rpc_payload: RpcOverrides parsing and build_presence_payload.

Pins the Discord appearance overrides contract: every override applies in
its declared state only; "real" (None after parsing) keeps today's values;
invalid values fall back to real with a named warning; the in-match corner
always carries the rank badge and the agent appears in the details text.
"""
import unittest

import os
import sys
from types import SimpleNamespace
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.rpc_payload import RpcOverrides, build_presence_payload


def make_colors():
    class Colors:
        agent_dict = {"sova": "Sova", "reyna": "Reyna"}
    return Colors()


MAP_DICT = {
    "/game/maps/ascent": "Ascent",
    "/game/maps/therange": "The Range",
}

GAMEMODES = {"competitive": "Competitive", "unrated": "Unrated"}

FLAT_MENUS = {
    "isValid": True,
    "sessionLoopState": "MENUS",
    "partySize": 2,
    "maxPartySize": 5,
    "partyAccessibility": "OPEN",
    "queueId": "competitive",
}

FLAT_INGAME = {
    "isValid": True,
    "sessionLoopState": "INGAME",
    "matchMap": "/Game/Maps/Ascent",
    "partyOwnerMatchScoreAllyTeam": 4,
    "partyOwnerMatchScoreEnemyTeam": 9,
    "queueId": "competitive",
}

DATA_RANK = {"rank": 19, "rank_name": "Diamond 2", "agent": "sova"}


class FromConfigTests(unittest.TestCase):
    def test_defaults_parse_to_all_real(self):
        o = RpcOverrides.from_config({"rpc_overrides": {}}, log=None)
        self.assertIsNone(o.rank_tier)
        self.assertIsNone(o.agent_display)
        self.assertIsNone(o.party_size)
        self.assertIsNone(o.score_ally)

    def test_rank_name_resolves_to_tier_and_display(self):
        log = []
        o = RpcOverrides.from_config(
            {"rpc_overrides": {"rank": "diamond 2"}}, log=log.append)
        self.assertEqual(o.rank_tier, 19)
        self.assertEqual(o.rank_display, "Diamond 2")

    def test_unknown_rank_warns_and_falls_back_to_real(self):
        log = []
        o = RpcOverrides.from_config(
            {"rpc_overrides": {"rank": "Not A Rank"}}, log=log.append)
        self.assertIsNone(o.rank_tier)
        self.assertTrue(any('"rank"' in line for line in log))

    def test_agent_resolves_case_insensitively(self):
        o = RpcOverrides.from_config(
            {"rpc_overrides": {"agent": "REYNA"}},
            log=None, agent_dict=make_colors().agent_dict)
        self.assertEqual(o.agent_display, "Reyna")

    def test_unknown_agent_without_table_fails_safe_to_real(self):
        log = []
        o = RpcOverrides.from_config(
            {"rpc_overrides": {"agent": "Reyna"}}, log=log.append)
        self.assertIsNone(o.agent_display)
        self.assertTrue(any('"agent"' in line for line in log))

    def test_party_and_score_parse_integers(self):
        o = RpcOverrides.from_config(
            {"rpc_overrides": {"party_size": "4", "party_max": 5,
                               "score_ally": "12", "score_enemy": "3"}},
            log=None)
        self.assertEqual(o.party_size, 4)
        self.assertEqual(o.party_max, 5)
        self.assertEqual(o.score_ally, 12)
        self.assertEqual(o.score_enemy, 3)

    def test_attribute_style_object_parses_like_dict(self):
        # The app passes the Config object, which exposes sections as
        # attributes; parsing must match the dict interface exactly.
        overrides = {"rank": "diamond 2", "party_size": "4"}
        from_dict = RpcOverrides.from_config(
            {"rpc_overrides": overrides}, log=None)
        from_attr = RpcOverrides.from_config(
            SimpleNamespace(rpc_overrides=overrides), log=None)
        self.assertEqual(from_dict.rank_tier, from_attr.rank_tier)
        self.assertEqual(from_dict.agent_display, from_attr.agent_display)
        self.assertEqual(from_dict.party_size, from_attr.party_size)
        self.assertEqual(from_dict.party_max, from_attr.party_max)
        self.assertEqual(from_dict.score_ally, from_attr.score_ally)
        self.assertEqual(from_dict.score_enemy, from_attr.score_enemy)

    def test_attribute_style_without_section_defaults_to_real(self):
        o = RpcOverrides.from_config(SimpleNamespace(), log=None)
        self.assertIsNone(o.rank_tier)
        self.assertIsNone(o.agent_display)
        self.assertIsNone(o.party_size)
        self.assertIsNone(o.party_max)
        self.assertIsNone(o.score_ally)
        self.assertIsNone(o.score_enemy)

    def test_bad_number_warns_and_falls_back_to_real(self):
        log = []
        o = RpcOverrides.from_config(
            {"rpc_overrides": {"score_ally": "lots"}}, log=log.append)
        self.assertIsNone(o.score_ally)
        self.assertTrue(any('"score_ally"' in line for line in log))


class BuildPayloadTests(unittest.TestCase):
    def build(self, presence, data, overrides=None):
        o = overrides or RpcOverrides()
        return build_presence_payload(
            presence, data, o, MAP_DICT, GAMEMODES, make_colors(),
            last_loop_state=None, start_time=100.0, now=200.0)

    def test_ingame_rank_badge_in_corner_and_agent_in_details(self):
        payload, _, _ = self.build(FLAT_INGAME, DATA_RANK)
        self.assertEqual(payload["small_image"], "19")
        self.assertEqual(payload["small_text"], "Diamond 2")
        self.assertEqual(payload["details"], "Competitive // 4 - 9 · Sova")

    def test_ingame_agent_override_swaps_text(self):
        payload, _, _ = self.build(
            FLAT_INGAME, DATA_RANK,
            RpcOverrides(agent_display="Reyna"))
        self.assertEqual(payload["details"], "Competitive // 4 - 9 · Reyna")

    def test_ingame_score_overrides_apply_only_in_match(self):
        payload, _, _ = self.build(
            FLAT_INGAME, DATA_RANK, RpcOverrides(score_ally=12, score_enemy=3))
        self.assertEqual(payload["details"], "Competitive // 12 - 3 · Sova")
        menus, _, _ = self.build(
            FLAT_MENUS, DATA_RANK, RpcOverrides(score_ally=12, score_enemy=3))
        self.assertNotIn("12", menus["details"])

    def test_ingame_real_agent_and_scores_without_overrides(self):
        payload, _, _ = self.build(FLAT_INGAME, DATA_RANK)
        self.assertEqual(payload["details"], "Competitive // 4 - 9 · Sova")

    def test_ingame_agent_missing_drops_text_suffix(self):
        payload, _, _ = self.build(FLAT_INGAME, {"rank": 19})
        self.assertEqual(payload["details"], "Competitive // 4 - 9")
        self.assertEqual(payload["small_image"], "19")

    def test_pregame_rank_override_applies(self):
        presence = dict(FLAT_MENUS, sessionLoopState="PREGAME")
        payload, _, _ = self.build(
            presence, DATA_RANK, RpcOverrides(rank_tier=27, rank_display="Radiant"))
        self.assertEqual(payload["small_image"], "27")
        self.assertEqual(payload["small_text"], "Radiant")
        self.assertEqual(payload["details"], "Agent Select - Competitive")

    def test_menus_party_override_applies(self):
        payload, _, _ = self.build(
            FLAT_MENUS, DATA_RANK, RpcOverrides(party_size=4, party_max=5))
        self.assertEqual(payload["state"], "Open Party (4 of 5)")

    def test_party_override_applies_in_ingame_state_line(self):
        payload, _, _ = self.build(
            FLAT_INGAME, DATA_RANK, RpcOverrides(party_size=4, party_max=5))
        self.assertEqual(payload["state"], "In a Party (4 of 5)")

    def test_state_change_resets_timer(self):
        result = self.build(FLAT_MENUS, DATA_RANK)
        self.assertEqual(result[2], 200.0)
        ingame = dict(FLAT_INGAME)
        payload, state, start = build_presence_payload(
            ingame, DATA_RANK, RpcOverrides(), MAP_DICT, GAMEMODES,
            make_colors(), last_loop_state="MENUS", start_time=100.0, now=999.0)
        self.assertEqual(start, 999.0)

    def test_invalid_presence_returns_none_and_keeps_state(self):
        payload, state, start = build_presence_payload(
            {"isValid": False}, DATA_RANK, RpcOverrides(), MAP_DICT, GAMEMODES,
            make_colors(), last_loop_state="MENUS", start_time=100.0, now=999.0)
        self.assertIsNone(payload)
        self.assertEqual(state, "MENUS")
        self.assertEqual(start, 100.0)

    def test_range_variant_keeps_rank_corner_and_agent_text(self):
        presence = dict(FLAT_INGAME, matchMap="/Game/Maps/TheRange")
        payload, _, _ = self.build(presence, DATA_RANK)
        self.assertEqual(payload["details"], "in Range · Sova")
        self.assertEqual(payload["large_image"], "splash_range_square")
        self.assertEqual(payload["small_image"], "19")


if __name__ == "__main__":
    unittest.main()
