import json
import threading
import time

from src.http_pool import pooled_requests

# HTTP from this module goes through the shared keep-alive pool in
# src/http_pool.py, bound under the familiar name so each call site keeps
# its exact shape and its explicit verify= flag. Every call here is remote
# and verifies TLS (guard-tested in tests/test_secrets_redaction.py); the
# multi-shard probe gets one lazily created, cached session per shard via
# the pool's (pd, shard) family routing.
requests = pooled_requests

class Names:

    def __init__(self, Requests, log, config=None):
        self.Requests = Requests
        self.log = log
        self.config = config
        # Per-process memo of successfully resolved names, so repeated
        # get_multiple_names_from_puuid calls only PUT the puuids we have
        # never resolved. Blank/hidden results are deliberately NOT memoized:
        # they keep being retried, exactly as before.
        self._resolved_memo = {}
        self._resolved_memo_lock = threading.Lock()

    def get_multiple_names_from_puuid(self, puuids):
        puuids = list(puuids or [])
        with self._resolved_memo_lock:
            missing = [p for p in puuids if p not in self._resolved_memo]

        if missing:
            response = requests.put(self.Requests.pd_url + "/name-service/v2/players", headers=self.Requests.get_headers(), json=missing, verify=True)

            if 'errorCode' in response.json():
                self.log(f'{response.json()["errorCode"]}, new token retrieved')
                response = requests.put(self.Requests.pd_url + "/name-service/v2/players", headers=self.Requests.get_headers(refresh=True), json=missing, verify=True)

            name_dict = {player["Subject"]: f"{player['GameName']}#{player['TagLine']}"
                         for player in response.json()}
            with self._resolved_memo_lock:
                for puuid, name in name_dict.items():
                    # Memoize resolved names only; blanks keep being retried.
                    if name and name.split("#")[0]:
                        self._resolved_memo[puuid] = name

        with self._resolved_memo_lock:
            # Every requested puuid keeps its key ("" when unresolved) so
            # callers' names[puuid] accesses behave exactly as before.
            return {p: self._resolved_memo.get(p, "") for p in puuids}

    def get_names_from_puuids(self, players):
        players_puuid = []
        for player in players:
            players_puuid.append(player["Subject"])
        return self.get_multiple_names_from_puuid(players_puuid)

    def get_players_puuid(self, Players):
        return [player["Subject"] for player in Players]

    def patch_hidden_names(self, names_dict, presence_list):
        """For players whose name resolved to '#', fall back to presence data."""
        presence_map = {}
        for p in presence_list:
            if "puuid" not in p:
                continue
            gn = p.get("game_name", "")
            gt = p.get("game_tag", "")
            if gn:
                presence_map[p["puuid"]] = f"{gn}#{gt}" if gt else gn
            else:
                combined = p.get("name", "")
                if combined and combined.split("#")[0]:
                    presence_map[p["puuid"]] = combined

        for puuid, name in names_dict.items():
            if not name.split("#")[0]:
                fallback = presence_map.get(puuid, "")
                if fallback and fallback.split("#")[0]:
                    names_dict[puuid] = fallback
        return names_dict

    def patch_from_cache(self, names_dict, name_cache):
        """For any name still blank, look it up in the persistent cache."""
        for puuid, name in names_dict.items():
            if not name.split("#")[0]:
                cached = name_cache.get(puuid)
                if cached:
                    names_dict[puuid] = cached
        return names_dict

    def update_cache(self, names_dict, name_cache, source):
        """Save all resolved (non-blank) names to the persistent cache."""
        for puuid, name in names_dict.items():
            if name.split("#")[0]:
                name_cache.set(puuid, name, source=source)

    def retroactive_post_match_lookup(self, blank_puuids, match_id, name_cache):
        """After a match ends, try to retroactively get names from match-details."""
        if not blank_puuids or not match_id:
            return {}
        try:
            response = requests.get(
                f"{self.Requests.pd_url}/match-details/v1/matches/{match_id}",
                headers=self.Requests.get_headers(),
                verify=True,
                timeout=5,
            )
            if response.status_code != 200:
                return {}
            data = response.json()
            resolved = {}
            for player in data.get("players", []):
                puuid = player.get("subject")
                if puuid in blank_puuids:
                    name = player.get("gameName", "")
                    tag = player.get("tagLine", "")
                    if name and "Hidden" not in name:
                        full = f"{name}#{tag}" if tag else name
                        resolved[puuid] = full
                        if name_cache is not None:
                            name_cache.set(puuid, full, source="post-match")
            if resolved:
                self.log(f"Retroactive resolved {len(resolved)} names from {match_id}")
            return resolved
        except Exception:
            return {}

    # =========================================================================
    # EXPERIMENTAL RESOLVERS — try every plausible workaround for the
    # 'Anonymize My Riot ID' redaction. Each method is independent and logged
    # so failures are visible. Use resolve_aggressive() as the umbrella entry.
    # =========================================================================

    def harvest_from_match_details(self, match_id):
        """Pull every {puuid, name} pair from a match-details response.
        Riot's anonymization is write-time, so old matches retain real names.
        """
        out = {}
        try:
            r = requests.get(
                f"{self.Requests.pd_url}/match-details/v1/matches/{match_id}",
                headers=self.Requests.get_headers(),
                verify=True,
                timeout=8,
            )
            if r.status_code != 200:
                self.log(f"match-details {match_id[:8]}: HTTP {r.status_code}")
                return out
            data = r.json()
            for player in data.get("players", []):
                puuid = player.get("subject")
                gn = player.get("gameName", "")
                tl = player.get("tagLine", "")
                if puuid and gn and "Hidden" not in gn:
                    out[puuid] = f"{gn}#{tl}" if tl else gn
        except Exception as e:
            self.log(f"match-details {match_id[:8]}: {type(e).__name__}: {e}")
        return out

    def bootstrap_from_match_history(self, own_puuid, name_cache,
                                     player_stats=None, max_matches=25):
        """Phase 2: walk recent matches, harvest every co-player name,
        write to persistent cache.
        """
        if not own_puuid:
            return 0
        try:
            r = self.Requests.fetch(
                url_type="pd",
                endpoint=f"/match-history/v1/history/{own_puuid}"
                         f"?startIndex=0&endIndex={max_matches}",
                method="get",
            )
        except Exception as e:
            self.log(f"bootstrap: history fetch failed: {e}")
            return 0
        if r is None:
            self.log("bootstrap: history returned None")
            return 0
        try:
            history = r.json().get("History", [])
        except Exception as e:
            self.log(f"bootstrap: history parse failed: {e}")
            return 0
        if not history:
            self.log("bootstrap: history empty")
            return 0
        harvested = 0
        for entry in history:
            mid = entry.get("MatchID")
            if not mid:
                continue
            harvested_pairs = self.harvest_from_match_details(mid)
            for puuid, full_name in harvested_pairs.items():
                if not name_cache.get(puuid):
                    name_cache.set(puuid, full_name, source="bootstrap-history")
                    harvested += 1
        if harvested:
            name_cache.save_now()
        self.log(f"bootstrap: {harvested} new names from {len(history)} matches")
        return harvested

    def resolve_aggressive(self, names_dict, name_cache=None,
                           presence_list=None, match_id=None,
                           game_state=None, own_puuid=None):
        """Umbrella resolver: tries every method in order from cheapest to
        most expensive. Each successful resolution is also written to the
        persistent cache (if provided).
        """
        blank = [p for p, n in names_dict.items() if not n.split("#")[0]]
        if not blank:
            return names_dict
        self.log(f"resolve_aggressive: starting with {len(blank)} blank puuids "
                 f"(state={game_state}, match={str(match_id)[:8] if match_id else None})")

        def _apply(source_name, harvested):
            if not harvested:
                return 0
            applied = 0
            for puuid in list(blank):
                if puuid in harvested:
                    full = harvested[puuid]
                    names_dict[puuid] = full
                    if name_cache is not None:
                        name_cache.set(puuid, full, source=source_name)
                    blank.remove(puuid)
                    applied += 1
            if applied:
                self.log(f"resolve_aggressive[{source_name}]: filled {applied}, "
                         f"remaining={len(blank)}")
            return applied

        # 1. Cheapest: persistent cache.
        if name_cache is not None and blank:
            cache_hits = {p: name_cache.get(p) for p in blank if name_cache.get(p)}
            _apply("cache", cache_hits)

        # 2. Match-details for the current match (post-write redaction varies).
        if blank and match_id:
            _apply("match-details-current",
                   self.harvest_from_match_details(match_id))

        # 3. Full-probe umbrella (multi-region name-service — THE FIX).
        if blank:
            _apply("full-probe", self.harvest_blank_puuids_full_probe(blank, name_cache=name_cache))

        if blank:
            self.log(f"resolve_aggressive: GIVE UP on {len(blank)} puuids: "
                     f"{[p[:8] for p in blank]}")
        return names_dict

    def try_multi_region_name_service(self, puuids):
        """Try /name-service/v2/players against every shard's PD host. The
        name-service on the user's home shard may return blank while the
        account's home shard still serves a name. Skip the user's home shard.
        """
        if not puuids:
            return {}
        shards = ("na", "eu", "latam", "br", "kr", "ap", "pbe")
        home_shard = (getattr(self.Requests, "shard", None)
                      or getattr(self.Requests, "region", None) or "").lower()
        out = {}
        for shard in shards:
            if shard == home_shard:
                continue
            try:
                url = f"https://pd.{shard}.a.pvp.net/name-service/v2/players"
                r = requests.put(url, headers=self.Requests.get_headers(),
                                 json=list(puuids), verify=True, timeout=4)
                self.log(f"[multi-region] shard={shard} -> HTTP {r.status_code}")
                if r.status_code != 200:
                    continue
                try:
                    data = r.json()
                except Exception:
                    continue
                if not isinstance(data, list):
                    continue
                for p in data:
                    if not isinstance(p, dict):
                        continue
                    puuid = p.get("Subject") or p.get("subject")
                    gn = p.get("GameName") or p.get("gameName") or ""
                    tl = p.get("TagLine") or p.get("tagLine") or ""
                    if puuid and gn:
                        full = f"{gn}#{tl}" if tl else gn
                        out[puuid] = full
                        self.log(f"[multi-region] HIT shard={shard} {puuid[:8]} = {full}")
            except Exception as e:
                self.log(f"[multi-region] shard={shard} exception: "
                         f"{type(e).__name__}: {e}")
        if out:
            self.log(f"[multi-region] resolved {len(out)}/{len(puuids)}")
        return out

    def harvest_blank_puuids_full_probe(self, blank_puuids, name_cache=None):
        """Brute-force resolver. Now slimmed to only the multi-region name-service
        method (only one that actually works in production)."""
        if not blank_puuids:
            return {}
        self.log(f"[full-probe] starting for {len(blank_puuids)} puuids")
        aggregated = {}
        try:
            aggregated.update(self.try_multi_region_name_service(blank_puuids))
        except Exception as e:
            self.log(f"[full-probe] try_multi_region_name_service "
                     f"exception: {type(e).__name__}: {e}")
        result = {}
        for puuid in blank_puuids:
            full = aggregated.get(puuid)
            if full and "#" in full and full.split("#")[0]:
                result[puuid] = full
                if name_cache is not None:
                    try:
                        name_cache.set(puuid, full, source="full-probe")
                    except Exception:
                        pass
        self.log(f"[full-probe] resolved {len(result)}/{len(blank_puuids)}")
        return result
