"""Contract tests for state_polling.pregame_draw_decision.

Contract (STRICT single print per agent select): "wait" only while the
loadouts probe is still alive ("polling" or "partial") AND fewer than five
enemies are found AND less than 20 seconds have elapsed in the state.
Every other combination returns "draw" (five enemies found; probe complete
or errored; or the 20-second settle cap reached). The function is pure.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.state_polling import pregame_draw_decision


class PregameDrawDecisionTests(unittest.TestCase):
    def test_wait_while_polling_and_enemies_still_missing(self):
        self.assertEqual(pregame_draw_decision(0.0, 0, "polling"), "wait")
        self.assertEqual(pregame_draw_decision(5.0, 2, "polling"), "wait")
        self.assertEqual(pregame_draw_decision(4.9, 4, "polling"), "wait")

    def test_wait_while_partial_and_enemies_still_missing(self):
        self.assertEqual(pregame_draw_decision(3.0, 1, "partial"), "wait")
        self.assertEqual(pregame_draw_decision(19.9, 4, "partial"), "wait")

    def test_draw_at_five_enemies_regardless_of_status(self):
        for status in ("polling", "partial", "complete", "error", "idle"):
            self.assertEqual(pregame_draw_decision(1.0, 5, status), "draw")

    def test_draw_when_probe_complete_or_error_even_with_missing_enemies(self):
        self.assertEqual(pregame_draw_decision(2.0, 4, "complete"), "draw")
        self.assertEqual(pregame_draw_decision(2.0, 0, "error"), "draw")

    def test_draw_when_probe_idle_so_caller_does_not_wait(self):
        # "idle" is not in the wait set; the flag-off convention makes the
        # caller pass enemy_count=5, but the helper itself must also draw.
        self.assertEqual(pregame_draw_decision(2.0, 4, "idle"), "draw")

    def test_draw_at_twenty_second_cap_while_still_polling(self):
        self.assertEqual(pregame_draw_decision(20.0, 4, "polling"), "draw")
        self.assertEqual(pregame_draw_decision(20.1, 4, "partial"), "draw")

    def test_boundary_just_before_and_at_the_cap(self):
        self.assertEqual(pregame_draw_decision(19.99, 4, "polling"), "wait")
        self.assertEqual(pregame_draw_decision(20.0, 4, "polling"), "draw")

    def test_low_enemy_counts_wait_inside_window(self):
        for count in (0, 1, 2, 3, 4):
            self.assertEqual(pregame_draw_decision(10.0, count, "polling"), "wait")


if __name__ == "__main__":
    unittest.main()
