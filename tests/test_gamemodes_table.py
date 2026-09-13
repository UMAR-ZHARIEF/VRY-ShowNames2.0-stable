"""Guard tests for the hand-written gamemodes table (src.constants.gamemodes).

The table maps Valorant queue ids to the display names shown on cards and
in the Discord presence. It is hand-maintained (Riot has no fetchable
queue->name source), so these tests pin the entries that real lobbies
depend on and the formatting conventions the lookup relies on
(keys are lowercase queue ids, values are non-empty display names).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.constants import gamemodes

ESSENTIAL_IDS = (
    "competitive",
    "unrated",
    "deathmatch",
    "swiftplay",
    "spikerush",
    "hurm",
    "ggteam",
    "premier",
    "retake",
    "skirmish",
)


class GamemodesTableTests(unittest.TestCase):
    def test_premier_present(self):
        self.assertEqual(gamemodes.get("premier"), "Premier")

    def test_retake_and_skirmish_display_names(self):
        self.assertEqual(gamemodes.get("retake"), "Retake")
        self.assertEqual(gamemodes.get("skirmish"), "Skirmish")

    def test_essential_ids_present(self):
        for queue_id in ESSENTIAL_IDS:
            with self.subTest(queue_id=queue_id):
                self.assertIn(queue_id, gamemodes)

    def test_every_value_is_non_empty_string(self):
        for key, value in gamemodes.items():
            with self.subTest(key=key):
                self.assertIsInstance(value, str)
                self.assertTrue(value.strip())

    def test_every_key_is_lowercase(self):
        for key in gamemodes:
            with self.subTest(key=key):
                self.assertEqual(key, key.lower())


if __name__ == "__main__":
    unittest.main()
