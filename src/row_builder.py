"""Shared table-cell formatters.

Single home for the cell formulas that used to be copy-pasted across the
INGAME, PREGAME and MENUS loops in main.py and the enemy-table helpers in
src/enemy_table_data.py. All functions are pure (no I/O) and return exactly
what the previous inline code returned for any reachable input; the guarded
"?" fallbacks only trigger for dict keys that could never be missing from a
real rank/stats payload.
"""
from colr import color as colr


def safe_text(data, key):
    try:
        value = data[key]
    except (KeyError, TypeError):
        return "?"
    return value if value is not None else "?"


def safe_index(collection, data, key):
    try:
        return collection[data[key]]
    except (KeyError, TypeError, IndexError):
        return "?"


def agent_cell(character_id, agent_dict):
    if not character_id:
        return "[dim]?[/dim]"
    return colr(agent_dict.get(str(character_id).lower(), "Unknown"), fore=(255, 255, 255))


def rank_cell(playerRank, Ranks, cfg, table_cfg):
    rank = safe_index(Ranks, playerRank, "rank")
    if rank == "?":
        return rank
    if cfg.get_feature_flag("aggregate_rank_rr") and table_cfg.get("rr"):
        rr = safe_text(playerRank, "rr")
        if rr != "?":
            return f"{rank} ({rr})"
    return rank


def peak_act_suffix(playerRank, cfg):
    try:
        ep = str(playerRank["peakrankep"])
        act = playerRank["peakrankact"]
    except (KeyError, TypeError):
        return ""
    has_letter = any(c.isalpha() for c in ep)
    act_suffix = f" ({ep}a{act})" if has_letter else f" (e{ep}a{act})"
    if not cfg.get_feature_flag("peak_rank_act"):
        act_suffix = ""
    return act_suffix


def peak_cell(playerRank, Ranks, cfg):
    peak = safe_index(Ranks, playerRank, "peakrank")
    if peak == "?":
        return peak
    return peak + peak_act_suffix(playerRank, cfg)


def gradient_cell(data, key, formatter):
    value = safe_text(data, key)
    if value == "?":
        return value
    try:
        return formatter(value)
    except Exception:
        return str(value)


def wr_cell(playerRank, colors):
    wr = safe_text(playerRank, "wr")
    if wr == "?":
        return wr
    games = safe_text(playerRank, "numberofgames")
    try:
        return colors.get_wr_gradient(wr) + f" ({games})"
    except Exception:
        return f"{wr} ({games})"


def rr_earned_cell(ppstats, colors):
    earned = safe_text(ppstats, "RankedRatingEarned")
    penalty = safe_text(ppstats, "AFKPenalty")
    if earned == "?" or penalty == "?":
        return earned
    try:
        return colors.get_rr_gradient(earned, penalty)
    except Exception:
        return str(earned)


def level_cell(level_fn, puuid, colors):
    # Enemy levels come from a caller-supplied lookup (last finished match),
    # the same source the ally path uses for hidden-level players.
    if level_fn is None:
        return "?"
    try:
        level = level_fn(puuid)
    except Exception:
        return "?"
    # bool is an int subclass; a boolean is never a real account level.
    if isinstance(level, bool) or not isinstance(level, int) or level <= 0:
        return "?"
    return colors.level_to_color(level)
