class PlayerStats:
    def __init__(self, Requests, log, config):
        self.Requests = Requests
        self.log = log
        self.config = config
        self.match_details_cache = {}
        self._last_match_id_by_puuid = {}
        self._level_fallback_cache = {}
        self._level_fallback_logged = set()

    def clear_runtime_cache(self):
        """Clear transient runtime caches (safe to call on MENUS/new match)."""
        self.match_details_cache.clear()
        self._last_match_id_by_puuid.clear()
        self._level_fallback_cache.clear()
        self._level_fallback_logged.clear()

    def _default_stats(self):
        return {
            "kd": "N/A",
            "hs": "N/A",
            "RankedRatingEarned": "N/A",
            "AFKPenalty": "N/A",
            "perf_bonus": "N/A",
        }

    def _get_match_details_cached(self, match_id):
        """Fetch /match-details once per match_id for this runtime session."""
        if not match_id:
            return None

        if match_id in self.match_details_cache:
            return self.match_details_cache[match_id]

        match_response = self.Requests.fetch(
            "pd",
            f"/match-details/v1/matches/{match_id}",
            "get",
        )

        if match_response.status_code == 404:
            self.match_details_cache[match_id] = None
            return None

        match_data = match_response.json()
        self.match_details_cache[match_id] = match_data
        return match_data

    def get_stats(self, puuid):
        # Early exit if no stats are required
        if not self.config.get_table_flag(
            "headshot_percent"
        ) and not self.config.get_table_flag("kd"):
            return self._default_stats()

        # Fetch competitive updates
        try:
            response = self.Requests.fetch(
                "pd",
                f"/mmr/v1/players/{puuid}/competitiveupdates?startIndex=0&endIndex=10&queue=competitive",
                "get",
            )
            matches = response.json().get("Matches", [])
            if not matches:
                # Fallback: try any-queue match history for name + casual stats
                try:
                    history_response = self.Requests.fetch(
                        "pd",
                        f"/match-history/v1/history/{puuid}?startIndex=0&endIndex=1",
                        "get",
                    )
                    history = history_response.json().get("History", [])
                    if history:
                        fallback_match_id = history[0].get("MatchID")
                        if fallback_match_id:
                            self._last_match_id_by_puuid[puuid] = fallback_match_id
                            self.log(f"match history fallback for {puuid}: {fallback_match_id}")
                            return self._get_stats_from_any_match(puuid, fallback_match_id)
                except Exception as e:
                    self.log(f"Error fetching match history fallback: {e}")
                return self._default_stats()
        except Exception as e:
            self.log(f"Error fetching competitive updates: {e}")
            return self._default_stats()

        match_summary = matches[0]
        match_id = match_summary.get("MatchID")
        if not match_id:
            return self._default_stats()
        self._last_match_id_by_puuid[puuid] = match_id

        bonuses = [m.get("RankedRatingPerformanceBonus", 0) for m in matches]
        total = len(bonuses)
        triggered = [b for b in bonuses if b > 0]
        if triggered:
            avg_bonus = round(sum(triggered) / len(triggered), 1)
            perf_bonus_str = f"{avg_bonus} ({len(triggered)}/{total})"
        elif total > 0:
            perf_bonus_str = f"0.0 (0/{total})"
        else:
            perf_bonus_str = "N/A"

        try:
            match_data = self._get_match_details_cached(match_id)
            if match_data is None:
                return self._default_stats()
        except Exception as e:
            self.log(f"Error fetching match details: {e}")
            return self._default_stats()

        stats = self._process_match_data(puuid, match_data, match_summary)
        stats["perf_bonus"] = perf_bonus_str
        return stats

    def _process_match_data(self, puuid, match_data, match_summary):
        total_hits, total_headshots, kills, deaths = 0, 0, 0, 0

        # Extract round stats
        for rround in match_data.get("roundResults", []):
            for player in rround.get("playerStats", []):
                if player.get("subject") == puuid:
                    for hits in player.get("damage", []):
                        total_hits += (
                            hits.get("legshots", 0)
                            + hits.get("bodyshots", 0)
                            + hits.get("headshots", 0)
                        )
                        total_headshots += hits.get("headshots", 0)

        # Extract overall player stats
        for player in match_data.get("players", []):
            if player.get("subject") == puuid:
                stats = player.get("stats", {})
                kills = stats.get("kills", 0)
                deaths = stats.get("deaths", 0)
                break

        # Calculate KD
        kd = round(kills / deaths, 2) if deaths else kills

        ranked_rating_earned = match_summary.get("RankedRatingEarned", "N/A")
        afk_penalty = match_summary.get("AFKPenalty", "N/A")

        # Compile final stats
        final_stats = {
            "kd": kd,
            "hs": round((total_headshots / total_hits) * 100) if total_hits else "N/A",
            "RankedRatingEarned": ranked_rating_earned,
            "AFKPenalty": afk_penalty,
        }
        return final_stats

    def _get_stats_from_any_match(self, puuid, match_id):
        """Extract HS% and KD from a non-competitive match. Rank/WR/RR stay N/A."""
        try:
            match_data = self._get_match_details_cached(match_id)
            if not match_data:
                return self._default_stats()
            dummy_summary = {"RankedRatingEarned": "N/A", "AFKPenalty": "N/A"}
            stats = self._process_match_data(puuid, match_data, dummy_summary)
            stats["perf_bonus"] = "N/A"
            return stats
        except Exception as e:
            self.log(f"Error fetching casual stats: {e}")
            return self._default_stats()

    def get_level_from_match_details(self, puuid, match_id):
        """Returns accountLevel for a puuid from cached match details, or None."""
        match_data = self._get_match_details_cached(match_id)
        if not match_data:
            return None
        for player in match_data.get("players", []):
            if player.get("subject") == puuid:
                level = player.get("accountLevel")
                if level:
                    return level
        return None

    def get_level_with_fallback(self, puuid, max_matches=5):
        """Account level for a puuid, walking any-queue history if needed.

        Level reads come from match details, and Riot prunes old matches, so a
        player's newest match can 404 (live evidence: log-546). This tries the
        known competitive match first, then walks the recent history list until
        one match still has details. The outcome (level or None) is cached per
        puuid, logged at most once, and the method never raises.
        """
        if puuid in self._level_fallback_cache:
            return self._level_fallback_cache[puuid]

        level = self.get_level_from_match_details(
            puuid, self._last_match_id_by_puuid.get(puuid)
        )
        if level is None:
            try:
                history_response = self.Requests.fetch(
                    "pd",
                    f"/match-history/v1/history/{puuid}?startIndex=0&endIndex={max_matches}",
                    "get",
                )
                history = history_response.json()
                entries = history.get("History", []) if isinstance(history, dict) else []
                for entry in entries:
                    if not isinstance(entry, dict) or not entry.get("MatchID"):
                        continue
                    level = self.get_level_from_match_details(puuid, entry["MatchID"])
                    if level:
                        break
            except Exception:
                level = None
            if puuid not in self._level_fallback_logged:
                self._level_fallback_logged.add(puuid)
                if level:
                    self.log(f"level fallback: {puuid[:8]} via older match")
                else:
                    self.log(f"level unresolved: {puuid[:8]} (no accessible match details)")

        self._level_fallback_cache[puuid] = level
        return level

if __name__ == "__main__":
    from constants import version
    from requestsV import Requests
    from logs import Logging
    from errors import Error
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    Logging = Logging()
    log = Logging.log
    ErrorSRC = Error(log)
    Requests = Requests(version, log, ErrorSRC)

    player_stats = PlayerStats(Requests, log, "a")
    result = player_stats.get_stats("963ad672-61e1-537e-8449-06ece1a5ceb7")
    print(result)
