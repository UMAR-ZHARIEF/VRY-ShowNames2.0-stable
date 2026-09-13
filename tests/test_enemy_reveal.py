"""Offline tests for the enemy reveal during agent select.

Covers the loadouts probe (src/enemy_probe_loadouts.py) and the enemy
table data helpers (src/enemy_table_data.py) against stub objects only:
no network, no game, no real Requests or names classes. Run with the
py launcher from the repo root:

    py tests/test_enemy_reveal.py
"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.enemy_probe_loadouts import (
    get_enemy_agents,
    get_enemy_puuids,
    get_loadouts_status,
    start_loadouts_probe,
)
from src.enemy_table_data import build_enemy_rows, resolve_enemy_names

MATCH = "11111111-2222-3333-4444-555555555555"
ALLY = [f"aaaaaaaa-0000-0000-0000-00000000000{i}" for i in range(5)]
ENEMY = [f"bbbbbbbb-0000-0000-0000-00000000000{i}" for i in range(5)]
AGENT_ONE = "cccccccc-0000-0000-0000-000000000001"
AGENT_TWO = "cccccccc-0000-0000-0000-000000000002"


def quiet_log(*args, **kwargs):
    pass


def wait_until(predicate, timeout=6.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def loadouts_payload():
    # 5 ally subjects with one duplicated entry, then the 5 enemies; the
    # first two enemies have locked an agent (CharacterID present).
    subjects = ALLY + [ALLY[0]] + ENEMY
    entries = []
    for index, subject in enumerate(subjects):
        entry = {"Subject": subject}
        if subject == ENEMY[0]:
            entry["CharacterID"] = AGENT_ONE
        elif subject == ENEMY[1]:
            entry["CharacterID"] = AGENT_TWO
        entries.append(entry)
    return {"Loadouts": entries}


def ally_only_payload():
    return {"Loadouts": [{"Subject": subject} for subject in ALLY]}


class StubRequests:
    """fetch(url_type, endpoint, method) returning a fixed or callable payload."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.lock = threading.Lock()

    def fetch(self, url_type=None, endpoint=None, method=None, *args, **kwargs):
        with self.lock:
            self.calls += 1
            count = self.calls
        if callable(self.payload):
            return self.payload(count)
        return self.payload


class StubNamesClass:
    def __init__(self, aggressive_result=None, aggressive_error=None):
        self.aggressive_result = aggressive_result
        self.aggressive_error = aggressive_error
        self.update_cache_calls = []
        self.patch_calls = 0

    def get_names_from_puuids(self, puuids):
        return {puuid: "" for puuid in puuids}

    def resolve_aggressive(self, names, name_cache=None, presence_list=None,
                           match_id=None, game_state=None, own_puuid=None):
        if self.aggressive_error is not None:
            raise self.aggressive_error
        if self.aggressive_result is not None:
            names.update(self.aggressive_result)
        return names

    def update_cache(self, names, name_cache, source=None):
        self.update_cache_calls.append(source)

    def patch_from_cache(self, names, name_cache):
        self.patch_calls += 1
        return names


class StubNameCache:
    pass


def make_colors():
    class StubColors:
        def get_hs_gradient(self, value):
            return f"HS{value}"

        def get_wr_gradient(self, value):
            return f"WR{value}"

        def get_rr_gradient(self, value, afk_penalty):
            return f"RRG{value}"

        def get_perf_bonus_color(self, value):
            return f"PERF{value}"

        def level_to_color(self, value):
            return f"LVL{value}"

    return StubColors()


def full_rank_stats(puuid):
    player_rank = {
        "rank": 5,
        "rr": 70,
        "peakrank": 7,
        "peakrankep": 9,
        "peakrankact": 3,
        "wr": 55,
        "numberofgames": 100,
        "leaderboard": 0,
    }
    previous_rank = {"rank": 4}
    ppstats = {
        "hs": 24.5,
        "kd": 1.1,
        "perf_bonus": 5,
        "RankedRatingEarned": 15,
        "AFKPenalty": 0,
    }
    return player_rank, previous_rank, ppstats


def make_cfg(aggregate=False, peak_act=False):
    class StubCfg:
        table = {"rr": True}

        def get_feature_flag(self, key):
            if key == "aggregate_rank_rr":
                return aggregate
            if key == "peak_rank_act":
                return peak_act
            return True

    return StubCfg()


RANKS = ["Unranked", "Iron 1", "Iron 2", "Iron 3", "Bronze 1", "Silver 1",
         "Gold 1", "Platinum 1", "Diamond 1", "Ascendant 1"]
NUMBERTORANKS = {index: name for index, name in enumerate(RANKS)}
AGENT_DICT = {AGENT_ONE.lower(): "Jett"}


class LoadoutsProbeTests(unittest.TestCase):
    def test_enemy_discovery_order_agents_and_status(self):
        requests_stub = StubRequests(loadouts_payload())
        start_loadouts_probe(requests_stub, MATCH, ALLY, quiet_log,
                             poll_seconds=0.05, budget_seconds=8)
        self.assertTrue(wait_until(
            lambda: get_loadouts_status(MATCH) == "complete"),
            "probe never reached complete status")
        self.assertEqual(get_enemy_puuids(MATCH), ENEMY)
        agents = get_enemy_agents(MATCH)
        self.assertEqual(agents.get(ENEMY[0], ""), AGENT_ONE)
        self.assertEqual(agents.get(ENEMY[1], ""), AGENT_TWO)
        self.assertEqual(agents.get(ENEMY[2], ""), "")
        self.assertEqual(agents.get(ENEMY[3], ""), "")
        self.assertEqual(agents.get(ENEMY[4], ""), "")

    def test_duplicated_ally_entry_is_not_an_enemy(self):
        payload = loadouts_payload()
        subjects = [entry["Subject"] for entry in payload["Loadouts"]]
        self.assertEqual(len(subjects), 11)
        self.assertEqual(len([s for s in subjects if s == ALLY[0]]), 2)
        requests_stub = StubRequests(payload)
        match_id = "aaaaaaaa-0000-0000-0000-0000000000aa"
        start_loadouts_probe(requests_stub, match_id, ALLY, quiet_log,
                             poll_seconds=0.05, budget_seconds=8)
        self.assertTrue(wait_until(
            lambda: get_loadouts_status(match_id) == "complete"))
        self.assertNotIn(ALLY[0], get_enemy_puuids(match_id))
        self.assertEqual(len(get_enemy_puuids(match_id)), 5)

    def test_error_code_response_keeps_polling_without_crash(self):
        requests_stub = StubRequests({"errorCode": "RESOURCE_NOT_FOUND"})
        match_id = "aaaaaaaa-0000-0000-0000-0000000000bb"
        start_loadouts_probe(requests_stub, match_id, ALLY, quiet_log,
                             poll_seconds=0.05, budget_seconds=2)
        self.assertTrue(wait_until(lambda: requests_stub.calls >= 3),
                        "probe stopped polling after errorCode responses")
        self.assertEqual(get_enemy_puuids(match_id), [])
        self.assertEqual(get_loadouts_status(match_id), "polling")

    def test_enemies_appear_progressively(self):
        def payload_for_call(count):
            if count <= 3:
                return ally_only_payload()
            return loadouts_payload()

        requests_stub = StubRequests(payload_for_call)
        match_id = "aaaaaaaa-0000-0000-0000-0000000000cc"
        start_loadouts_probe(requests_stub, match_id, ALLY, quiet_log,
                             poll_seconds=0.05, budget_seconds=8)
        self.assertTrue(wait_until(
            lambda: get_loadouts_status(match_id) in ("partial", "complete")),
            "probe never left polling with allies only")
        self.assertTrue(wait_until(
            lambda: get_loadouts_status(match_id) == "complete"))
        self.assertEqual(get_enemy_puuids(match_id), ENEMY)


class ResolveEnemyNamesTests(unittest.TestCase):
    def test_resolved_names_pass_through_pipeline(self):
        names_class = StubNamesClass(aggressive_result={
            ENEMY[0]: "Alice#001",
            ENEMY[1]: "Bob#002",
        })
        names = resolve_enemy_names(
            MATCH, ENEMY, names_class, StubNameCache(), [], "own-puuid",
            quiet_log,
        )
        self.assertEqual(set(names.keys()), set(ENEMY))
        self.assertEqual(names[ENEMY[0]], "Alice#001")
        self.assertEqual(names[ENEMY[1]], "Bob#002")
        self.assertEqual(names[ENEMY[2]], "")
        self.assertEqual(names_class.update_cache_calls, ["api-enemy"])
        self.assertEqual(names_class.patch_calls, 1)

    def test_raising_resolve_aggressive_does_not_propagate(self):
        names_class = StubNamesClass(
            aggressive_error=RuntimeError("rate limited"))
        names = resolve_enemy_names(
            MATCH, ENEMY, names_class, StubNameCache(), [], "own-puuid",
            quiet_log,
        )
        self.assertEqual(set(names.keys()), set(ENEMY))


class BuildEnemyRowsTests(unittest.TestCase):
    def build(self, enemy, rank_stats_fn, names=None, agents=None,
              aggregate=False, peak_act=False, level_fn=None):
        names = names if names is not None else {enemy[0]: "Alice#001"}
        agents = agents if agents is not None else {}
        return build_enemy_rows(
            enemy, agents, names, rank_stats_fn,
            make_cfg(aggregate=aggregate, peak_act=peak_act),
            RANKS, NUMBERTORANKS, make_colors(), {"rr": True}, AGENT_DICT,
            level_fn=level_fn,
        )

    def test_full_data_row_shape_and_cells(self):
        rows = self.build(ENEMY[:1], full_rank_stats,
                          agents={ENEMY[0]: AGENT_ONE})
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(len(row), 16)
        self.assertIn("Alice#001", row[2])
        self.assertIn("Jett", row[1])
        self.assertEqual(row[5], RANKS[5])
        self.assertEqual(row[7], RANKS[7])
        self.assertEqual(row[8], RANKS[4])
        self.assertIn("HS24.5", row[10])
        self.assertIn("WR55", row[11])

    def test_missing_agent_and_name_use_placeholders(self):
        rows = self.build(ENEMY[:1], full_rank_stats, names={ENEMY[0]: ""},
                          agents={})
        row = rows[0]
        self.assertEqual(len(row), 16)
        self.assertIn("?", row[1])
        # The unresolved-name cell reuses the current enemy preview style
        # (dim "Hidden"), the companion of the bold yellow resolved name.
        self.assertIn("Hidden", row[2])

    def test_none_rank_stats_never_raises(self):
        def none_stats(puuid):
            return None, None, None

        rows = self.build(ENEMY[:1], none_stats)
        row = rows[0]
        self.assertEqual(len(row), 16)
        for index in (5, 6, 7, 8, 10, 11, 12):
            self.assertEqual(row[index], "?")
        self.assertIn("Alice#001", row[2])

    def test_aggregate_rank_rr_and_peak_act_flags(self):
        names = {ENEMY[0]: "Alice#001"}
        agents = {ENEMY[0]: AGENT_ONE}
        rows = build_enemy_rows(
            ENEMY[:1], agents, names, full_rank_stats,
            make_cfg(aggregate=True, peak_act=True),
            RANKS, NUMBERTORANKS, make_colors(), {"rr": True}, AGENT_DICT,
        )
        row = rows[0]
        self.assertEqual(len(row), 16)
        self.assertTrue(row[5].startswith(RANKS[5]))
        self.assertIn("70", row[5])
        self.assertIn("e9a3", row[7])

    def test_level_fn_provides_level(self):
        rows = self.build(ENEMY[:1], full_rank_stats, level_fn=lambda p: 24)
        self.assertEqual(rows[0][13], "LVL24")

    def test_level_fn_raising_gives_placeholder(self):
        def raiser(p):
            raise RuntimeError("match history unavailable")

        rows = self.build(ENEMY[:1], full_rank_stats, level_fn=raiser)
        self.assertEqual(rows[0][13], "?")

    def test_level_fn_invalid_values_give_placeholder(self):
        for level_fn in (lambda p: None, lambda p: 0, lambda p: -3,
                         lambda p: "24", lambda p: True):
            rows = self.build(ENEMY[:1], full_rank_stats, level_fn=level_fn)
            self.assertEqual(rows[0][13], "?")

    def test_level_fn_absent_keeps_placeholder(self):
        rows = self.build(ENEMY[:1], full_rank_stats)
        self.assertEqual(rows[0][13], "?")


if __name__ == "__main__":
    unittest.main(verbosity=2)
