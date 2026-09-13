"""Tests for MatchContext (Phase 4 step 1): consolidated per-session state.

Pins the exact initial values the old main.py module-level globals had, the
match-player-cache reset/TTL semantics, the menus 60s TTL cache, the enemy
level cache, and lock usability from two threads.
"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.match_context import MatchContext


class InitialValuesTests(unittest.TestCase):
    def test_initial_values_match_old_globals(self):
        ctx = MatchContext()
        self.assertEqual(ctx.server, "")
        self.assertIsNone(ctx.team_side)
        self.assertEqual(ctx.gamemode, "")
        self.assertEqual(ctx.v3_probed_matches, set())
        self.assertEqual(ctx.enemy_level_cache, {})
        self.assertIsNone(ctx.pending_post_match_lookup)
        self.assertEqual(ctx.match_player_cache, {"match_id": None, "players": {}})
        self.assertEqual(ctx.consecutive_state_failures, 0)
        self.assertIsNone(ctx.last_drawn_signature)
        self.assertIsNone(ctx.last_draw_state)
        self.assertEqual(ctx.last_draw_time, 0.0)
        self.assertEqual(ctx.last_drawn_enemy_count, -1)
        self.assertEqual(ctx.last_enemy_names, {})
        self.assertEqual(ctx.state_entered_at, 0.0)
        self.assertEqual(ctx.menus_stats_cache, {})
        self.assertEqual(MatchContext.MATCH_PLAYER_CACHE_TTL_SECONDS, 300)
        self.assertEqual(MatchContext.MENUS_STATS_TTL_SECONDS, 60)
        self.assertIsInstance(ctx.menus_stats_lock, type(threading.Lock()))


class MatchPlayerCacheTests(unittest.TestCase):
    def test_reset_binds_new_match_id_and_clears_players(self):
        ctx = MatchContext()
        ctx.match_player_cache["match_id"] = "old"
        ctx.match_player_cache["players"]["a"] = {"ts": 0}
        ctx.reset_match_player_cache("new")
        self.assertEqual(ctx.match_player_cache["match_id"], "new")
        self.assertEqual(ctx.match_player_cache["players"], {})

    def test_ensure_expired_player_is_dropped(self):
        ctx = MatchContext()
        ctx.reset_match_player_cache("m1")
        ctx.match_player_cache["players"]["old"] = {"ts": time.time() - 999}
        ctx.ensure_match_player_cache("m1")
        self.assertNotIn("old", ctx.match_player_cache["players"])

    def test_ensure_keeps_fresh_player(self):
        ctx = MatchContext()
        ctx.reset_match_player_cache("m1")
        ctx.match_player_cache["players"]["fresh"] = {"ts": time.time()}
        ctx.ensure_match_player_cache("m1")
        self.assertIn("fresh", ctx.match_player_cache["players"])

    def test_ensure_with_new_match_id_resets(self):
        ctx = MatchContext()
        ctx.match_player_cache["match_id"] = "m1"
        ctx.match_player_cache["players"]["a"] = {"ts": time.time()}
        ctx.ensure_match_player_cache("m2")
        self.assertEqual(ctx.match_player_cache["match_id"], "m2")
        self.assertEqual(ctx.match_player_cache["players"], {})


class MenusStatsCacheTests(unittest.TestCase):
    def test_set_then_get_within_ttl_round_trips(self):
        ctx = MatchContext()
        ctx.menus_stats_set("puuid1", {"rank": 5}, {"rank": 4}, {"kd": 1.2})
        got = ctx.menus_stats_get("puuid1")
        self.assertEqual(got, ({"rank": 5}, {"rank": 4}, {"kd": 1.2}))

    def test_get_returns_none_for_unknown_subject(self):
        ctx = MatchContext()
        self.assertIsNone(ctx.menus_stats_get("nobody"))

    def test_expired_entry_returns_none(self):
        ctx = MatchContext()
        ctx.menus_stats_cache["puuid1"] = (time.time() - 61, 1, 2, 3)
        self.assertIsNone(ctx.menus_stats_get("puuid1"))


class EnemyLevelCacheTests(unittest.TestCase):
    def test_level_cache_stores_per_match_and_puuid(self):
        ctx = MatchContext()
        ctx.enemy_level_cache[("m1", "puuidA")] = 324
        ctx.enemy_level_cache[("m2", "puuidA")] = 325
        self.assertEqual(ctx.enemy_level_cache[("m1", "puuidA")], 324)
        self.assertEqual(ctx.enemy_level_cache[("m2", "puuidA")], 325)


class DrawGateTests(unittest.TestCase):
    def test_draw_gate_fields_independent(self):
        ctx = MatchContext()
        ctx.last_drawn_signature = "sig1"
        ctx.last_draw_state = "PREGAME"
        ctx.last_draw_time = 123.5
        ctx.last_drawn_enemy_count = 5
        ctx.last_enemy_names["abc"] = "Name#TAG"
        self.assertEqual(ctx.last_drawn_signature, "sig1")
        self.assertEqual(ctx.last_draw_state, "PREGAME")
        self.assertEqual(ctx.last_draw_time, 123.5)
        self.assertEqual(ctx.last_drawn_enemy_count, 5)
        self.assertEqual(ctx.last_enemy_names, {"abc": "Name#TAG"})


class ThreadedAccessTests(unittest.TestCase):
    def test_lock_survives_concurrent_menus_access(self):
        ctx = MatchContext()
        errors = []

        def worker(i):
            try:
                for _ in range(200):
                    ctx.menus_stats_set(f"p{i}", 1, 2, 3)
                    ctx.menus_stats_get(f"p{i}")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
