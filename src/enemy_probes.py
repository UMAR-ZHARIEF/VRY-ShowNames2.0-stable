"""Shared facade for the two enemy probes.

The loadouts probe (src/enemy_probe_loadouts.py) and the v3 result readers
(src/enemy_probe_v3.py) keep their own public functions; this facade gives
main.py ONE dialect so the display and gating code no longer knows each
probe's private shape.

Semantics mirror exactly what main.py did before this facade existed:

- ``loadouts_enabled`` is the caller-computed flag (feature flag AND a known
  match id), exactly like the old ``loadouts_on``.
- When loadouts is enabled: puuids come from the loadouts results, agents
  from the same results, and the live status is returned.
- When loadouts is disabled: puuids/agents are empty and the status is
  "idle" (no probe results are read at all).
- v3 confirmed names are only read when ``v3_enabled`` is true (the feature
  flag AND a known match id); any exception is reported through ``log`` with
  the same message text main.py used, and the v3 view degrades to empty.
- v3-only puuids are appended to the loadouts puuids (discovery order kept,
  duplicates removed) ONLY when loadouts is enabled, exactly like the union
  main.py performed.
"""
from src.enemy_probe_loadouts import (
    get_enemy_agents,
    get_enemy_puuids,
    get_loadouts_status,
    start_loadouts_probe,
)


def enemy_snapshot(match_id, loadouts_enabled, v3_enabled, log=None):
    """One merged, read-only view of both probes for one match id."""
    puuids = []
    agents = {}
    loadouts_status = "idle"
    if loadouts_enabled and match_id is not None:
        puuids = list(get_enemy_puuids(match_id))
        agents = get_enemy_agents(match_id)
        loadouts_status = get_loadouts_status(match_id)

    v3_names = {}
    v3_status = ""
    if v3_enabled and match_id:
        try:
            from src.enemy_probe_v3 import get_confirmed_names, get_status
            v3_names = get_confirmed_names(match_id)
            v3_status = get_status(match_id)
        except Exception as exc:
            if log is not None:
                log(f"[enemy-table] read v3 results exc: {type(exc).__name__}: {exc}")

    if loadouts_enabled:
        for _p in v3_names:
            if _p not in puuids:
                puuids.append(_p)

    return {
        "puuids": puuids,
        "agents": agents,
        "v3_names": v3_names,
        "loadouts_status": loadouts_status,
        "v3_status": v3_status,
    }


def start_loadouts_probe_safe(Requests, match_id, ally_puuids, log):
    """Thin wrapper so main.py imports only this module for probe starts."""
    start_loadouts_probe(Requests, match_id, ally_puuids, log)
