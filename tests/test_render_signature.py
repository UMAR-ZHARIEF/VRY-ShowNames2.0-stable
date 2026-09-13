"""Contract tests for the render-on-change helpers in src/state_polling.py.

The two helpers are being added in parallel to this test file against the
agreed contract:
    render_signature(game_state, players) -> str
        players: iterable of dicts, key 'puuid' required, optional
        'agent'/'team'/'rank'/'rr' (default ''). Order-independent: sorted
        by puuid. The game_state is part of the signature.
    should_redraw(signature, last_signature, seconds_since_last_draw,
                  force_interval=60.0) -> bool
        True on first draw (last_signature None), on any signature change,
        or when seconds_since_last_draw >= force_interval.
        Caller contract: main.py always passes force_interval=float("inf"):
        no state has a refresh timer, so an identical signature never
        redraws no matter how long it sits; draws are data-driven only
        (reason "first" or "changed" in the log).
Pure functions only: no I/O, no threads, no shared state.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.state_polling import render_signature, should_redraw


def _player(puuid, agent="", team="", rank="", rr=""):
    return {"puuid": puuid, "agent": agent, "team": team, "rank": rank, "rr": rr}


BASE_PLAYERS = [
    _player("aaaaaaaa-0000-0000-0000-000000000001", agent="jett", team="Blue", rank=21, rr=88),
    _player("aaaaaaaa-0000-0000-0000-000000000002", agent="omen", team="Blue", rank=17, rr=42),
    _player("aaaaaaaa-0000-0000-0000-000000000003", agent="reyna", team="Red", rank=19, rr=61),
]

SHUFFLED_PLAYERS = [
    BASE_PLAYERS[2],
    BASE_PLAYERS[0],
    BASE_PLAYERS[1],
]


class RenderSignatureTests(unittest.TestCase):
    def test_order_independence(self):
        self.assertEqual(
            render_signature("MENUS", BASE_PLAYERS),
            render_signature("MENUS", SHUFFLED_PLAYERS),
        )

    def test_deterministic_across_calls(self):
        first = render_signature("INGAME", BASE_PLAYERS)
        second = render_signature("INGAME", list(reversed(BASE_PLAYERS)))
        self.assertEqual(first, second)

    def test_game_state_is_included(self):
        self.assertNotEqual(
            render_signature("MENUS", BASE_PLAYERS),
            render_signature("INGAME", BASE_PLAYERS),
        )

    def test_empty_players_is_stable_and_distinct(self):
        empty_a = render_signature("MENUS", [])
        empty_b = render_signature("MENUS", ())
        single = render_signature("MENUS", [_player("bbbbbbbb-0000-0000-0000-000000000001")])
        self.assertEqual(empty_a, empty_b)
        self.assertNotEqual(empty_a, single)

    def test_changed_field_changes_signature(self):
        base = render_signature("INGAME", BASE_PLAYERS)

        changed_puuid = render_signature(
            "INGAME",
            [_player("dddddddd-0000-0000-0000-000000000009", agent="jett", team="Blue", rank=21, rr=88)]
            + BASE_PLAYERS[1:],
        )
        changed_agent = render_signature(
            "INGAME",
            [_player(BASE_PLAYERS[0]["puuid"], agent="sage", team="Blue", rank=21, rr=88)]
            + BASE_PLAYERS[1:],
        )
        changed_team = render_signature(
            "INGAME",
            [_player(BASE_PLAYERS[0]["puuid"], agent="jett", team="Red", rank=21, rr=88)]
            + BASE_PLAYERS[1:],
        )
        changed_rank = render_signature(
            "INGAME",
            [_player(BASE_PLAYERS[0]["puuid"], agent="jett", team="Blue", rank=22, rr=88)]
            + BASE_PLAYERS[1:],
        )
        changed_rr = render_signature(
            "INGAME",
            [_player(BASE_PLAYERS[0]["puuid"], agent="jett", team="Blue", rank=21, rr=89)]
            + BASE_PLAYERS[1:],
        )

        for label, changed in (
            ("puuid", changed_puuid),
            ("agent", changed_agent),
            ("team", changed_team),
            ("rank", changed_rank),
            ("rr", changed_rr),
        ):
            with self.subTest(field=label):
                self.assertNotEqual(base, changed)

    def test_missing_optional_fields_match_explicit_defaults(self):
        minimal = [_player("eeeeeeee-0000-0000-0000-000000000001")]
        explicit = [_player("eeeeeeee-0000-0000-0000-000000000001", agent="", team="", rank="", rr="")]
        self.assertEqual(
            render_signature("PREGAME", minimal),
            render_signature("PREGAME", explicit),
        )


class ShouldRedrawTests(unittest.TestCase):
    SIG_A = "MENUS|a"
    SIG_B = "INGAME|b"

    def test_first_draw_when_last_signature_is_none(self):
        self.assertTrue(should_redraw(self.SIG_A, None, 0.0))
        self.assertTrue(should_redraw(self.SIG_A, None, 999.0))

    def test_signature_change_redraws_immediately(self):
        self.assertTrue(should_redraw(self.SIG_B, self.SIG_A, 0.0))

    def test_identical_and_fresh_skips(self):
        self.assertFalse(should_redraw(self.SIG_A, self.SIG_A, 0.0))
        self.assertFalse(should_redraw(self.SIG_A, self.SIG_A, 30.0))

    def test_force_interval_boundary_default_60(self):
        self.assertFalse(should_redraw(self.SIG_A, self.SIG_A, 59.9))
        self.assertTrue(should_redraw(self.SIG_A, self.SIG_A, 60.0))

    def test_custom_force_interval(self):
        self.assertFalse(should_redraw(self.SIG_A, self.SIG_A, 4.9, force_interval=5.0))
        self.assertTrue(should_redraw(self.SIG_A, self.SIG_A, 5.0, force_interval=5.0))

    def test_change_beats_force_interval_logic(self):
        # A changed signature redraws regardless of elapsed time, even when
        # the interval argument would otherwise allow skipping.
        self.assertTrue(should_redraw(self.SIG_B, self.SIG_A, 0.1, force_interval=60.0))

    def test_infinite_interval_never_forces_identical_signature(self):
        # main.py passes force_interval=float("inf"): no state has a refresh
        # timer, so an identical signature never redraws at ANY elapsed time.
        self.assertFalse(
            should_redraw(self.SIG_A, self.SIG_A, 1e9, force_interval=float("inf"))
        )
        self.assertFalse(
            should_redraw(self.SIG_A, self.SIG_A, 0.0, force_interval=float("inf"))
        )

    def test_infinite_interval_still_redraws_on_change(self):
        # Data-driven redraws are unaffected: any signature change redraws
        # immediately, even with the timer disabled.
        self.assertTrue(
            should_redraw(self.SIG_B, self.SIG_A, 1e9, force_interval=float("inf"))
        )


if __name__ == "__main__":
    unittest.main()
