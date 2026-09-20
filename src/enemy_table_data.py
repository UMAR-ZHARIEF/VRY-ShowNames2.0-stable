# Enemy-team data helpers for the PREGAME enemy table. Names use the same
# resolver pipeline as the ally path; every cell degrades to "?" or "Hidden"
# instead of raising so a partially revealed enemy team still renders.
#
# The shared cell formatters live in src/row_builder.py; the private aliases
# below keep this module's internal call sites (and its tests) unchanged.
from concurrent.futures import ThreadPoolExecutor

from src.row_builder import (
    agent_cell as _agent_cell,
    gradient_cell as _gradient_cell,
    level_cell as _level_cell,
    peak_act_suffix,
    peak_cell as _peak_cell,
    rank_cell as _rank_cell,
    rr_earned_cell as _rr_earned_cell,
    safe_index as _index,
    safe_text as _text,
    wr_cell as _wr_cell,
)


def resolve_enemy_names(match_id, enemy_puuids, namesClass, nameCache, presence_list, own_puuid, log):
    names = {puuid: "" for puuid in enemy_puuids}
    try:
        # get_names_from_puuids expects player dicts with a "Subject" key,
        # same call as the ally path, so plain puuids are wrapped.
        names = namesClass.get_names_from_puuids(
            [{"Subject": puuid} for puuid in enemy_puuids]
        )
    except Exception as e:
        log(f"[enemy-table-data] get_names_from_puuids exc: {type(e).__name__}: {e}")
    try:
        names = namesClass.resolve_aggressive(
            names,
            name_cache=nameCache,
            presence_list=presence_list,
            match_id=match_id,
            game_state="PREGAME",
            own_puuid=own_puuid,
        )
    except Exception as e:
        log(f"[enemy-table-data] resolve_aggressive exc: {type(e).__name__}: {e}")
    try:
        namesClass.update_cache(names, nameCache, source="api-enemy")
    except Exception as e:
        log(f"[enemy-table-data] update_cache exc: {type(e).__name__}: {e}")
    try:
        names = namesClass.patch_from_cache(names, nameCache)
    except Exception as e:
        log(f"[enemy-table-data] patch_from_cache exc: {type(e).__name__}: {e}")
    return {puuid: names.get(puuid, "") for puuid in enemy_puuids}


def _enemy_fetch_job(rank_stats_fn, level_fn, puuid):
    # One worker's unit of work. Order inside the job matters: rank/stats
    # first, then level, mirroring the sequential per-enemy order so the
    # level fallback sees the same PlayerStats state (the last competitive
    # match id recorded by get_stats) as it did serially. Exceptions degrade
    # exactly as before: rank/stats to (None, None, None) via
    # _safe_rank_stats, level to None (row_builder.level_cell renders "?"
    # for both None and a raising/invalid lookup).
    rank_stats = _safe_rank_stats(rank_stats_fn, puuid)
    level = None
    if level_fn is not None:
        try:
            level = level_fn(puuid)
        except Exception:
            level = None
    return rank_stats, level


def _gather_enemy_rank_stats_levels(enemy_puuids, rank_stats_fn, level_fn):
    # Fetch every enemy's data concurrently (bounded pool, <=8 workers) and
    # return results as a list aligned with enemy_puuids, so the assembly in
    # build_enemy_rows is index-based and byte-identical to the sequential
    # build it replaced.
    if not enemy_puuids:
        return []
    max_workers = min(8, len(enemy_puuids))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(_enemy_fetch_job, rank_stats_fn, level_fn, puuid)
            for puuid in enemy_puuids
        ]
        return [future.result() for future in futures]


def build_enemy_rows(enemy_puuids, agents_map, names_map, rank_stats_fn, cfg, Ranks, NUMBERTORANKS, colors, table_cfg, agent_dict, level_fn=None):
    # Per-enemy data is gathered concurrently up front; rows are then
    # assembled in the original enemy_puuids order (deterministic rendering
    # unchanged from the sequential version).
    gathered = _gather_enemy_rank_stats_levels(enemy_puuids, rank_stats_fn, level_fn)
    rows = []
    for index, puuid in enumerate(enemy_puuids):
        playerRank, previousPlayerRank, ppstats = gathered[index][0]
        name = (names_map or {}).get(puuid, "")
        if name and name.split("#")[0]:
            name_cell = f"[bold yellow]{name}[/bold yellow]"
        else:
            name_cell = "[dim]Hidden[/dim]"
        rows.append(
            [
                "",
                _agent_cell((agents_map or {}).get(puuid, ""), agent_dict),
                name_cell,
                "",
                "",
                _rank_cell(playerRank, Ranks, cfg, table_cfg),
                _text(playerRank, "rr"),
                _peak_cell(playerRank, Ranks, cfg),
                _index(Ranks, previousPlayerRank, "rank"),
                _text(playerRank, "leaderboard"),
                _gradient_cell(ppstats, "hs", colors.get_hs_gradient),
                _wr_cell(playerRank, colors),
                _text(ppstats, "kd"),
                _level_cell(lambda _puuid, _level=gathered[index][1]: _level, puuid, colors),
                _rr_earned_cell(ppstats, colors),
                _gradient_cell(ppstats, "perf_bonus", colors.get_perf_bonus_color),
            ]
        )
    return rows


def _safe_rank_stats(rank_stats_fn, puuid):
    try:
        playerRank, previousPlayerRank, ppstats = rank_stats_fn(puuid)
        return playerRank, previousPlayerRank, ppstats
    except Exception:
        return None, None, None
