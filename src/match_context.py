"""MatchContext: one object holding main.py's per-session / per-match state.

Introduced in Phase 4 step 1. This is a mechanical consolidation of the
module-level mutable globals that used to live in main.py (match cache,
probe bookkeeping, draw-gate state, menus TTL cache, etc.). Semantics are
identical to the old globals: same fields, same initial values, same TTL
rules. No behavior change.
"""
import threading
import time


class MatchContext:
    MATCH_PLAYER_CACHE_TTL_SECONDS = 300  # safety TTL
    MENUS_STATS_TTL_SECONDS = 60

    def __init__(self):
        # Server / lobby display state (carried across cycles on purpose;
        # the INGAME gamemode guard keeps the previous value at match
        # boundaries when the private presence decodes to None).
        self.server = ""
        self.team_side = None
        self.gamemode = ""

        # Probe bookkeeping: matches whose v3 probe was already dispatched.
        self.v3_probed_matches = set()

        # PREGAME enemy level cache: (match_id, puuid) -> level or None.
        self.enemy_level_cache = {}

        # Retroactive post-match name lookup: (match_id, [puuids]) or None.
        self.pending_post_match_lookup = None

        # Rank+stats cache per player for the current match, so PREGAME data
        # can be reused in INGAME: puuid -> {"playerRank", "previousPlayerRank",
        # "ppstats", "ts"}.
        self.match_player_cache = {
            "match_id": None,
            "players": {},
        }

        # Consecutive failed state checks before declaring DISCONNECTED
        # (reconnect-storm postmortem, logs 549/550).
        self.consecutive_state_failures = 0

        # Draw-gate state: render only when the output would change.
        self.last_drawn_signature = None
        self.last_draw_state = None
        self.last_draw_time = 0.0
        self.last_drawn_enemy_count = -1
        self.last_enemy_names = {}
        self.state_entered_at = 0.0

        # MENUS party rank/stats cache: puuid -> (ts, playerRank,
        # previousPlayerRank, ppstats). Party data barely changes between
        # menu cycles, so it is not refetched every cycle.
        self.menus_stats_cache = {}
        self.menus_stats_lock = threading.Lock()

    def reset_match_player_cache(self, match_id=None):
        self.match_player_cache["match_id"] = match_id
        self.match_player_cache["players"] = {}

    def ensure_match_player_cache(self, match_id):
        if not match_id:
            return
        if self.match_player_cache["match_id"] != match_id:
            self.reset_match_player_cache(match_id)
            return
        now = time.time()
        expired = [
            puuid
            for puuid, cached in self.match_player_cache["players"].items()
            if (now - cached.get("ts", now)) > self.MATCH_PLAYER_CACHE_TTL_SECONDS
        ]
        for puuid in expired:
            del self.match_player_cache["players"][puuid]

    def menus_stats_get(self, subject):
        """Cached (playerRank, previousPlayerRank, ppstats) within TTL, else None."""
        now = time.time()
        with self.menus_stats_lock:
            cached = self.menus_stats_cache.get(subject)
            if cached is not None and now - cached[0] < self.MENUS_STATS_TTL_SECONDS:
                return cached[1], cached[2], cached[3]
        return None

    def menus_stats_set(self, subject, playerRank, previousPlayerRank, ppstats):
        with self.menus_stats_lock:
            self.menus_stats_cache[subject] = (
                time.time(),
                playerRank,
                previousPlayerRank,
                ppstats,
            )
