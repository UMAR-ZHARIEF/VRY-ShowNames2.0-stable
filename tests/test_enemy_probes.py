"""Regression tests for the enemy-probe facade (src/enemy_probes.py).

Both backends are monkeypatched in-process (no network, no threads): the
loadouts accessors live on src.enemy_probes itself, and the v3 readers are
imported lazily inside enemy_snapshot, so patching src.enemy_probe_v3
attributes is enough. Pins: union order, idle path, tolerant v3 failure
with and without log, agents passthrough, status passthrough, and the
start_loadouts_probe_safe delegation.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.enemy_probes as ep
import src.enemy_probe_v3 as v3mod


class _StubLog(list):
    def __call__(self, message):
        self.append(message)


def _patched(loadouts_puuids, loadouts_agents, loadouts_status, v3_names, v3_status, v3_raises=False):
    """Patch both backends for the duration of a test."""
    loadouts_patch = mock.patch.multiple(
        ep,
        get_enemy_puuids=mock.Mock(return_value=list(loadouts_puuids)),
        get_enemy_agents=mock.Mock(return_value=dict(loadouts_agents)),
        get_loadouts_status=mock.Mock(return_value=loadouts_status),
        start_loadouts_probe=mock.Mock(),
    )
    v3_get_confirmed = mock.Mock(return_value=dict(v3_names))
    if v3_raises:
        v3_get_confirmed.side_effect = RuntimeError("boom")
    v3_patch = mock.patch.multiple(
        v3mod,
        get_confirmed_names=v3_get_confirmed,
        get_status=mock.Mock(return_value=v3_status),
    )
    return loadouts_patch, v3_patch


class EnemySnapshotTests(unittest.TestCase):
    def test_union_order_loadouts_first_v3_only_appended_no_duplicates(self):
        loadouts_patch, v3_patch = _patched(
            loadouts_puuids=["1111", "2222", "3333"],
            loadouts_agents={"1111": "abc", "2222": ""},
            loadouts_status="complete",
            v3_names={"2222": "Dup#1111", "4444": "Only#V3"},
            v3_status="done",
        )
        with loadouts_patch, v3_patch:
            snap = ep.enemy_snapshot("match-1", loadouts_enabled=True, v3_enabled=True, log=_StubLog())
        # Loadouts discovery order first, v3-only appended, duplicate removed.
        self.assertEqual(snap["puuids"], ["1111", "2222", "3333", "4444"])
        self.assertEqual(snap["v3_names"], {"2222": "Dup#1111", "4444": "Only#V3"})

    def test_loadouts_disabled_reads_v3_only_and_status_is_idle(self):
        loadouts_patch, v3_patch = _patched(
            loadouts_puuids=["1111"],
            loadouts_agents={"1111": "abc"},
            loadouts_status="complete",
            v3_names={"9999": "V3Only#0001"},
            v3_status="done",
        )
        with loadouts_patch, v3_patch:
            snap = ep.enemy_snapshot("match-2", loadouts_enabled=False, v3_enabled=True, log=_StubLog())
        # Facade mirrors old main.py: v3-only puuids are appended ONLY when
        # loadouts is enabled; disabled means empty puuids + idle status.
        self.assertEqual(snap["puuids"], [])
        self.assertEqual(snap["agents"], {})
        self.assertEqual(snap["loadouts_status"], "idle")
        self.assertEqual(snap["v3_status"], "done")

    def test_v3_failure_degrades_without_raising_and_logs_when_given(self):
        loadouts_patch, v3_patch = _patched(
            loadouts_puuids=["1111"],
            loadouts_agents={},
            loadouts_status="partial",
            v3_names={},
            v3_status="",
            v3_raises=True,
        )
        stub_log = _StubLog()
        with loadouts_patch, v3_patch:
            snap_with_log = ep.enemy_snapshot("match-3", True, True, log=stub_log)
            snap_without_log = ep.enemy_snapshot("match-3", True, True)
        self.assertEqual(snap_with_log["v3_names"], {})
        self.assertEqual(snap_with_log["v3_status"], "")
        self.assertEqual(snap_without_log["v3_names"], {})
        self.assertEqual(len(stub_log), 1)
        self.assertIn("[enemy-table] read v3 results exc", stub_log[0])

    def test_agents_passthrough_includes_all_subjects(self):
        agents = {
            "1111": "a1",
            "2222": "a2",
            "3333": "a3",
            "4444": "a4",
            "5555": "a5",
        }
        loadouts_patch, v3_patch = _patched(
            loadouts_puuids=list(agents), loadouts_agents=agents,
            loadouts_status="complete", v3_names={}, v3_status="done",
        )
        with loadouts_patch, v3_patch:
            snap = ep.enemy_snapshot("match-4", True, True)
        self.assertEqual(snap["agents"], agents)

    def test_statuses_pass_through_unchanged(self):
        loadouts_patch, v3_patch = _patched(
            loadouts_puuids=[], loadouts_agents={}, loadouts_status="polling",
            v3_names={}, v3_status="running",
        )
        with loadouts_patch, v3_patch:
            snap = ep.enemy_snapshot("match-5", True, True)
        self.assertEqual(snap["loadouts_status"], "polling")
        self.assertEqual(snap["v3_status"], "running")


class StartSafeDelegationTests(unittest.TestCase):
    def test_start_loadouts_probe_safe_delegates_once_with_same_args(self):
        args = ("fake-requests", "match-6", ["p1", "p2"], _StubLog())
        with mock.patch.object(ep, "start_loadouts_probe") as starter:
            ep.start_loadouts_probe_safe(*args)
        starter.assert_called_once_with(*args)


if __name__ == "__main__":
    unittest.main()
