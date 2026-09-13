"""Seam guard: valoApiSkins is a parsed dict, never a Response.

Phase 1 made main.py pass the skins payload as a parsed dict
(src/static_content.get_json). Consumers must index ["data"] instead of
calling .json(). These tests fail if anyone reintroduces .json() on the
parameter.
"""
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.colors import Colors
from src.Loadouts import Loadouts

FAKE_SKINS = {"data": [{"uuid": "x", "contentTierUuid": "t"}]}


class GetRgbColorFromSkinTests(unittest.TestCase):
    def _colors(self):
        colors = Colors(False, {}, {})
        colors.tier_dict = {"t": (1, 2, 3)}
        return colors

    def test_accepts_parsed_dict_and_returns_tier(self):
        colors = self._colors()
        self.assertEqual(colors.get_rgb_color_from_skin("x", FAKE_SKINS), (1, 2, 3))

    def test_unknown_skin_returns_none(self):
        colors = self._colors()
        self.assertIsNone(colors.get_rgb_color_from_skin("nope", FAKE_SKINS))

    def test_no_json_call_left_in_colors(self):
        source = inspect.getsource(Colors.get_rgb_color_from_skin)
        self.assertNotIn(".json()", source)

    def test_no_json_call_left_in_loadouts_get_match_loadouts(self):
        source = inspect.getsource(Loadouts.get_match_loadouts)
        self.assertNotIn(".json()", source)


if __name__ == "__main__":
    unittest.main()
