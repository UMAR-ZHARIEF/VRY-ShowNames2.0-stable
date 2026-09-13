"""Adaptive tolerant polling helpers for the main loop's state detection.

Postmortem (reconnect storm, logs 549-550): the old flow declared a full
DISCONNECT on a single failed state check, and the recovery ritual itself
kept the failure alive. These pure helpers implement the replacement:
independent forgettable checks, a tight poll interval while a match is
forming or live, and a failure threshold before any reconnect ritual.

Pure functions only: no I/O, no threads, no shared state.
"""

DISCONNECT_THRESHOLD = 3
ACTIVE_POLL_SECONDS = 2


def derive_game_state(private_presence):
    """sessionLoopState ('MENUS'/'PREGAME'/'INGAME') from a decoded private
    presence dict, or None when absent/unknown. Mirrors the nested
    (matchPresenceData) vs flattened structure handling in presences.py.
    """
    if not private_presence:
        return None
    if "matchPresenceData" in private_presence:
        return private_presence["matchPresenceData"].get("sessionLoopState")
    if "sessionLoopState" in private_presence:
        return private_presence["sessionLoopState"]
    return None


def next_poll_seconds(game_state, base_cooldown):
    """Seconds to sleep before the next state check. Tight (2s) while a
    match is forming or live so state changes are felt quickly; the normal
    cooldown elsewhere. Odd cooldown values (None, zero, negative) fall
    back to the tight interval rather than raising."""
    if game_state in ("PREGAME", "INGAME"):
        return ACTIVE_POLL_SECONDS
    try:
        return max(2, int(base_cooldown))
    except (TypeError, ValueError):
        return ACTIVE_POLL_SECONDS


def render_signature(game_state, players):
    """Stable, order-independent fingerprint of what one draw would show.

    players: iterable of dicts; 'puuid' required, all other keys optional:
    agent, team, rank, rr, level, name (missing keys render as '').
    Pure function: no I/O, no shared state.
    """
    parts = []
    for player in players or []:
        if not isinstance(player, dict) or not player.get("puuid"):
            continue
        parts.append(
            f"{player['puuid']}:"
            f"{player.get('agent', '')}:"
            f"{player.get('team', '')}:"
            f"{player.get('rank', '')}:"
            f"{player.get('rr', '')}:"
            f"{player.get('level', '')}:"
            f"{player.get('name', '')}"
        )
    parts.sort()
    return f"{game_state}|" + ";".join(parts)


def should_redraw(signature, last_signature, seconds_since_last_draw, force_interval=60.0):
    """True when nothing was drawn yet, the fingerprint changed, or the
    safety interval elapsed since the last draw."""
    if last_signature is None:
        return True
    if signature != last_signature:
        return True
    return seconds_since_last_draw >= force_interval


def pregame_draw_decision(elapsed_in_state, enemy_count, probe_status):
    """STRICT single print per agent select: "wait" only while the enemy
    table may still improve (probe alive, fewer than 5 enemies found, inside
    the 20s settle cap). "draw" every other case, then the caller stays
    silent for the rest of PREGAME. Pure function: no I/O."""
    if probe_status in ("polling", "partial") and enemy_count < 5 and elapsed_in_state < 20.0:
        return "wait"
    return "draw"


def should_declare_disconnected(consecutive_failures, threshold=DISCONNECT_THRESHOLD):
    """True only after `threshold` consecutive failed state checks. A single
    failure must never trigger the DISCONNECTED ritual (see module docstring)."""
    return consecutive_failures >= threshold
