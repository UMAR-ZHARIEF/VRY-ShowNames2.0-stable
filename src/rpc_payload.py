"""Pure Discord presence payload builder + typed RpcOverrides.

The card-composition logic was moved here verbatim from src/rpc.py's
_build_payload so it is unit-testable without threads, a Discord
connection, or any global state. rpc.py keeps only the connection,
threading, retry and dedupe machinery and delegates here.

Overrides come from config.json section "rpc_overrides" and are parsed and
validated once at startup (RpcOverrides.from_config). Every override
defaults to "real": until the user changes a value, the card shows exactly
today's real values. Invalid values get one named warning and fall back to
"real"; the card never breaks.
"""
import re
import time
from dataclasses import dataclass

from src.constants import NUMBERTORANKS

_REAL = "real"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain_rank_names():
    """Plain display names for every tier in NUMBERTORANKS (ANSI stripped)."""
    return [_ANSI_RE.sub("", str(entry)).strip() for entry in NUMBERTORANKS]


@dataclass
class RpcOverrides:
    """Parsed Discord appearance overrides. None always means "real"."""
    rank_tier: int | None = None
    rank_display: str | None = None
    agent_display: str | None = None
    forced_state: str | None = None
    map_display: str | None = None
    party_size: int | None = None
    party_max: int | None = None
    score_ally: int | None = None
    score_enemy: int | None = None
    game_mode_display: str | None = None

    @classmethod
    def all_real(cls):
        return cls()

    @classmethod
    def from_config(cls, cfg, log=None, agent_dict=None, map_dict=None):
        """Parse the "rpc_overrides" config section once at startup.

        rank: "real" or a rank name (validated against NUMBERTORANKS).
        agent: "real" or an agent name (validated against agent_dict when it
            is provided; without the table the override fails safe to real).
        rpc_state: "real" (follow the real game) or one of "lobby",
            "agent_select", "ingame" (pin the card to that look). Invalid
            values produce one named warning and fall back to real.
        map: "real" (the real map) or a map display name (validated against
            map_dict values when it is provided; without the table the
            override fails safe to real).
        party_size/party_max/score_ally/score_enemy: "real" or a whole
            number written as a string or int.

        Any invalid value produces one named warning and falls back to real.
        """
        # cfg arrives as a dict in tests and as the Config object in the app;
        # Config exposes sections as attributes (setattr), never via .get().
        raw = cfg.get("rpc_overrides") if isinstance(cfg, dict) else getattr(cfg, "rpc_overrides", None)
        section = dict(raw or {})

        def _warn(key, detail):
            if log is not None:
                log(f'config: rpc_overrides "{key}" {detail}; using real')

        def _as_int(key, raw):
            try:
                return int(str(raw).strip())
            except (TypeError, ValueError):
                _warn(key, f'is not "real" or a whole number (got "{raw}")')
                return None

        rank_tier = rank_display = None
        rank_raw = str(section.get("rank", _REAL)).strip()
        if rank_raw.lower() != _REAL:
            for tier, plain in enumerate(_plain_rank_names()):
                if plain.lower() == rank_raw.lower():
                    rank_tier, rank_display = tier, plain
                    break
            else:
                _warn("rank", f'is not "real" or a known rank name (got "{rank_raw}")')

        agent_display = None
        agent_raw = str(section.get("agent", _REAL)).strip()
        if agent_raw.lower() != _REAL:
            # agent_dict is keyed by agent UUIDs (values are display names),
            # so a human name is matched against the VALUES, mirroring the
            # map validation above.
            matched = None
            if agent_dict:
                for _uuid, _name in agent_dict.items():
                    if str(_name).strip().lower() == agent_raw.lower():
                        matched = str(_name).strip()
                        break
            if matched:
                agent_display = matched
            else:
                _warn("agent", f'is not "real" or a known agent name (got "{agent_raw}")')

        forced_state = None
        state_raw = str(section.get("rpc_state", _REAL)).strip().lower()
        if state_raw != _REAL:
            forced = {"lobby": "MENUS", "agent_select": "PREGAME", "ingame": "INGAME"}.get(state_raw)
            if forced is not None:
                forced_state = forced
            else:
                _warn("rpc_state", f'is not "real", "lobby", "agent_select" or "ingame" (got "{state_raw}")')

        map_display = None
        map_raw = str(section.get("map", _REAL)).strip()
        if map_raw.lower() != _REAL:
            if map_dict:
                for _key, _value in map_dict.items():
                    if str(_value).strip().lower() == map_raw.lower():
                        map_display = str(_value).strip()
                        break
                else:
                    _warn("map", f'is not "real" or a known map name (got "{map_raw}")')
            else:
                _warn("map", "cannot be validated (no map table); using real")

        party_size = None if str(section.get("party_size", _REAL)).strip().lower() == _REAL \
            else _as_int("party_size", section.get("party_size"))
        party_max = None if str(section.get("party_max", _REAL)).strip().lower() == _REAL \
            else _as_int("party_max", section.get("party_max"))
        score_ally = None if str(section.get("score_ally", _REAL)).strip().lower() == _REAL \
            else _as_int("score_ally", section.get("score_ally"))
        score_enemy = None if str(section.get("score_enemy", _REAL)).strip().lower() == _REAL \
            else _as_int("score_enemy", section.get("score_enemy"))

        # game_mode is free text by design: there is no fixed vocabulary of
        # modes to validate against ("Unrated", "Premier", anything goes).
        # Only an empty value is invalid: that is a configured override with
        # nothing in it, which gets one named warning and falls back to real.
        game_mode_display = None
        game_mode_raw = str(section.get("game_mode", _REAL)).strip()
        if game_mode_raw.lower() != _REAL:
            if game_mode_raw:
                game_mode_display = game_mode_raw
            else:
                _warn("game_mode", 'is empty; using real')

        return cls(
            rank_tier=rank_tier,
            rank_display=rank_display,
            agent_display=agent_display,
            forced_state=forced_state,
            map_display=map_display,
            party_size=party_size,
            party_max=party_max,
            score_ally=score_ally,
            score_enemy=score_enemy,
            game_mode_display=game_mode_display,
        )


def build_presence_payload(presence, data, overrides, map_dict, gamemodes, colors,
                           last_loop_state=None, start_time=None, now=None):
    """Compose the Discord payload. Returns (payload|None, loop_state, start_time).

    Pure: no I/O, no globals. The caller owns last_loop_state and
    start_time and applies the returned (possibly unchanged) values.
    """
    if now is None:
        now = time.time()

    if not presence or not presence.get("isValid"):
        return None, last_loop_state, start_time

    # Temp fix: Riot is swapping between nested and flat API structures.
    session_state = None
    match_map = ""
    party_size = 0
    max_party = 0
    party_access = ""
    party_state = ""

    if "matchPresenceData" in presence:  # Check for nested structure
        match_data = presence.get("matchPresenceData", {}) or {}
        party_data = presence.get("partyPresenceData", {}) or {}

        session_state = match_data.get("sessionLoopState")
        match_map = match_data.get("matchMap", "")
        party_size = party_data.get("partySize")
        max_party = party_data.get("maxPartySize")
        party_access = party_data.get("partyAccessibility")
        party_state = party_data.get("partyState")
    elif "sessionLoopState" in presence:  # Check for flattened structure
        session_state = presence.get("sessionLoopState")
        match_map = presence.get("matchMap", "")
        party_size = presence.get("partySize")
        max_party = presence.get("maxPartySize")
        party_access = presence.get("partyAccessibility")
        party_state = presence.get("partyState")
    else:
        # No known structure found, log and fail
        session_state = presence["matchPresenceData"]["sessionLoopState"]

    # Party overrides apply to all three states from a single point.
    if overrides.party_size is not None:
        party_size = overrides.party_size
    if overrides.party_max is not None:
        max_party = overrides.party_max

    if not session_state:
        return None, last_loop_state, start_time

    # Pinned card: a forced rpc_state replaces the real sessionLoopState, so
    # real state changes produce no visible change and the timer keeps
    # counting without reset (the reset below fires only when the forced
    # state itself changes, e.g. unpinning back to real).
    if overrides.forced_state is not None:
        session_state = overrides.forced_state

    if session_state != last_loop_state:
        start_time = now
        last_loop_state = session_state

    # Rank badge (small corner image) is the same slot in all three states.
    # When no rank data exists yet, omit the badge entirely instead of
    # sending the literal asset name "None", which Discord silently drops.
    if overrides.rank_tier is not None:
        rank_image = str(overrides.rank_tier)
    elif data.get("rank") is not None:
        rank_image = str(data.get("rank"))
    else:
        rank_image = None
    rank_text = overrides.rank_display or data.get("rank_name")
    if rank_image is None:
        rank_text = None

    if session_state == "INGAME":
        real_agent = None
        if data.get("agent") not in (None, "") and getattr(colors, "agent_dict", None):
            real_agent = colors.agent_dict.get(data.get("agent", "").lower())
        agent_display = overrides.agent_display or real_agent or ""

        gamemode = "Custom Game" if presence.get("provisioningFlow") == "CustomGame" else gamemodes.get(presence.get("queueId"))
        if overrides.game_mode_display is not None:
            gamemode = overrides.game_mode_display

        ally = overrides.score_ally if overrides.score_ally is not None else presence.get("partyOwnerMatchScoreAllyTeam")
        enemy = overrides.score_enemy if overrides.score_enemy is not None else presence.get("partyOwnerMatchScoreEnemyTeam")
        details = f"{gamemode} // {ally} - {enemy}"
        if agent_display:
            details = f"{details} · {agent_display}"

        match_map = (match_map or "").lower()
        mapText = map_dict.get(match_map)
        if mapText == "The Range":
            mapImage = "splash_range_square"
            details = "in Range"
            if agent_display:
                details = f"in Range · {agent_display}"
        else:
            mi = map_dict.get(match_map)
            mapImage = f"splash_{mi}_square".lower() if mi else None

        # Map override replaces the real splash in the two map-bearing cards.
        if overrides.map_display is not None:
            mapText = overrides.map_display
            mapImage = f"splash_{overrides.map_display}_square".lower()

        if not mapText:
            mapText = None
            mapImage = None

        return dict(
            state=f"In a Party ({party_size} of {max_party})",
            details=details,
            large_image=mapImage,
            large_text=mapText,
            small_image=rank_image,
            small_text=rank_text,
            start=int(start_time),
        ), last_loop_state, start_time

    if session_state == "MENUS":
        is_idle = presence.get("isIdle")
        image = "game_icon_yellow" if is_idle else "game_icon"
        image_text = "VALORANT - Idle" if is_idle else "VALORANT - Online"

        party_string = "Open Party" if party_access == "OPEN" else "Closed Party"

        gamemode = "Custom Game" if party_state == "CUSTOM_GAME_SETUP" else gamemodes.get(presence.get("queueId"))
        if overrides.game_mode_display is not None:
            gamemode = overrides.game_mode_display

        return dict(
            state=f"{party_string} ({party_size} of {max_party})",
            details=f" Lobby - {gamemode}",
            large_image=image,
            large_text=image_text,
            small_image=rank_image,
            small_text=rank_text,
        ), last_loop_state, start_time

    if session_state == "PREGAME":
        is_custom = presence.get("provisioningFlow") == "CustomGame" or party_state == "CUSTOM_GAME_SETUP"
        gamemode = "Custom Game" if is_custom else gamemodes.get(presence.get("queueId"))
        if overrides.game_mode_display is not None:
            gamemode = overrides.game_mode_display

        match_map = (match_map or "").lower()
        mapText = map_dict.get(match_map)
        mapImage = f"splash_{mapText}_square".lower() if mapText else None

        # Map override replaces the real splash in the two map-bearing cards.
        if overrides.map_display is not None:
            mapText = overrides.map_display
            mapImage = f"splash_{overrides.map_display}_square".lower()

        return dict(
            state=f"In a Party ({party_size} of {max_party})",
            details=f"Agent Select - {gamemode}",
            large_image=mapImage,
            large_text=mapText if mapText else None,
            small_image=rank_image,
            small_text=rank_text,
        ), last_loop_state, start_time

    return None, last_loop_state, start_time
