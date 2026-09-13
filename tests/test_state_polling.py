"""Contract tests for src/state_polling.py (adaptive tolerant polling).

The module is built in parallel to this test file against the agreed
contract:
    derive_game_state(private_presence) -> str|None
    next_poll_seconds(game_state, base_cooldown) -> int
    should_declare_disconnected(consecutive_failures, threshold=3) -> bool
No I/O, pure functions only.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.state_polling import (
    derive_game_state,
    next_poll_seconds,
    should_declare_disconnected,
)


class DeriveGameStateTests(unittest.TestCase):
    def test_nested_structure(self):
        presence = {"matchPresenceData": {"sessionLoopState": "MENUS"}}
        self.assertEqual(derive_game_state(presence), "MENUS")

    def test_flat_structure(self):
        self.assertEqual(derive_game_state({"sessionLoopState": "PREGAME"}), "PREGAME")

    def test_falsy_inputs_return_none(self):
        self.assertIsNone(derive_game_state(None))
        self.assertIsNone(derive_game_state({}))
        self.assertIsNone(derive_game_state(""))

    def test_unknown_structure_returns_none(self):
        self.assertIsNone(derive_game_state({"somethingElse": 1}))


class NextPollSecondsTests(unittest.TestCase):
    def test_active_states_poll_fast(self):
        self.assertEqual(next_poll_seconds("PREGAME", 10), 2)
        self.assertEqual(next_poll_seconds("INGAME", 10), 2)

    def test_other_states_use_cooldown(self):
        self.assertEqual(next_poll_seconds("MENUS", 10), 10)
        self.assertEqual(next_poll_seconds("DISCONNECTED", 10), 10)

    def test_cooldown_floor_is_two(self):
        self.assertEqual(next_poll_seconds("MENUS", 0), 2)
        self.assertEqual(next_poll_seconds("MENUS", -5), 2)
        self.assertEqual(next_poll_seconds("MENUS", 1), 2)

    def test_odd_cooldown_values(self):
        self.assertEqual(next_poll_seconds("MENUS", 3), 3)
        self.assertEqual(next_poll_seconds("MENUS", None), 2)


class ShouldDeclareDisconnectedTests(unittest.TestCase):
    def test_below_threshold_never_declares(self):
        self.assertFalse(should_declare_disconnected(0))
        self.assertFalse(should_declare_disconnected(1))
        self.assertFalse(should_declare_disconnected(2))

    def test_threshold_declares(self):
        self.assertTrue(should_declare_disconnected(3))
        self.assertTrue(should_declare_disconnected(5))
        self.assertTrue(should_declare_disconnected(100))

    def test_custom_threshold(self):
        self.assertFalse(should_declare_disconnected(4, threshold=5))
        self.assertTrue(should_declare_disconnected(5, threshold=5))


if __name__ == "__main__":
    unittest.main()
