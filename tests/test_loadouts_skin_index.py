"""Skin/loadout catalog scan indexes in src/Loadouts.py must not change output.

W2 speedup package: the per-player/per-item nested catalog scans (weapons,
skins, chromas, buddies, sprays, agents, titles, player cards) were replaced
by O(1) lookup indexes built once per catalog payload. Presentation is
frozen, so these tests pin the exact outputs the old nested loops produced:
skin display names, the skinDisplayIcon fallback chain (chroma displayIcon ->
chroma fullRender -> skin displayIcon -> skin levels[0] displayIcon), the
Standard/Melee weapon-icon override, the phantom elif semantics, and the
"catalog miss leaves the key absent" behavior. Expected values are derived
by hand from the synthetic fixtures below.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from colr import color  # noqa: E402

from src import Loadouts as loadouts_module  # noqa: E402
from src.colors import Colors  # noqa: E402
from src.constants import sockets  # noqa: E402
from src.Loadouts import Loadouts  # noqa: E402

SKIN_SOCKET = sockets["skin"]
CHROMA_SOCKET = sockets["skin_chroma"]
BUDDY_SOCKET = sockets["skin_buddy"]
SPRAY_TYPE_ID = "d5f120f8-ff8c-4aac-92ea-f2b5acbe9475"
RED = (255, 0, 0)

VANDAL_UUID = "w-vandal"
PHANTOM_UUID = "w-phantom"

# Skin-level fixture: chroma displayIcon beats fullRender (pin fallback order).
ION_SKIN = {"uuid": "s-ion", "displayName": "Ion Vandal",
            "displayIcon": "ion-skin-icon.png",
            "chromas": [{"uuid": "c-ion", "displayIcon": "ion-chroma-icon.png",
                         "fullRender": "ion-chroma-full.png"}],
            "levels": [{"displayIcon": "ion-level-icon.png"}]}
# fullRender fallback: chroma displayIcon is None.
REAVER_SKIN = {"uuid": "s-reaver", "displayName": "Reaver Vandal",
               "displayIcon": "reaver-skin-icon.png",
               "chromas": [{"uuid": "c-reaver", "displayIcon": None,
                            "fullRender": "reaver-chroma-full.png"}],
               "levels": [{"displayIcon": "reaver-level-icon.png"}]}
# Standard override: chain yields the chroma fullRender, then the weapon icon
# overwrites it because the skin name starts with "Standard".
STANDARD_SKIN = {"uuid": "s-standard", "displayName": "Standard Vandal",
                 "displayIcon": "standard-skin-icon.png",
                 "chromas": [{"uuid": "c-standard", "displayIcon": None,
                              "fullRender": "standard-chroma-full.png"}],
                 "levels": [{"displayIcon": "standard-level-icon.png"}]}
# levels[0] fallback: chroma icons and skin displayIcon are all None.
PHANTOM_SKIN = {"uuid": "s-phantom", "displayName": "RGX Phantom",
                "displayIcon": None,
                "chromas": [{"uuid": "c-phantom", "displayIcon": None,
                             "fullRender": None}],
                "levels": [{"displayIcon": "phantom-level-icon.png"}]}

WEAPONS = {"data": [
    {"uuid": VANDAL_UUID, "displayName": "Vandal",
     "displayIcon": "vandal-icon.png",
     "skins": [ION_SKIN, REAVER_SKIN, STANDARD_SKIN]},
    {"uuid": PHANTOM_UUID, "displayName": "Phantom",
     "displayIcon": "phantom-icon.png",
     "skins": [PHANTOM_SKIN]},
]}

SKINS = {"data": [
    {"uuid": "s-ion", "displayName": "Ion Vandal",
     "contentTierUuid": "tier-exclusive"},
    {"uuid": "s-reaver", "displayName": "Reaver Vandal",
     "contentTierUuid": "tier-exclusive"},
    {"uuid": "s-standard", "displayName": "Standard Vandal",
     "contentTierUuid": "tier-exclusive"},
    {"uuid": "s-phantom", "displayName": "RGX Phantom",
     "contentTierUuid": "tier-exclusive"},
]}


class StubRequests:
    def __init__(self, loadouts):
        self.loadouts = loadouts

    def fetch(self, *args, **kwargs):
        return self.loadouts


class StubServer:
    def __init__(self):
        self.payloads = []

    def send_payload(self, kind, payload):
        self.payloads.append((kind, payload))


def make_loadouts(loadouts_payload):
    colors = Colors(False, {}, {})
    colors.tier_dict = {"tier-exclusive": RED}
    return Loadouts(StubRequests(loadouts_payload), (lambda *a: None),
                    colors, StubServer(), "Ascent")


def loadout(items):
    return {"Loadout": {"Items": items}}


def base_loadouts_payload(vandal_skin_id="s-ion", vandal_chroma_id="c-ion",
                          phantom_skin_id="s-phantom",
                          phantom_chroma_id="c-phantom"):
    return {"Loadouts": [
        loadout({
            VANDAL_UUID: {"Sockets": {
                SKIN_SOCKET: {"Item": {"ID": vandal_skin_id}},
                CHROMA_SOCKET: {"Item": {"ID": vandal_chroma_id}}}},
            PHANTOM_UUID: {"Sockets": {
                SKIN_SOCKET: {"Item": {"ID": phantom_skin_id}},
                CHROMA_SOCKET: {"Item": {"ID": phantom_chroma_id}}}},
        }),
        loadout({
            # uppercase skin ID on purpose: the old loop lowercased both sides
            VANDAL_UUID: {"Sockets": {
                SKIN_SOCKET: {"Item": {"ID": "S-ION"}},
                CHROMA_SOCKET: {"Item": {"ID": "C-ION"}}}},
            PHANTOM_UUID: {"Sockets": {
                SKIN_SOCKET: {"Item": {"ID": "s-phantom"}},
                CHROMA_SOCKET: {"Item": {"ID": "c-phantom"}}}},
        }),
    ]}


class GetMatchLoadoutsIndexTest(unittest.TestCase):
    """weaponChoose/phantom skin resolution via the name/uuid indexes."""

    PLAYERS = [
        {"Subject": "puuid-a", "TeamID": "Blue",
         "CharacterID": "",
         "PlayerIdentity": {"AccountLevel": 11, "PlayerTitleID": "",
                            "PlayerCardID": ""}},
        {"Subject": "puuid-b", "TeamID": "Blue",
         "CharacterID": "",
         "PlayerIdentity": {"AccountLevel": 22, "PlayerTitleID": "",
                            "PlayerCardID": ""}},
    ]

    def test_chosen_weapon_and_phantom_skins_resolve_identically(self):
        loadouts = make_loadouts(base_loadouts_payload())
        catalogs = make_catalogs()
        with mock.patch("src.static_content.get_json",
                        side_effect=lambda path: catalogs[path]):
            result = loadouts.get_match_loadouts(
                "match-1", self.PLAYERS, "Vandal", SKINS,
                               {"puuid-a": "PlayerA#1", "puuid-b": "PlayerB#2"})
        self.assertEqual(result[0], {
            "puuid-a": color("Ion", fore=RED),
            "puuid-b": color("Ion", fore=RED),  # uppercase socket ID matched
        })
        # old elif: with weaponChoose=Vandal the phantom weapon still fills
        # phantomLists via its own skin
        self.assertEqual(result[1], {
            "puuid-a": color("RGX", fore=RED),
            "puuid-b": color("RGX", fore=RED),
        })

    def test_phantom_as_weapon_choose_leaves_phantom_lists_empty(self):
        loadouts = make_loadouts(base_loadouts_payload())
        catalogs = make_catalogs()
        with mock.patch("src.static_content.get_json",
                        side_effect=lambda path: catalogs[path]):
            result = loadouts.get_match_loadouts(
                "match-1", self.PLAYERS, "Phantom", SKINS,
                               {"puuid-a": "PlayerA#1", "puuid-b": "PlayerB#2"})
        self.assertEqual(result[0], {
            "puuid-a": color("RGX", fore=RED),
            "puuid-b": color("RGX", fore=RED),
        })
        # old elif never fired when the chosen name was phantom itself
        self.assertEqual(result[1], {})

    def test_unknown_weapon_choose_matches_nothing(self):
        loadouts = make_loadouts(base_loadouts_payload())
        catalogs = make_catalogs()
        with mock.patch("src.static_content.get_json",
                        side_effect=lambda path: catalogs[path]):
            result = loadouts.get_match_loadouts(
                "match-1", self.PLAYERS, "Knife", SKINS,
                               {"puuid-a": "PlayerA#1", "puuid-b": "PlayerB#2"})
        self.assertEqual(result[0], {})
        self.assertEqual(result[1], {
            "puuid-a": color("RGX", fore=RED),
            "puuid-b": color("RGX", fore=RED),
        })

    def test_lowercase_choose_matches_cased_weapon_name(self):
        loadouts = make_loadouts(base_loadouts_payload())
        catalogs = make_catalogs()
        with mock.patch("src.static_content.get_json",
                        side_effect=lambda path: catalogs[path]):
            result = loadouts.get_match_loadouts(
                "match-1", self.PLAYERS, "vandal", SKINS,
                               {"puuid-a": "PlayerA#1", "puuid-b": "PlayerB#2"})
        self.assertEqual(result[0]["puuid-a"], color("Ion", fore=RED))


def item(skin_id, chroma_id, buddy_id=None):
    sockets_map = {SKIN_SOCKET: {"Item": {"ID": skin_id}},
                   CHROMA_SOCKET: {"Item": {"ID": chroma_id}}}
    if buddy_id is not None:
        sockets_map[BUDDY_SOCKET] = {"Item": {"ID": buddy_id}}
    return {"Sockets": sockets_map}


def make_catalogs(weapons=WEAPONS, buddies=None):
    return {
        "/v1/sprays": {"data": [{"uuid": "spray-1", "displayName": "Shards",
                                 "displayIcon": "spray-1-icon.png",
                                 "fullTransparentIcon": "spray-1-full.png"}]},
        "/v1/weapons": weapons,
        "/v1/buddies": buddies if buddies is not None else
        {"data": [{"uuid": "buddy-1", "displayIcon": "buddy-1-icon.png"}]},
        "/v1/agents": {"data": [{"uuid": "agent-jett", "displayName": "Jett",
                                 "displayIcon": "jett-icon.png"}]},
        "/v1/playertitles": {"data": [{"uuid": "title-1",
                                       "titleText": "Radiant"}]},
        "/v1/playercards": {"data": [{"uuid": "card-1",
                                      "largeArt": "card-1-large.png"}]},
    }


class ConvertTestCase(unittest.TestCase):
    def setUp(self):
        loadouts_module._INDEX_CACHE.clear()

    # helper: convert one player carrying the given items
    def convert(self, items, player=None, names=None, expressions=None):
        player = player or {"Subject": "puuid-a", "TeamID": "Blue",
                            "CharacterID": "agent-jett",
                            "PlayerIdentity": {"AccountLevel": 123,
                                               "PlayerTitleID": "title-1",
                                               "PlayerCardID": "card-1"}}
        names = names if names is not None else {"puuid-a": "PlayerA#123"}
        inventory = {"Loadouts": [{"Loadout": {
            "Items": items,
            "Expressions": {"AESSelections": expressions if expressions is not None
                            else [{"TypeID": SPRAY_TYPE_ID, "AssetID": "SPRAY-1"}]},
        }}]}
        patcher = mock.patch("src.static_content.get_json",
                             side_effect=lambda path: self.catalogs[path])
        patcher.start()
        self.addCleanup(patcher.stop)
        converter = make_loadouts({"Loadouts": []})
        result = converter.convertLoadoutToJsonArray(
            inventory, [player], "game", names)
        return result["Players"]["puuid-a"]


class ConvertGoldenLoadoutTest(ConvertTestCase):
    """Full per-player conversion output, byte-for-byte field for field."""

    def setUp(self):
        super().setUp()
        self.catalogs = make_catalogs()

    def test_full_player_dict_matches_old_nested_loop_output(self):
        state = self.convert({
            VANDAL_UUID: item("s-ion", "c-ion", buddy_id="buddy-1"),
            PHANTOM_UUID: item("s-phantom", "c-phantom"),
        })
        self.assertEqual(state, {
            "Name": "PlayerA#123",
            "Team": "Blue",
            "Sprays": {0: {"displayName": "Shards",
                           "displayIcon": "spray-1-icon.png",
                           "fullTransparentIcon": "spray-1-full.png"}},
            "Level": 123,
            "Title": "Radiant",
            "PlayerCard": "card-1-large.png",
            "AgentArtworkName": "JettArtwork",
            "Agent": "jett-icon.png",
            "Weapons": {
                VANDAL_UUID: {
                    "skin": "s-ion",
                    "skin_chroma": "c-ion",
                    "skin_buddy": "buddy-1",
                    "buddy_displayIcon": "buddy-1-icon.png",
                    "weapon": "Vandal",
                    "skinDisplayName": "Ion Vandal",
                    # chroma displayIcon beats fullRender: order pinned
                    "skinDisplayIcon": "ion-chroma-icon.png",
                },
                PHANTOM_UUID: {
                    "skin": "s-phantom",
                    "skin_chroma": "c-phantom",
                    "weapon": "Phantom",
                    "skinDisplayName": "RGX Phantom",
                    # all-None chain end: skin levels[0] displayIcon
                    "skinDisplayIcon": "phantom-level-icon.png",
                },
            },
        })
        # insertion order feeds the serialized website payload; the update
        # sequence must stay exactly as the old nested loops produced it
        self.assertEqual(list(state["Weapons"][VANDAL_UUID].keys()),
                         ["skin", "skin_chroma", "skin_buddy",
                          "buddy_displayIcon", "weapon", "skinDisplayName",
                          "skinDisplayIcon"])
        self.assertEqual(list(state.keys()),
                         ["Name", "Team", "Sprays", "Level", "Title",
                          "PlayerCard", "AgentArtworkName", "Agent",
                          "Weapons"])


class DisplayIconFallbackChainTest(ConvertTestCase):
    """Each branch of the None-icon chain, hand derived per fixture."""

    def setUp(self):
        super().setUp()
        self.catalogs = make_catalogs()

    def test_chroma_fullrender_used_when_displayicon_none(self):
        state = self.convert({VANDAL_UUID: item("s-reaver", "c-reaver")})
        entry = state["Weapons"][VANDAL_UUID]
        self.assertEqual(entry["skinDisplayName"], "Reaver Vandal")
        self.assertEqual(entry["skinDisplayIcon"], "reaver-chroma-full.png")
        # the skin-level icon must NOT win over the chroma fullRender
        self.assertNotEqual(entry["skinDisplayIcon"], "reaver-skin-icon.png")

    def test_standard_skin_overridden_with_weapon_icon(self):
        state = self.convert({VANDAL_UUID: item("s-standard", "c-standard")})
        entry = state["Weapons"][VANDAL_UUID]
        self.assertEqual(entry["skinDisplayName"], "Standard Vandal")
        # the chain set standard-chroma-full.png, the Standard override
        # replaces it with the weapon displayIcon
        self.assertEqual(entry["skinDisplayIcon"], "vandal-icon.png")

    def test_melee_named_skin_overridden_with_weapon_icon(self):
        melee_skin = {"uuid": "s-melee", "displayName": "Melee Fixture",
                      "displayIcon": "melee-skin-icon.png",
                      "chromas": [{"uuid": "c-melee", "displayIcon": None,
                                   "fullRender": "melee-chroma-full.png"}],
                      "levels": [{"displayIcon": "melee-level-icon.png"}]}
        melee_weapons = {"data": [
            {"uuid": "w-melee", "displayName": "Melee",
             "displayIcon": "melee-icon.png", "skins": [melee_skin]},
        ]}
        self.catalogs = make_catalogs(weapons=melee_weapons)
        state = self.convert({"w-melee": item("s-melee", "c-melee")})
        entry = state["Weapons"]["w-melee"]
        self.assertEqual(entry["skinDisplayName"], "Melee Fixture")
        self.assertEqual(entry["skinDisplayIcon"], "melee-icon.png")


class CatalogMissBehaviorTest(ConvertTestCase):
    """Catalog misses leave keys absent exactly like the old scan loops."""

    def setUp(self):
        super().setUp()
        self.catalogs = make_catalogs()

    def test_unknown_buddy_leaves_buddy_key_absent(self):
        state = self.convert({VANDAL_UUID: item("s-ion", "c-ion",
                                                buddy_id="buddy-unknown")})
        entry = state["Weapons"][VANDAL_UUID]
        self.assertNotIn("buddy_displayIcon", entry)

    def test_known_buddy_sets_display_icon(self):
        state = self.convert({VANDAL_UUID: item("s-ion", "c-ion",
                                                buddy_id="buddy-1")})
        self.assertEqual(
            state["Weapons"][VANDAL_UUID]["buddy_displayIcon"],
            "buddy-1-icon.png")

    def test_unknown_spray_leaves_empty_spray_entry(self):
        state = self.convert(
            {VANDAL_UUID: item("s-ion", "c-ion")},
            expressions=[{"TypeID": SPRAY_TYPE_ID, "AssetID": "SPRAY-UNKNOWN"}])
        self.assertEqual(state["Sprays"], {0: {}})

    def test_unknown_agent_title_card_leave_keys_absent(self):
        state = self.convert(
            {VANDAL_UUID: item("s-ion", "c-ion")},
            player={"Subject": "puuid-a", "TeamID": "Blue",
                    "CharacterID": "agent-none",
                    "PlayerIdentity": {"AccountLevel": 5,
                                       "PlayerTitleID": "title-none",
                                       "PlayerCardID": "card-none"}})
        self.assertNotIn("Title", state)
        self.assertNotIn("PlayerCard", state)
        self.assertNotIn("AgentArtworkName", state)
        self.assertNotIn("Agent", state)

    def test_unknown_weapon_item_keeps_predefined_sockets_only(self):
        # old loops: the predefined-socket fields are item-driven (always set),
        # while weapon/skin/chroma/buddy fields only come from catalog matches
        state = self.convert({"w-unknown": item("s-ion", "c-ion")})
        self.assertEqual(state["Weapons"],
                         {"w-unknown": {"skin": "s-ion",
                                        "skin_chroma": "c-ion"}})

    def test_known_weapon_unknown_skin_sets_weapon_name_only(self):
        state = self.convert({VANDAL_UUID: item("s-unknown", "c-unknown")})
        entry = state["Weapons"][VANDAL_UUID]
        self.assertEqual(entry["weapon"], "Vandal")
        self.assertNotIn("skinDisplayName", entry)
        self.assertNotIn("skinDisplayIcon", entry)


class HideNamesAgentLookupTest(ConvertTestCase):
    """hide_names Name branch resolves the agent displayName via the index."""

    def setUp(self):
        super().setUp()
        self.catalogs = make_catalogs()

    def test_agent_display_name_used_for_hidden_name(self):
        with mock.patch("src.Loadouts.hide_names", True):
            state = self.convert(
                {VANDAL_UUID: item("s-ion", "c-ion")}, names={})
        self.assertEqual(state["Name"], "Jett")


class IndexCacheIdentityTest(ConvertTestCase):
    """A fresh catalog object must rebuild the index, not reuse the old one."""

    def setUp(self):
        super().setUp()
        self.catalogs = make_catalogs()

    def test_replaced_buddy_catalog_object_changes_result(self):
        state = self.convert({VANDAL_UUID: item("s-ion", "c-ion",
                                                buddy_id="buddy-1")})
        self.assertEqual(
            state["Weapons"][VANDAL_UUID]["buddy_displayIcon"],
            "buddy-1-icon.png")
        # same uuid, brand-new buddy catalog object with a different icon
        self.catalogs = make_catalogs(
            buddies={"data": [{"uuid": "buddy-1",
                               "displayIcon": "buddy-1-NEW-icon.png"}]})
        state = self.convert({VANDAL_UUID: item("s-ion", "c-ion",
                                                buddy_id="buddy-1")})
        self.assertEqual(
            state["Weapons"][VANDAL_UUID]["buddy_displayIcon"],
            "buddy-1-NEW-icon.png")


if __name__ == "__main__":
    unittest.main()
