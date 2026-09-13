"""Golden tests pinning the exact table-cell formatting behavior.

These strings were computed by reading src/enemy_table_data.py BEFORE the
Phase 4 step 2 unification moved the helpers into src/row_builder.py. They
pin the pre-unification behavior: after the move, the same inputs MUST
produce the exact same strings, or the printed tables would change.

The helpers now live in src/row_builder.py (public names); this suite is
their behavior contract: same inputs MUST produce the exact same strings.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.row_builder import (
    agent_cell,
    gradient_cell,
    level_cell,
    peak_cell,
    rank_cell,
    rr_earned_cell,
    safe_index,
    safe_text,
    wr_cell,
)

RANKS = ["Iron 1", "Iron 2", "Bronze 1", "Silver 1", "Gold 1", "Platinum 1"]


class StubColors:
    """Deterministic gradients so exact strings can be asserted."""

    def get_hs_gradient(self, value):
        return f"[HS:{value}]"

    def get_wr_gradient(self, value):
        return f"[WR:{value}]"

    def get_rr_gradient(self, earned, penalty):
        return f"[RR:{earned}/{penalty}]"

    def get_perf_bonus_color(self, value):
        return f"[PB:{value}]"

    def level_to_color(self, value):
        return f"[LV:{value}]"


class RaisingColors(StubColors):
    def get_hs_gradient(self, value):
        raise ValueError("boom")

    def get_wr_gradient(self, value):
        raise ValueError("boom")

    def get_rr_gradient(self, earned, penalty):
        raise ValueError("boom")


class StubCfg:
    def __init__(self, flags):
        self._flags = flags

    def get_feature_flag(self, key):
        return self._flags.get(key, False)


AGG_ON = StubCfg({"aggregate_rank_rr": True, "peak_rank_act": True})
AGG_OFF = StubCfg({"aggregate_rank_rr": False, "peak_rank_act": True})
PEAK_OFF = StubCfg({"aggregate_rank_rr": True, "peak_rank_act": False})
TABLE_RR_ON = {"rr": True}
TABLE_RR_OFF = {"rr": False}
COLORS = StubColors()
RAISING = RaisingColors()


class TextIndexTests(unittest.TestCase):
    def test_text_present_value_passes_through(self):
        self.assertEqual(safe_text({"kd": 1.5}, "kd"), 1.5)

    def test_text_missing_key_is_question_mark(self):
        self.assertEqual(safe_text({}, "kd"), "?")

    def test_text_none_value_is_question_mark(self):
        self.assertEqual(safe_text({"kd": None}, "kd"), "?")

    def test_text_none_data_is_question_mark(self):
        self.assertEqual(safe_text(None, "kd"), "?")

    def test_index_maps_rank_number_to_name(self):
        self.assertEqual(safe_index(RANKS, {"rank": 4}, "rank"), "Gold 1")

    def test_index_missing_key_is_question_mark(self):
        self.assertEqual(safe_index(RANKS, {}, "rank"), "?")

    def test_index_out_of_range_is_question_mark(self):
        self.assertEqual(safe_index(RANKS, {"rank": 99}, "rank"), "?")

    def test_index_none_collection_is_question_mark(self):
        self.assertEqual(safe_index(None, {"rank": 4}, "rank"), "?")


class RankCellTests(unittest.TestCase):
    def test_aggregate_on_table_rr_on(self):
        self.assertEqual(
            rank_cell({"rank": 4, "rr": 55}, RANKS, AGG_ON, TABLE_RR_ON),
            "Gold 1 (55)",
        )

    def test_aggregate_off_ignores_rr(self):
        self.assertEqual(
            rank_cell({"rank": 4, "rr": 55}, RANKS, AGG_OFF, TABLE_RR_ON),
            "Gold 1",
        )

    def test_table_rr_off_ignores_rr_even_with_aggregate(self):
        self.assertEqual(
            rank_cell({"rank": 4, "rr": 55}, RANKS, AGG_ON, TABLE_RR_OFF),
            "Gold 1",
        )

    def test_missing_rank_is_question_mark_even_with_rr(self):
        self.assertEqual(rank_cell({"rr": 55}, RANKS, AGG_ON, TABLE_RR_ON), "?")

    def test_missing_rr_falls_back_to_plain_rank(self):
        self.assertEqual(
            rank_cell({"rank": 4}, RANKS, AGG_ON, TABLE_RR_ON), "Gold 1"
        )

    def test_none_rr_falls_back_to_plain_rank(self):
        self.assertEqual(
            rank_cell({"rank": 4, "rr": None}, RANKS, AGG_ON, TABLE_RR_ON),
            "Gold 1",
        )


class PeakCellTests(unittest.TestCase):
    def test_lettered_episode_gets_plain_suffix(self):
        rank = {"peakrank": 5, "peakrankep": "v25", "peakrankact": 3}
        self.assertEqual(
            peak_cell(rank, RANKS, AGG_ON), "Platinum 1 (v25a3)"
        )

    def test_numbered_episode_gets_e_prefix(self):
        rank = {"peakrank": 5, "peakrankep": "10", "peakrankact": 1}
        self.assertEqual(
            peak_cell(rank, RANKS, AGG_ON), "Platinum 1 (e10a1)"
        )

    def test_flag_off_drops_the_suffix(self):
        rank = {"peakrank": 5, "peakrankep": "v25", "peakrankact": 3}
        self.assertEqual(peak_cell(rank, RANKS, PEAK_OFF), "Platinum 1")

    def test_missing_peakrank_is_question_mark(self):
        self.assertEqual(peak_cell({}, RANKS, AGG_ON), "?")

    def test_missing_episode_details_keep_bare_peak(self):
        rank = {"peakrank": 5}
        self.assertEqual(peak_cell(rank, RANKS, AGG_ON), "Platinum 1")


class WrCellTests(unittest.TestCase):
    def test_wr_with_games(self):
        rank = {"wr": 60, "numberofgames": 10}
        self.assertEqual(wr_cell(rank, COLORS), "[WR:60] (10)")

    def test_missing_wr_is_question_mark(self):
        self.assertEqual(wr_cell({}, COLORS), "?")

    def test_missing_games_shows_question_mark_in_parens(self):
        rank = {"wr": 60}
        self.assertEqual(wr_cell(rank, COLORS), "[WR:60] (?)")

    def test_raising_gradient_falls_back_to_plain_text(self):
        rank = {"wr": 60, "numberofgames": 10}
        self.assertEqual(wr_cell(rank, RAISING), "60 (10)")


class RrEarnedCellTests(unittest.TestCase):
    def test_earned_with_penalty(self):
        stats = {"RankedRatingEarned": 18, "AFKPenalty": 0}
        self.assertEqual(rr_earned_cell(stats, COLORS), "[RR:18/0]")

    def test_missing_penalty_returns_earned_as_is(self):
        stats = {"RankedRatingEarned": 18}
        self.assertEqual(rr_earned_cell(stats, COLORS), 18)

    def test_missing_earned_is_question_mark(self):
        self.assertEqual(rr_earned_cell({}, COLORS), "?")

    def test_na_values_flow_into_the_gradient(self):
        stats = {"RankedRatingEarned": "N/A", "AFKPenalty": "N/A"}
        self.assertEqual(rr_earned_cell(stats, COLORS), "[RR:N/A/N/A]")

    def test_raising_gradient_falls_back_to_plain_text(self):
        stats = {"RankedRatingEarned": 18, "AFKPenalty": 0}
        self.assertEqual(rr_earned_cell(stats, RAISING), "18")


class GradientCellTests(unittest.TestCase):
    def test_present_value_is_formatted(self):
        self.assertEqual(
            gradient_cell({"hs": 25}, "hs", COLORS.get_hs_gradient), "[HS:25]"
        )

    def test_missing_value_is_question_mark(self):
        self.assertEqual(gradient_cell({}, "hs", COLORS.get_hs_gradient), "?")

    def test_raising_formatter_falls_back_to_str(self):
        self.assertEqual(
            gradient_cell({"perf_bonus": "weird"}, "perf_bonus",
                          lambda v: 1 / 0),
            "weird",
        )

    def test_perf_bonus_passthrough(self):
        self.assertEqual(
            gradient_cell({"perf_bonus": 7}, "perf_bonus",
                          COLORS.get_perf_bonus_color),
            "[PB:7]",
        )


class LevelCellTests(unittest.TestCase):
    def test_positive_int_is_colored(self):
        self.assertEqual(
            level_cell(lambda p: 250, "puuid", COLORS), "[LV:250]"
        )

    def test_bool_is_never_a_level(self):
        self.assertEqual(level_cell(lambda p: True, "puuid", COLORS), "?")

    def test_none_is_question_mark(self):
        self.assertEqual(level_cell(lambda p: None, "puuid", COLORS), "?")

    def test_zero_and_negative_are_question_marks(self):
        self.assertEqual(level_cell(lambda p: 0, "puuid", COLORS), "?")
        self.assertEqual(level_cell(lambda p: -5, "puuid", COLORS), "?")

    def test_raising_lookup_is_question_mark(self):
        def boom(p):
            raise RuntimeError("boom")

        self.assertEqual(level_cell(boom, "puuid", COLORS), "?")

    def test_no_lookup_fn_is_question_mark(self):
        self.assertEqual(level_cell(None, "puuid", COLORS), "?")


class AgentCellTests(unittest.TestCase):
    def test_empty_character_id_is_dim_question_mark(self):
        self.assertEqual(agent_cell("", {}), "[dim]?[/dim]")

    def test_unknown_character_id_names_the_agent(self):
        # colr wraps the text in ANSI codes; pin the visible word only.
        self.assertIn("Unknown", agent_cell("abc", {}))


if __name__ == "__main__":
    unittest.main()
