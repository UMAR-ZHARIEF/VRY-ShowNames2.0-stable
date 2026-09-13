"""V3 enemy-preview probes — untried vectors only.

Every vector in this file was NOT exercised by V1 (`enemy_probe.py`) or
V2 (`enemy_probe_v2.py`). Research across 6 parallel agents converged on
"no evidence-based vector remains" — but the user explicitly asked for
methods we have not tried, regardless of expected outcome. So this
module attempts each one and logs the result for evidence.

API-only. No memory access. No game-file modification.
"""
import re
import ssl
import json
import time
import socket
import base64
import threading
import concurrent.futures as _cf
import urllib.parse
import requests


import os
import glob
import subprocess

_PUUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

# Cache the clientconfig response per session — same payload covers
# chat + rms host derivation, so we only need to fetch it once.
_CLIENTCONFIG_CACHE = {}
_CLIENTCONFIG_LOCK = threading.Lock()

# Once we discover a working XMPP host identity (via the server's
# `from=` attribute in its stream greeting), cache it so future probes
# skip the discovery roundtrip.
_XMPP_REAL_HOST = {}  # affinity -> real host string ("sa2.pvp.net")
_XMPP_REAL_HOST_LOCK = threading.Lock()

# Hard guard: only ONE active dispatch per (kind, match_id). Without
# this, two concurrent XMPP sessions for the same Riot account get
# kicked by the server and all post-bind queries return 0 bytes.
_DISPATCH_ACTIVE = set()
_DISPATCH_LOCK = threading.Lock()


def _claim_dispatch(kind, match_id):
    key = (kind, match_id)
    with _DISPATCH_LOCK:
        if key in _DISPATCH_ACTIVE:
            return False
        _DISPATCH_ACTIVE.add(key)
        return True


def _release_dispatch(kind, match_id):
    key = (kind, match_id)
    with _DISPATCH_LOCK:
        _DISPATCH_ACTIVE.discard(key)


def _v3_fetch_chattokens(requests_obj, pregame_match_id, own_puuid, log):
    """Mint MUC chat tokens for pregame/coregame/all-chat. Riot's
    pregame MUC requires `<password>{Token}</password>` inside the join
    stanza. Each endpoint returns {Token, Room}.

    Returns a list of (label, token, room) tuples for every endpoint
    that returned 200. Probes URL/method variants because the original
    `/pregame/.../chattoken` returns HTTP 500 (handler exists but is
    broken or our shape is wrong).
    """
    if not pregame_match_id:
        return []
    glz = requests_obj.glz_url
    headers = requests_obj.get_headers()
    mid = pregame_match_id
    pp = own_puuid or ""

    # First, auto-discover the user's partyId by calling the same
    # endpoint the official client uses (visible in ShooterGame.log:
    # `Party_FetchPlayer`). The response body has CurrentPartyID.
    party_id = ""
    try:
        pr = _req("GET", f"{glz}/parties/v1/players/{pp}",
                  headers=headers, timeout=4)
        log(f"[v3-chattoken] Party_FetchPlayer HTTP {pr.status_code}")
        if pr.status_code == 200:
            pdata = pr.json() if pr.text else {}
            party_id = (pdata.get("CurrentPartyID")
                        or pdata.get("PartyID")
                        or pdata.get("currentPartyID") or "")
            log(f"[v3-chattoken] discovered partyId={party_id!r}")
    except Exception as e:
        log(f"[v3-chattoken] Party_FetchPlayer exc: "
            f"{type(e).__name__}: {e}")
    # (label, method, url) — methods include POST variants for the
    # 500-returning original endpoint plus alternative URL schemes
    # mirroring coregame and parties patterns.
    targets = [
        # Original
        ("pregame-chattoken-GET", "GET",
         f"{glz}/pregame/v1/matches/{mid}/chattoken"),
        # Same path, POST
        ("pregame-chattoken-POST", "POST",
         f"{glz}/pregame/v1/matches/{mid}/chattoken"),
        # Coregame-style naming on pregame service
        ("pregame-teamchatmuctoken", "GET",
         f"{glz}/pregame/v1/matches/{mid}/teamchatmuctoken"),
        ("pregame-allchatmuctoken", "GET",
         f"{glz}/pregame/v1/matches/{mid}/allchatmuctoken"),
        # Parties-style naming on pregame service
        ("pregame-muctoken-GET", "GET",
         f"{glz}/pregame/v1/matches/{mid}/muctoken"),
        ("pregame-muctoken-POST", "POST",
         f"{glz}/pregame/v1/matches/{mid}/muctoken"),
        # Speculative team query params
        ("pregame-chattoken-team2", "GET",
         f"{glz}/pregame/v1/matches/{mid}/chattoken?team=2"),
        ("pregame-chattoken-team-red", "GET",
         f"{glz}/pregame/v1/matches/{mid}/chattoken?team=red"),
        # PUUID-keyed variants
        ("pregame-player-chattoken", "GET",
         f"{glz}/pregame/v1/players/{pp}/chattoken"),
        ("pregame-player-muctoken", "GET",
         f"{glz}/pregame/v1/players/{pp}/muctoken"),
        ("pregame-player-teamchatmuctoken", "GET",
         f"{glz}/pregame/v1/players/{pp}/teamchatmuctoken"),
        # Coregame variants (404 last run, but try POST too)
        ("coregame-allchatmuctoken-GET", "GET",
         f"{glz}/core-game/v1/matches/{mid}/allchatmuctoken"),
        ("coregame-allchatmuctoken-POST", "POST",
         f"{glz}/core-game/v1/matches/{mid}/allchatmuctoken"),
        ("coregame-teamchatmuctoken-GET", "GET",
         f"{glz}/core-game/v1/matches/{mid}/teamchatmuctoken"),
        ("coregame-player-allchatmuctoken", "GET",
         f"{glz}/core-game/v1/players/{pp}/allchatmuctoken"),
        ("coregame-player-teamchatmuctoken", "GET",
         f"{glz}/core-game/v1/players/{pp}/teamchatmuctoken"),
        # Local Riot Client chat-token endpoint shape (some tools list this)
        ("chat-v6-muctoken", "GET",
         f"{glz}/chat/v6/muctoken/{mid}"),
        # Newly-discovered from ShooterGame.log
        ("pregame-voicetoken", "GET",
         f"{glz}/pregame/v1/matches/{mid}/voicetoken"),
        ("coregame-voicetoken", "GET",
         f"{glz}/core-game/v1/matches/{mid}/voicetoken"),
    ]
    # Party v2 muctoken — official client uses /parties/v2/, we used v1
    if party_id:
        targets.append(
            ("parties-v2-muctoken", "GET",
             f"{glz}/parties/v2/parties/{party_id}/muctoken"))
        targets.append(
            ("parties-v1-muctoken", "GET",
             f"{glz}/parties/v1/parties/{party_id}/muctoken"))
        targets.append(
            ("parties-v2-voicetoken", "GET",
             f"{glz}/parties/v2/parties/{party_id}/voicetoken"))
        targets.append(
            ("parties-v1-voicetoken", "GET",
             f"{glz}/parties/v1/parties/{party_id}/voicetoken"))
    results = []
    for label, method, url in targets:
        try:
            r = _req(method, url, headers=headers, timeout=4)
            log(f"[v3-chattoken] {label} {method} HTTP {r.status_code} "
                f"len={len(r.text)}")
            if r.status_code != 200:
                if len(r.text) < 200:
                    log(f"[v3-chattoken] {label} body: {r.text!r}")
                continue
            try:
                data = r.json()
            except Exception:
                log(f"[v3-chattoken] {label} non-JSON body: "
                    f"{r.text[:200]!r}")
                continue
            tok = (data.get("Token") or data.get("token")
                   or data.get("AccessToken") or "")
            room = (data.get("Room") or data.get("room")
                    or data.get("Mucname") or data.get("MUCName")
                    or data.get("muc") or "")
            if tok and room:
                log(f"[v3-chattoken] {label} -> Room={room!r} "
                    f"Token=len={len(tok)}")
                results.append((label, tok, room))
                # Decode the JWT and dump participantPuuids
                try:
                    tparts = tok.split(".")
                    if len(tparts) >= 2:
                        tclaims = json.loads(base64.urlsafe_b64decode(
                            tparts[1] + "==="))
                        log(f"[v3-chattoken] {label} JWT keys="
                            f"{sorted(tclaims.keys())}")
                        pps = tclaims.get("participantPuuids", [])
                        if isinstance(pps, list):
                            log(f"[v3-chattoken] {label} JWT "
                                f"participantPuuids count={len(pps)}: "
                                f"{pps}")
                except Exception as e:
                    log(f"[v3-chattoken] {label} JWT decode exc: "
                        f"{type(e).__name__}: {e}")
            else:
                # Even non-Token responses might have useful info
                log(f"[v3-chattoken] {label} unexpected shape: "
                    f"keys={sorted(data.keys()) if isinstance(data, dict) else type(data).__name__} "
                    f"body_sample={r.text[:200]!r}")
        except Exception as e:
            log(f"[v3-chattoken] {label} exc: "
                f"{type(e).__name__}: {e}")
    log(f"[v3-chattoken] DONE — {len(results)} valid token(s) of "
        f"{len(targets)} probed")
    return results


def _v3_get_clientconfig(requests_obj, log):
    """Fetch Riot's published service-discovery dict via the same endpoint
    Riot's own client uses on startup. Cached for the session.
    Returns the raw dict or {} on failure."""
    with _CLIENTCONFIG_LOCK:
        if "data" in _CLIENTCONFIG_CACHE:
            return _CLIENTCONFIG_CACHE["data"]
    headers = requests_obj.get_headers()
    url = "https://clientconfig.player.riotgames.com/api/v1/config/public"
    try:
        r = _req("GET", url, headers=headers, timeout=6, params={"app": "Riot Client", "namespace": "keystone.products.valorant"})
        log(f"[v3-clientconfig] HTTP {r.status_code} len={len(r.text)}")
        if r.status_code != 200:
            with _CLIENTCONFIG_LOCK:
                _CLIENTCONFIG_CACHE["data"] = {}
            return {}
        data = r.json() if r.text else {}
        if isinstance(data, dict):
            log(f"[v3-clientconfig] got {len(data)} top-level keys")
        with _CLIENTCONFIG_LOCK:
            _CLIENTCONFIG_CACHE["data"] = data
        return data
    except Exception as e:
        log(f"[v3-clientconfig] exc: {type(e).__name__}: {e}")
        with _CLIENTCONFIG_LOCK:
            _CLIENTCONFIG_CACHE["data"] = {}
        return {}


def _extract_service_hosts(config, service_substr, affinity, log,
                           label="service"):
    """Walk the clientconfig dict for keys matching `service_substr`
    (e.g. 'chat' or 'rms') and extract any string values that look like
    a hostname. Returns list of candidate hosts (no scheme).
    """
    if not isinstance(config, dict) or not config:
        return []
    aff = (affinity or "").lower()
    candidates = []
    seen = set()

    def _add(v, src):
        if not isinstance(v, str) or not v:
            return
        v = v.replace("https://", "").replace("http://", "")
        v = v.split("/")[0].split(":")[0]
        if v and v not in seen:
            seen.add(v)
            candidates.append(v)
            log(f"[v3-clientconfig] {label} host '{v}' from key={src!r}")

    sub = service_substr.lower()
    for k, v in config.items():
        kl = k.lower()
        if sub not in kl:
            continue
        if isinstance(v, str):
            _add(v, k)
        elif isinstance(v, dict):
            for k2, v2 in v.items():
                k2l = str(k2).lower()
                if aff and aff in k2l:
                    _add(v2, f"{k}[{k2}]")
                if isinstance(v2, str) and ("host" in kl or "domain" in kl
                                            or "endpoint" in kl):
                    _add(v2, f"{k}[{k2}]")
        elif isinstance(v, list):
            for entry in v:
                if isinstance(entry, str):
                    _add(entry, k)
    return candidates


def _netstat_active_port(port, log):
    """Return set of remote IPs that have ESTABLISHED TCP connections to
    `port`. Pure read of the OS connection table — no game files,
    no memory access. Windows-only (netstat -an)."""
    try:
        result = subprocess.run(
            ["netstat", "-an"],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        log(f"[v3-netstat] subprocess exc: {type(e).__name__}: {e}")
        return set()
    if result.returncode != 0:
        log(f"[v3-netstat] netstat exit={result.returncode}")
        return set()
    hosts = set()
    suffix = f":{port}"
    for line in result.stdout.splitlines():
        if suffix not in line:
            continue
        if "ESTABLISHED" not in line.upper():
            continue
        parts = line.split()
        for tok in parts:
            if tok.endswith(suffix):
                host_part = tok.rsplit(":", 1)[0]
                if host_part and not host_part.startswith("127.") \
                        and not host_part.startswith("0.") \
                        and host_part != "[::]" \
                        and "[" not in host_part:
                    hosts.add(host_part)
    if hosts:
        log(f"[v3-netstat] port {port} ESTABLISHED -> {sorted(hosts)}")
    else:
        log(f"[v3-netstat] no ESTABLISHED connections on port {port}")
    return hosts

# Shared state: confirmed enemy puuid -> "GameName#TagLine" per match_id.
# main.py reads this from the table render path so any vector that ever
# resolves a real enemy will populate the enemy preview table.
_V3_RESULTS = {}
_V3_LOCK = threading.Lock()


def get_confirmed_names(match_id):
    """Return dict {puuid_lower: 'GameName#TagLine'} of confirmed real
    enemies for the given match, or {} if none yet."""
    if not match_id:
        return {}
    with _V3_LOCK:
        entry = _V3_RESULTS.get(match_id, {})
        return dict(entry.get("confirmed", {}))


def get_status(match_id):
    """Return 'running', 'done', or '' for a given match."""
    if not match_id:
        return ""
    with _V3_LOCK:
        return _V3_RESULTS.get(match_id, {}).get("status", "")


def _scan(text):
    if not text:
        return set()
    return {m.lower() for m in _PUUID_RE.findall(text)}


def _verify_candidates_via_name_service(requests_obj, candidates, ally_set, log):
    """PUT /name-service/v2/players to NA/EU/KR for each candidate.
    Returns {puuid_lower: 'GameName#TagLine'} for any that resolve to
    a real account. Mirrors V2's verifier — kept standalone."""
    if not candidates:
        log("[v3-verify] no candidates, skip")
        return {}
    non_ally = [p for p in candidates if p.lower() not in ally_set]
    if not non_ally:
        log("[v3-verify] all candidates were allies, skip")
        return {}
    log(f"[v3-verify] verifying {len(non_ally)} non-ally candidate(s) "
        f"across NA/EU/KR")
    headers = requests_obj.get_headers()
    resolved = {}
    for shard in ("na", "eu", "kr"):
        try:
            url = f"https://pd.{shard}.a.pvp.net/name-service/v2/players"
            r = _req("PUT", url, headers=headers, json_body=list(non_ally), timeout=4)
            log(f"[v3-verify] shard={shard} -> HTTP {r.status_code}")
            if r.status_code != 200:
                continue
            data = r.json()
            if not isinstance(data, list):
                continue
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                subj = entry.get("Subject", "").lower()
                game = entry.get("GameName", "")
                tag = entry.get("TagLine", "")
                if subj and game:
                    resolved[subj] = f"{game}#{tag}"
                    log(f"[v3-verify] RESOLVED {subj[:8]} -> "
                        f"{game}#{tag} (via {shard})")
        except Exception as e:
            log(f"[v3-verify] shard={shard} exc: "
                f"{type(e).__name__}: {e}")
    unresolved = [p for p in non_ally if p.lower() not in resolved]
    if unresolved:
        log(f"[v3-verify] {len(unresolved)} candidate(s) NOT real: "
            f"{[p[:8] for p in unresolved[:10]]}")
    return resolved


_TRAFFIC_DUMP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "v3_traffic_dump.jsonl")
_TRAFFIC_DUMP_LOCK = threading.Lock()


def _dump_traffic(method, url, req_headers, req_body, status,
                   resp_headers, resp_text, exc=None):
    """Append a single request/response to the JSONL dump file. Used
    for offline analysis — what we send vs what Riot returns. Each
    line is a complete JSON object."""
    try:
        record = {
            "ts": time.time(),
            "method": method,
            "url": url,
            "req_headers": dict(req_headers or {}),
            "req_body": req_body,
            "status": status,
            "resp_headers": dict(resp_headers or {}),
            "resp_text": (resp_text or "")[:8000],
            "exc": exc,
        }
        line = json.dumps(record, default=str)
        with _TRAFFIC_DUMP_LOCK:
            os.makedirs(os.path.dirname(_TRAFFIC_DUMP_PATH),
                        exist_ok=True)
            with open(_TRAFFIC_DUMP_PATH, "a",
                      encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        pass  # never break the probe over dump failure


def _req(method, url, headers=None, json_body=None, data=None,
         timeout=4, **kwargs):
    """Like `requests.request` but ALSO writes to the traffic dump.
    Drop-in replacement for direct `requests.get/put/post/request`
    calls in productive vectors. Returns the Response object.
    Raises on exception (caller handles)."""
    req_body = None
    if json_body is not None:
        try:
            req_body = json.dumps(json_body)
        except Exception:
            req_body = str(json_body)
    elif data is not None:
        req_body = data if isinstance(data, str) else str(data)
    try:
        r = requests.request(method, url, headers=headers or {},
                             json=json_body, data=data,
                             verify=False, timeout=timeout, **kwargs)
        _dump_traffic(method, url, headers, req_body,
                      r.status_code, dict(r.headers), r.text or "")
        return r
    except Exception as e:
        _dump_traffic(method, url, headers, req_body, None, None, "",
                      exc=f"{type(e).__name__}: {e}")
        raise




# =====================================================================
# Vector 1 — RAW DUMP of /pregame/v1/matches/{id}
# V1/V2 logged response *length* but never dumped the response body.
# This vector dumps a redacted version so the user can confirm whether
# the second `Teams[]` entry has populated Players[] in 2026 builds.
# =====================================================================
def _v3_raw_dump(requests_obj, pregame_match_id, ally_set, log):
    url = f"{requests_obj.glz_url}/pregame/v1/matches/{pregame_match_id}"
    headers = requests_obj.get_headers()
    try:
        r = _req("GET", url, headers=headers, timeout=4)
        text = r.text or ""
        log(f"[v3-raw-dump] HTTP {r.status_code} len={len(text)}")
        try:
            data = r.json()
        except Exception:
            log(f"[v3-raw-dump] body (truncated 1000 chars): {text[:1000]}")
            return _scan(text) - ally_set
        teams = data.get("Teams", [])
        log(f"[v3-raw-dump] Teams[] has {len(teams)} entries")
        for i, team in enumerate(teams):
            if not isinstance(team, dict):
                continue
            tid = team.get("TeamID", "?")
            players = team.get("Players", [])
            log(f"[v3-raw-dump]   Teams[{i}] TeamID={tid} "
                f"Players[]={len(players)} entries")
            for j, p in enumerate(players[:6]):
                if not isinstance(p, dict):
                    continue
                subj = p.get("Subject", "")
                cid = p.get("CharacterID", "")
                state = p.get("CharacterSelectionState", "")
                log(f"[v3-raw-dump]     [{j}] Subject={subj[:8] if subj else '<empty>'} "
                    f"CharID={cid[:8] if cid else '<empty>'} state={state}")
        if "EnemyTeam" in data:
            et = data["EnemyTeam"]
            log(f"[v3-raw-dump] EnemyTeam present, type={type(et).__name__}, "
                f"value={et if et is None else 'object'}")
        # Log all top-level fields so we can spot MUCName, TeamMatchToken,
        # ChatToken, etc. — anything that might gate the MUC join.
        log(f"[v3-raw-dump] top-level keys: {sorted(data.keys())}")
        for k in sorted(data.keys()):
            if k in ("Teams", "AllyTeam", "EnemyTeam"):
                continue  # already logged or null
            v = data[k]
            if isinstance(v, (str, int, float, bool)) or v is None:
                log(f"[v3-raw-dump]   {k}={v!r}")
            elif isinstance(v, (list, dict)):
                log(f"[v3-raw-dump]   {k}=<{type(v).__name__} len={len(v)}> "
                    f"sample={str(v)[:200]!r}")
        return _scan(text) - ally_set
    except Exception as e:
        log(f"[v3-raw-dump] exc: {type(e).__name__}: {e}")
        return set()


# =====================================================================
# Vector — Non-chat HTTP probes. The chat/MUC angle is fully explored
# (allies-only at every layer). Try entirely different services that
# logically need to know match composition: match-history, MMR,
# competitive ranking updates, Premier (team mode), leaderboards,
# contracts, player-stats, and the actual game-pod hostname.
# =====================================================================
def _v3_ingame_race_poll(requests_obj, match_id, ally_set, log,
                         budget_seconds=70, interval=0.5):
    """Background poll of /core-game/v1/matches/{matchId} every
    `interval` seconds during PREGAME. The match-id is the same for
    pregame and coregame; coregame returns 404 during pregame and 200
    once the match has actually started.

    Returns confirmed enemy puuids the moment Riot's coregame service
    populates — typically 1-2 seconds before VRY's main loop notices
    the INGAME state transition.
    """
    if not match_id:
        return set()
    glz = requests_obj.glz_url
    headers = requests_obj.get_headers()
    deadline = time.time() + budget_seconds
    poll_count = 0
    found = set()
    while time.time() < deadline:
        poll_count += 1
        try:
            r = requests.get(
                f"{glz}/core-game/v1/matches/{match_id}",
                headers=headers, verify=False, timeout=2)
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception as e:
                    log(f"[v3-race] poll #{poll_count} 200 "
                        f"non-JSON: {type(e).__name__}: {e}")
                    time.sleep(interval)
                    continue
                players = data.get("Players", [])
                log(f"[v3-race] *** COREGAME ALIVE *** "
                    f"poll #{poll_count} returned {len(players)} "
                    f"players")
                for p in players:
                    if not isinstance(p, dict):
                        continue
                    subj = (p.get("Subject") or "").lower()
                    if not subj or subj in ally_set:
                        continue
                    found.add(subj)
                if found:
                    log(f"[v3-race] {len(found)} non-ally puuid(s) "
                        f"surfaced via early coregame: "
                        f"{[p[:8] for p in found]}")
                return found
            elif r.status_code in (404, 400):
                pass  # expected during pregame, keep polling
            else:
                log(f"[v3-race] poll #{poll_count} HTTP "
                    f"{r.status_code} (continuing)")
        except Exception as e:
            log(f"[v3-race] poll #{poll_count} exc: "
                f"{type(e).__name__}: {e}")
        time.sleep(interval)
    log(f"[v3-race] DONE — {poll_count} polls in {budget_seconds}s, "
        f"coregame never returned 200 during pregame window")
    return set()


def coregame_race_async(requests_obj, match_id, ally_puuids, own_puuid,
                        log, budget_seconds=70):
    """Run the coregame race-poll in a daemon thread alongside XMPP
    and V3-main probes. Writes confirmed enemy names into _V3_RESULTS
    on success."""
    if not _claim_dispatch("race", match_id):
        log(f"[v3-race] already running for match="
            f"{match_id[:8] if match_id else '?'}; skip")
        return None
    ally_set = {p.lower() for p in ally_puuids if p}
    if own_puuid:
        ally_set.add(own_puuid.lower())

    def _runner():
        log(f"[v3-race] starting (budget={budget_seconds}s, 500ms poll)")
        try:
            puuids = _v3_ingame_race_poll(
                requests_obj, match_id, ally_set, log, budget_seconds)
            if puuids:
                confirmed = _verify_candidates_via_name_service(
                    requests_obj, puuids, ally_set, log)
                if confirmed:
                    log(f"[v3-race] *** EARLY ENEMY REVEAL *** "
                        f"{len(confirmed)} confirmed real:")
                    for puuid, name in confirmed.items():
                        log(f"[v3-race]   {puuid[:8]} -> {name}")
                    with _V3_LOCK:
                        entry = _V3_RESULTS.setdefault(
                            match_id, {"status": "done",
                                       "confirmed": {}})
                        entry["confirmed"].update(confirmed)
        except Exception as e:
            log(f"[v3-race] runner exc: "
                f"{type(e).__name__}: {e}")
        finally:
            _release_dispatch("race", match_id)

    t = threading.Thread(target=_runner, name="vry-race-poll",
                         daemon=True)
    t.start()
    return t


def _v3_decode_all_jwts(requests_obj, log):
    """Decode every JWT VRY currently holds and log the claims. May
    surface a claim we never inspected (e.g. permission scopes that
    hint at hidden endpoints)."""
    headers = requests_obj.get_headers()
    auth_h = headers.get("Authorization", "")
    rso = auth_h[7:] if auth_h.startswith("Bearer ") else ""
    ent_jwt = headers.get("X-Riot-Entitlements-JWT", "")

    def _decode(label, tok):
        if not tok or "." not in tok:
            log(f"[v3-jwt] {label}: <not a JWT>")
            return {}
        try:
            parts = tok.split(".")
            payload = parts[1] + "==="
            claims = json.loads(base64.urlsafe_b64decode(payload))
            log(f"[v3-jwt] {label} claims keys={sorted(claims.keys())}")
            for k in sorted(claims.keys()):
                v = claims[k]
                if isinstance(v, (str, int, float, bool)):
                    log(f"[v3-jwt] {label}.{k}={v!r}")
                elif isinstance(v, list):
                    log(f"[v3-jwt] {label}.{k}=<list len={len(v)}>: "
                        f"{str(v)[:300]}")
                elif isinstance(v, dict):
                    log(f"[v3-jwt] {label}.{k}=<dict keys="
                        f"{sorted(v.keys())}>: {str(v)[:300]}")
            return claims
        except Exception as e:
            log(f"[v3-jwt] {label} decode exc: "
                f"{type(e).__name__}: {e}")
            return {}

    _decode("RSO", rso)
    _decode("Entitlements", ent_jwt)
    # Mint and decode each PAS service token
    for svc in ("chat", "valorant", "rms", "product/valorant"):
        try:
            r = _req("GET", f"https://riot-geo.pas.si.riotgames.com/pas/v1/service/{svc}", headers={"Authorization": f"Bearer {rso}"}, timeout=4)
            if r.status_code == 200:
                _decode(f"PAS({svc})", r.text.strip())
        except Exception as e:
            log(f"[v3-jwt] PAS({svc}) exc: {type(e).__name__}: {e}")
    return set()  # JWT decoder is informational only


def _v3_shootergame_remoting(requests_obj, match_id, own_puuid, log):
    """ShooterGame.exe runs its own embedded HTTP server on a port
    revealed in /product-session/v1/sessions launch arguments. The
    remoting-auth-token authenticates against it. This API surface is
    SEPARATE from the Riot Client lockfile API and may expose internal
    game data the launcher doesn't.
    """
    lockfile = getattr(requests_obj, "lockfile", None)
    if not lockfile:
        return set()
    rc_auth = base64.b64encode(
        f"riot:{lockfile['password']}".encode()).decode()
    rc_headers = {"Authorization": f"Basic {rc_auth}"}
    rc_base = f"https://127.0.0.1:{lockfile['port']}"

    # Fetch product-session and parse remoting-app-port + token
    try:
        r = _req("GET", rc_base + "/product-session/v1/sessions", headers=rc_headers, timeout=4)
        if r.status_code != 200:
            log(f"[v3-rg] product-session HTTP {r.status_code}; abort")
            return set()
        sessions = r.json()
    except Exception as e:
        log(f"[v3-rg] product-session exc: "
            f"{type(e).__name__}: {e}")
        return set()

    # Find the Valorant ("ares") product session
    remoting_port = None
    remoting_token = None
    if isinstance(sessions, dict):
        for sid, sess in sessions.items():
            if not isinstance(sess, dict):
                continue
            launch = sess.get("launchConfiguration", {})
            args = launch.get("arguments", []) if isinstance(launch, dict) else []
            if not isinstance(args, list):
                continue
            port_arg = next((a for a in args
                             if isinstance(a, str)
                             and a.startswith("-remoting-app-port=")), None)
            tok_arg = next((a for a in args
                            if isinstance(a, str)
                            and a.startswith("-remoting-auth-token=")), None)
            if port_arg and tok_arg:
                remoting_port = port_arg.split("=", 1)[1]
                remoting_token = tok_arg.split("=", 1)[1]
                product = launch.get("productId", sid)
                log(f"[v3-rg] product={product!r} "
                    f"remoting-app-port={remoting_port} "
                    f"remoting-auth-token=len={len(remoting_token)}")
                break
    if not remoting_port or not remoting_token:
        log("[v3-rg] no remoting auth found in product-session")
        return set()

    # ShooterGame's API uses Basic auth with username "riot" and the
    # remoting-auth-token as password (per Riot's launch arg convention)
    rg_auth = base64.b64encode(
        f"riot:{remoting_token}".encode()).decode()
    rg_headers = {"Authorization": f"Basic {rg_auth}"}
    rg_base = f"https://127.0.0.1:{remoting_port}"

    # Step 1: Fetch /help — the COMPLETE API catalog. Parse it to
    # extract every endpoint path the game exposes, then filter for
    # ones related to match/player/pregame/coregame/chat/roster.
    discovered_paths = set()
    try:
        r = _req("GET", rg_base + "/help", headers=rg_headers, timeout=6)
        if r.status_code == 200 and r.text:
            try:
                help_data = r.json()
            except Exception:
                help_data = {}
            log(f"[v3-rg] /help len={len(r.text)} "
                f"top_keys={sorted(help_data.keys()) if isinstance(help_data, dict) else type(help_data).__name__}")
            # Dump full /help to disk so we can analyze offline
            try:
                dump_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "logs", "shootergame_help_dump.json")
                with open(dump_path, "w", encoding="utf-8") as fh:
                    fh.write(r.text)
                log(f"[v3-rg] /help dumped to {dump_path}")
            except Exception as e:
                log(f"[v3-rg] /help dump exc: "
                    f"{type(e).__name__}: {e}")
            # Events are usually named OnJsonApiEvent_<path_with_underscores>
            # Convert OnJsonApiEvent_pregame_v1_matches → /pregame/v1/matches
            interesting_keywords = (
                "pregame", "core_game", "core-game", "coregame",
                "match", "player", "chat", "roster", "team",
                "presence", "muc", "party",
            )
            events = help_data.get("events", {}) if isinstance(help_data, dict) else {}
            for event_name in events:
                if not event_name.startswith("OnJsonApiEvent_"):
                    continue
                path = "/" + event_name[len("OnJsonApiEvent_"):].replace("_", "/")
                ln = path.lower()
                if any(kw in ln for kw in interesting_keywords):
                    discovered_paths.add(path)
            # Functions sometimes also map to paths
            functions = help_data.get("functions", {}) if isinstance(help_data, dict) else {}
            if functions:
                log(f"[v3-rg] /help has {len(functions)} function(s)")
            # Other top-level keys may also list paths
            for key in ("paths", "endpoints", "api", "routes"):
                v = help_data.get(key) if isinstance(help_data, dict) else None
                if v:
                    log(f"[v3-rg] /help.{key} sample={str(v)[:300]!r}")
            log(f"[v3-rg] discovered {len(discovered_paths)} interesting "
                f"path(s) from /help (events filter)")
            for p in sorted(discovered_paths)[:30]:
                log(f"[v3-rg] discovered: {p}")
        else:
            log(f"[v3-rg] /help HTTP {r.status_code}")
    except Exception as e:
        log(f"[v3-rg] /help exc: {type(e).__name__}: {e}")

    # Step 2: Probe each discovered path (and a few static fallbacks)
    paths = list(discovered_paths)
    paths += [
        "/help",   # already fetched but log uniformly
        "/swagger",
        "/swagger.json",
        "/openapi.json",
        "/api/swagger",
    ]
    found = set()
    match_id_lc = (match_id or "").lower()
    log(f"[v3-rg] probing {len(paths)} path(s) on remoting :{remoting_port}")
    for p in paths:
        try:
            r = _req("GET", rg_base + p, headers=rg_headers, timeout=3)
            log(f"[v3-rg] {p} HTTP {r.status_code} len={len(r.text)}")
            if r.status_code == 200 and r.text:
                # Sample
                sample = r.text[:400]
                log(f"[v3-rg] {p} sample={sample!r}")
                # Scope-by-match-id filter
                puuids = _scan(r.text)
                if puuids:
                    body_lc = r.text.lower()
                    if match_id_lc and match_id_lc in body_lc:
                        log(f"[v3-rg] {p} body MENTIONS match-id, "
                            f"counting {len(puuids)} puuid(s)")
                        found |= puuids
                    else:
                        log(f"[v3-rg] {p} body has {len(puuids)} "
                            f"puuid(s) but no match-id; skipping")
            elif r.status_code != 404 and len(r.text) < 300:
                log(f"[v3-rg] {p} body: {r.text!r}")
        except Exception as e:
            log(f"[v3-rg] {p} exc: {type(e).__name__}: {e}")
    return found


# =====================================================================
# Vector — Local Riot Client REST API chat probes.
# The local Riot Client (RiotClientServices.exe) keeps a server-side-
# authorized set of chat conversations on the local machine, surfaced
# through the lockfile-authed REST API at 127.0.0.1:{lockfile_port}.
# VRY already uses /chat/v4/presences. We've never called the v6
# conversation/participant/message endpoints which list every room
# the client is in plus every member of those rooms.
# =====================================================================
def _v3_local_chat_api(requests_obj, ally_set, log):
    lockfile = getattr(requests_obj, "lockfile", None)
    if not lockfile or "port" not in lockfile or "password" not in lockfile:
        log("[v3-local] no lockfile available; skip")
        return set()
    port = lockfile["port"]
    auth = base64.b64encode(
        f"riot:{lockfile['password']}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}
    base = f"https://127.0.0.1:{port}"
    found = set()

    def _local_get(path, label):
        try:
            r = _req("GET", base + path, headers=headers, timeout=4)
            log(f"[v3-local] {label} HTTP {r.status_code} "
                f"len={len(r.text)}")
            if r.status_code != 200:
                if len(r.text) < 300:
                    log(f"[v3-local] {label} body: {r.text!r}")
                return None
            try:
                return r.json()
            except Exception:
                log(f"[v3-local] {label} non-JSON: {r.text[:200]!r}")
                return None
        except Exception as e:
            log(f"[v3-local] {label} exc: {type(e).__name__}: {e}")
            return None

    # 1. Probe the named MUC conversation paths Riot exposes locally.
    # These return the cid + metadata for the user's pregame/coregame/
    # party/custom-game rooms regardless of whether they appear in the
    # default /conversations listing (which only has DMs).
    cids = []
    named_muc_paths = [
        ("ares-pregame", "/chat/v6/conversations/ares-pregame"),
        ("ares-coregame", "/chat/v6/conversations/ares-coregame"),
        ("ares-parties", "/chat/v6/conversations/ares-parties"),
        ("ares-teambuilder-custom",
         "/chat/v6/conversations/ares-teambuilder-custom"),
        ("ares-allchat", "/chat/v6/conversations/ares-allchat"),
        ("ares-coregame-allchat",
         "/chat/v6/conversations/ares-coregame-allchat"),
    ]
    for muc_label, muc_path in named_muc_paths:
        muc_data = _local_get(muc_path, f"named({muc_label})")
        if isinstance(muc_data, dict):
            log(f"[v3-local] {muc_label} keys={sorted(muc_data.keys())} "
                f"sample={str(muc_data)[:300]!r}")
            cid = muc_data.get("cid") or muc_data.get("id") or ""
            if cid and cid not in cids:
                cids.append(cid)
            # Pull any embedded puuids
            blob = json.dumps(muc_data)
            for puuid in _scan(blob):
                pl = puuid.lower()
                if pl in ally_set:
                    continue
                log(f"[v3-local] named({muc_label}) non-ally puuid: "
                    f"{pl[:8]}")
                found.add(pl)

    # 2. Also list DM conversations (default listing)
    convs = _local_get("/chat/v6/conversations", "list-conversations")
    if isinstance(convs, dict):
        conv_list = (convs.get("conversations")
                     or convs.get("Conversations")
                     or [])
    elif isinstance(convs, list):
        conv_list = convs
    else:
        conv_list = []
    log(f"[v3-local] {len(conv_list)} conversation(s) returned")
    for c in conv_list:
        if not isinstance(c, dict):
            continue
        cid = c.get("cid") or c.get("id") or ""
        ctype = c.get("type", "?")
        muc = c.get("muc", c.get("Muc", False))
        log(f"[v3-local] conv: cid={cid!r} type={ctype} muc={muc} "
            f"keys={sorted(c.keys())}")
        if cid:
            cids.append(cid)
        # Conversations may include participants directly
        for k in ("participants", "Participants", "members", "Members"):
            members = c.get(k)
            if isinstance(members, list):
                for m in members:
                    if isinstance(m, dict):
                        pp = (m.get("puuid") or m.get("Puuid")
                              or m.get("subject") or m.get("Subject")
                              or "")
                    elif isinstance(m, str):
                        pp = m
                    else:
                        continue
                    if pp:
                        pl = pp.lower()
                        if pl not in ally_set:
                            log(f"[v3-local] conv-member non-ally "
                                f"{pl[:8]} (cid={cid[:20]})")
                            found.add(pl)

    # 2. Per-conversation participants endpoint
    for cid in cids[:20]:
        # url-encode cid since it may contain @/. characters
        encoded = urllib.parse.quote(cid, safe="")
        for path_tmpl, label in (
            (f"/chat/v6/participants/{encoded}",
             f"participants({cid[:20]})"),
            (f"/chat/v6/conversations/{encoded}",
             f"conv-detail({cid[:20]})"),
            (f"/chat/v6/messages/{encoded}",
             f"messages({cid[:20]})"),
        ):
            data = _local_get(path_tmpl, label)
            if not data:
                continue
            # Recursively scan the response for puuids
            blob = json.dumps(data) if not isinstance(data, str) else data
            for puuid in _scan(blob):
                pl = puuid.lower()
                if pl in ally_set:
                    continue
                log(f"[v3-local] {label} non-ally puuid: {pl[:8]}")
                found.add(pl)

    log(f"[v3-local] DONE — {len(found)} non-ally candidate(s)")
    return found


# =====================================================================
# Vector — Riot Client / Valorant log scraper.
# READ-ONLY. No writes, no deletes, no DLL injection, no process
# tampering. Just opens log files the Riot Client writes to disk in
# plaintext and searches them for XMPP stanzas, match-id mentions, and
# Token+Room pairs Riot's official client may have logged while doing
# its own MUC join. All public RE research said the official client
# joins via internal mechanism we can't see over the user-facing
# socket — but the client may *log* what it did.
# =====================================================================
def _v3_scrape_client_logs(requests_obj, match_id, ally_set, log):
    candidate_dirs = [
        os.path.expandvars(
            r"%LOCALAPPDATA%\Riot Games\Riot Client\Logs\Riot Client UX Logs"),
        os.path.expandvars(
            r"%LOCALAPPDATA%\Riot Games\Riot Client\Logs"),
        os.path.expandvars(r"%LOCALAPPDATA%\VALORANT\Saved\Logs"),
    ]
    found = set()
    files_read = 0
    match_id_lc = (match_id or "").lower()
    for d in candidate_dirs:
        if not os.path.isdir(d):
            log(f"[v3-clog] dir not found: {d}")
            continue
        log_files = []
        for pat in ("*.log", "*.txt"):
            log_files.extend(glob.glob(os.path.join(d, pat)))
        log_files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
        log_files = log_files[:3]
        log(f"[v3-clog] dir {d}: {len(log_files)} most-recent file(s)")
        for fpath in log_files:
            fname = os.path.basename(fpath)
            try:
                size = os.path.getsize(fpath)
                if size > 30_000_000:
                    log(f"[v3-clog] skip {fname} ({size}b too large)")
                    continue
                # Read-only with shared access (Windows allows shared
                # read even while Riot Client is writing).
                with open(fpath, "r", encoding="utf-8",
                          errors="replace") as fh:
                    content = fh.read()
                files_read += 1
                log(f"[v3-clog] read {fname} ({len(content)}b)")

                # 1. Hunt for ares-pregame/coregame/parties XMPP stanzas
                muc_stanzas = re.findall(
                    r"<(?:presence|iq|message)[^>]*"
                    r"(?:ares-pregame|ares-coregame|ares-parties)"
                    r"[^>]*>[\s\S]*?</(?:presence|iq|message)>",
                    content)
                if muc_stanzas:
                    log(f"[v3-clog] {fname}: {len(muc_stanzas)} MUC-related "
                        f"XMPP stanza(s)")
                for snippet in muc_stanzas[:8]:
                    snippet = snippet[:2500]
                    log(f"[v3-clog] STANZA: {snippet!r}")
                    # Extract puuids from from=/to= JIDs
                    for fjid in re.findall(
                            r"(?:from|to)=['\"]"
                            r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}"
                            r"-[0-9a-f]{4}-[0-9a-f]{12})@",
                            snippet, re.IGNORECASE):
                        if fjid.lower() not in ally_set:
                            if match_id_lc and fjid.lower() == match_id_lc:
                                continue  # skip match_id itself
                            found.add(fjid.lower())

                # 2. Hunt for current match-id mentions (context-based)
                if match_id_lc:
                    idxs = []
                    start = 0
                    cl = content.lower()
                    while True:
                        i = cl.find(match_id_lc, start)
                        if i == -1:
                            break
                        idxs.append(i)
                        start = i + 1
                        if len(idxs) > 8:
                            break
                    if idxs:
                        log(f"[v3-clog] {fname}: match-id mentioned "
                            f"{len(idxs)} time(s)")
                    for i in idxs[:5]:
                        snippet = content[max(0, i - 400):i + 600]
                        log(f"[v3-clog] CTX: {snippet!r}")
                        # Any non-ally puuid in this neighborhood is
                        # match-tied (in same context as our match-id).
                        for puuid in _scan(snippet):
                            pl = puuid.lower()
                            if pl in ally_set:
                                continue
                            if pl == match_id_lc:
                                continue
                            found.add(pl)

                # 3. Hunt for Token+Room pairs (Riot's chattoken
                # responses logged by the client). Could give us a
                # token shape we could replicate.
                for m in re.finditer(
                        r'"Token"\s*:\s*"([^"]{50,})"',
                        content):
                    log(f"[v3-clog] TOKEN found len={len(m.group(1))} "
                        f"prefix={m.group(1)[:30]!r}")
                for m in re.finditer(
                        r'"Room"\s*:\s*"([^"]+)"', content):
                    log(f"[v3-clog] ROOM found: {m.group(1)!r}")
                for m in re.finditer(
                        r'MUCName"?\s*[:=]\s*"?([^",\s]+)', content):
                    log(f"[v3-clog] MUCName mention: {m.group(1)!r}")

                # 4. Extract ALL pregame/coregame/chat HTTP queries the
                # official game made. Surfaces endpoint URLs we may
                # never have probed.
                seen_urls = set()
                for m in re.finditer(
                        r'QueryName: \[((?:Pregame|CoreGame|Coregame|'
                        r'Match|Chat|Roster|Voice|Party)_[^\]]+)\][^\n]*'
                        r'URL \[(GET|POST|PUT|DELETE) ([^\]]+)\]',
                        content):
                    qn, method, url = m.group(1), m.group(2), m.group(3)
                    key = f"{method} {url.split('?')[0]}"
                    if key in seen_urls:
                        continue
                    seen_urls.add(key)
                    log(f"[v3-clog] QUERY: {qn} {method} {url}")

                # Detect the user's current partyId from the log so we
                # can filter it out of CHAT-PRESENCE captures. Party IDs
                # are UUID-shaped and would otherwise look like puuids.
                # The official client's URL pattern is
                # `/parties/v[12]/parties/{partyId}/...`.
                party_id_lc = ""
                pm = re.search(
                    r'/parties/v[12]/parties/'
                    r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}'
                    r'-[0-9a-f]{4}-[0-9a-f]{12})',
                    content, re.IGNORECASE)
                if pm:
                    party_id_lc = pm.group(1).lower()

                # ShooterGame.log accumulates events across MANY past
                # matches (file is multi-megabyte). To avoid pulling in
                # historical opponents/friends, scope chat-event scans
                # to only the section of the log AFTER the FIRST mention
                # of the current match-id (i.e. the section written
                # since the current pregame started).
                if match_id_lc:
                    first_idx = content.lower().find(match_id_lc)
                    scoped = (content[first_idx:] if first_idx >= 0
                              else "")
                    if first_idx >= 0:
                        log(f"[v3-clog] {fname}: scoping chat scan to "
                            f"{len(scoped)}b after match-id first "
                            f"appears at offset {first_idx}")
                else:
                    scoped = ""

                # 5. Extract `incoming presence <PUUID>` chat events
                # — but ONLY in the scoped (current-match) section.
                for m in re.finditer(
                        r'incoming presence\s+'
                        r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}'
                        r'-[0-9a-f]{4}-[0-9a-f]{12})',
                        scoped, re.IGNORECASE):
                    pl = m.group(1).lower()
                    if pl in ally_set:
                        continue
                    if match_id_lc and pl == match_id_lc:
                        continue
                    if party_id_lc and pl == party_id_lc:
                        continue
                    log(f"[v3-clog] CHAT-PRESENCE non-ally puuid: {pl[:8]}")
                    found.add(pl)

                # 6. (REMOVED) The loose `LogRiotGamesApiClient...chat:`
                # regex was capturing party-ids and other UUID-shaped
                # internal IDs (party-id, trace-id, etc.). The strict
                # `incoming presence` pattern above is sufficient.

            except PermissionError as e:
                log(f"[v3-clog] {fname} permission denied: {e}")
            except Exception as e:
                log(f"[v3-clog] {fname} exc: "
                    f"{type(e).__name__}: {e}")

    log(f"[v3-clog] DONE — {files_read} file(s) scanned, "
        f"{len(found)} non-ally candidate puuid(s)")
    return found


# =====================================================================
# XMPP authenticated probe
# V1's `direct_xmpp_probe` opened a stream and never authed. V3 does
# full SASL X-Riot-RSO-PAS, then roster IQ, disco#items on
# `ares-pregame.{shard}.pvp.net`, then MUC <presence/> to enemy room.
# Run in a daemon thread with a 60s budget.
# =====================================================================
def _xmpp_get_pas_token(rso_token, log):
    """Mint a PAS chat token via riot-geo.pas. Returns (token, claims_dict)."""
    try:
        r = _req("GET",
                 "https://riot-geo.pas.si.riotgames.com/pas/v1/service/chat",
                 headers={"Authorization": f"Bearer {rso_token}"},
                 timeout=5)
        log(f"[v3-xmpp] PAS chat -> HTTP {r.status_code} "
            f"len={len(r.text)}")
        if r.status_code != 200:
            return None, {}
        token = r.text.strip()
        try:
            parts = token.split(".")
            payload = parts[1] + "==="
            claims = json.loads(base64.urlsafe_b64decode(payload))
            log(f"[v3-xmpp] PAS claims keys={sorted(claims.keys())}")
            for k in ("affinity", "aff", "dest", "host", "iss", "sub"):
                if k in claims:
                    log(f"[v3-xmpp] PAS claim {k}={claims[k]!r}")
            return token, claims
        except Exception as e:
            log(f"[v3-xmpp] PAS decode exc: {type(e).__name__}: {e}")
            return token, {}
    except Exception as e:
        log(f"[v3-xmpp] PAS mint exc: {type(e).__name__}: {e}")
        return None, {}


def _xmpp_candidate_hosts(claims, fallback_shard):
    """Yield candidate XMPP hostnames from JWT claims if present.
    Empirically: clientconfig + netstat-derived hosts are what work;
    the speculative `{aff}.chat.si.riotgames.com` family always 404s
    in DNS. Returns just JWT-claim hosts (rare to be set)."""
    hosts = []
    seen = set()
    for k in ("dest", "host", "chat_host", "xmpp_host"):
        v = claims.get(k)
        if isinstance(v, str) and v and v not in seen:
            seen.add(v)
            hosts.append(v)
    return hosts


def _xmpp_run(requests_obj, pregame_match_id, ally_set, log,
              budget_seconds=60):
    """Authenticated XMPP probe. Returns set of non-ally puuids seen."""
    headers = requests_obj.get_headers()
    auth_h = headers.get("Authorization", "")
    if not auth_h.startswith("Bearer "):
        log("[v3-xmpp] no Bearer token; abort")
        return set()
    rso = auth_h[7:]
    pas, claims = _xmpp_get_pas_token(rso, log)
    if not pas:
        return set()
    aff = (claims.get("affinity") or claims.get("aff") or "").lower()
    if not aff:
        aff = (getattr(requests_obj, "region", "") or "").lower() or "as2"
    # 1. Riot clientconfig — pure API, what the official client uses
    config = _v3_get_clientconfig(requests_obj, log)
    cc_hosts = _extract_service_hosts(config, "chat", aff, log, label="chat")
    # 2. netstat fallback — read OS connection table for whatever the
    # running Riot Client is currently connected to on port 5223
    if not cc_hosts:
        log("[v3-xmpp] clientconfig had no chat host; trying netstat")
        ns_hosts = _netstat_active_port(5223, log)
        cc_hosts = list(ns_hosts)
    # 3. Last-ditch: speculative guesses
    candidates = list(cc_hosts) + [
        h for h in _xmpp_candidate_hosts(
            claims, getattr(requests_obj, "region", ""))
        if h not in cc_hosts
    ]
    log(f"[v3-xmpp] {len(candidates)} candidate host(s) "
        f"(cc={len(cc_hosts)}): {candidates}")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    found = set()

    sock = None
    host = None
    for cand in candidates:
        try:
            log(f"[v3-xmpp] trying tls://{cand}:5223")
            sock = socket.create_connection((cand, 5223), timeout=6)
            host = cand
            log(f"[v3-xmpp] connected to {cand}")
            break
        except Exception as e:
            log(f"[v3-xmpp] {cand} -> {type(e).__name__}: {e}")
            sock = None
    if sock is None:
        log("[v3-xmpp] no host reachable on :5223; abort")
        return set()
    try:
        sock = ctx.wrap_socket(sock, server_hostname=host)
        sock.settimeout(5)

        def _send(stanza, label):
            try:
                sock.sendall(stanza.encode("utf-8"))
                log(f"[v3-xmpp] sent {label} ({len(stanza)} bytes)")
            except Exception as e:
                log(f"[v3-xmpp] send {label} exc: "
                    f"{type(e).__name__}: {e}")

        def _recv_for(seconds, label):
            data = bytearray()
            deadline = time.time() + seconds
            while time.time() < deadline:
                try:
                    sock.settimeout(max(0.2, deadline - time.time()))
                    chunk = sock.recv(8192)
                except (socket.timeout, ssl.SSLWantReadError):
                    if data:
                        break
                    continue
                except Exception as e:
                    log(f"[v3-xmpp] recv {label} exc: "
                        f"{type(e).__name__}: {e}")
                    break
                if not chunk:
                    break
                data.extend(chunk)
                if b"</stream:stream>" in data:
                    break
            txt = data.decode("utf-8", errors="replace")
            log(f"[v3-xmpp] {label} recv {len(data)}b "
                f"sample={txt[:200]!r}")
            return txt

        # 1. Stream open. Use cached real host if we discovered it in a
        # prior probe, else guess `{aff}.pvp.net` (often wrong: PAS aff
        # 'sea2' maps to XMPP host 'sa2.pvp.net' — the 'e' is dropped).
        with _XMPP_REAL_HOST_LOCK:
            domain_to = _XMPP_REAL_HOST.get(aff) or f"{aff}.pvp.net"
        _send(
            f"<?xml version='1.0'?>"
            f"<stream:stream to='{domain_to}' "
            f"xml:lang='en' version='1.0' "
            f"xmlns:stream='http://etherx.jabber.org/streams' "
            f"xmlns='jabber:client'>",
            f"stream-open(to={domain_to})")
        greeting = _recv_for(2.5, "stream-greeting")
        found |= _scan(greeting) - ally_set

        # Parse the server's `from=` and cache it for next time.
        m = re.search(r"\bfrom=['\"]([^'\"]+)['\"]", greeting)
        if m:
            real_host = m.group(1)
            with _XMPP_REAL_HOST_LOCK:
                _XMPP_REAL_HOST[aff] = real_host
            if real_host != domain_to:
                log(f"[v3-xmpp] server identifies as {real_host!r}; "
                    f"our 'to' was {domain_to!r}")

        # If our guess was wrong, server returned <host-unknown> + closed
        # the stream. Reconnect to the same IP and re-open with the host
        # the server told us is correct.
        needs_retry = ("host-unknown" in greeting
                       or "<stream:error" in greeting)
        if needs_retry and m and m.group(1) != domain_to:
            real_host = m.group(1)
            log(f"[v3-xmpp] stream errored; reconnecting with "
                f"to='{real_host}'")
            try:
                sock.close()
            except Exception:
                pass
            try:
                raw = socket.create_connection((host, 5223), timeout=6)
                sock = ctx.wrap_socket(raw, server_hostname=host)
                sock.settimeout(5)
            except Exception as e:
                log(f"[v3-xmpp] reconnect exc: {type(e).__name__}: {e}")
                return found
            domain_to = real_host
            _send(
                f"<?xml version='1.0'?>"
                f"<stream:stream to='{domain_to}' "
                f"xml:lang='en' version='1.0' "
                f"xmlns:stream='http://etherx.jabber.org/streams' "
                f"xmlns='jabber:client'>",
                f"stream-open-retry(to={domain_to})")
            greeting = _recv_for(2.5, "stream-greeting-retry")
            found |= _scan(greeting) - ally_set

        # 2. SASL X-Riot-RSO-PAS
        _send(
            f"<auth mechanism='X-Riot-RSO-PAS' "
            f"xmlns='urn:ietf:params:xml:ns:xmpp-sasl'>"
            f"<rso_token>{rso}</rso_token>"
            f"<pas_token>{pas}</pas_token>"
            f"</auth>",
            "sasl-auth")
        sasl_resp = _recv_for(3, "sasl-resp")
        found |= _scan(sasl_resp) - ally_set
        if "<success" not in sasl_resp:
            log("[v3-xmpp] SASL did not return <success/>; "
                "aborting deeper probes")
            return found

        # Restart the stream after SASL success
        _send(
            f"<stream:stream to='{domain_to}' "
            f"xml:lang='en' version='1.0' "
            f"xmlns:stream='http://etherx.jabber.org/streams' "
            f"xmlns='jabber:client'>",
            "stream-restart")
        restart_resp = _recv_for(2, "stream-restart-resp")
        # Dump the FULL features blob so we can see what the server
        # requires (session, rxep, sm, etc).
        log(f"[v3-xmpp] stream-restart-resp full ({len(restart_resp)}b): "
            f"{restart_resp[:1500]!r}")

        # 3. Bind with puuid-mode extension. Server advertises
        # `<bind><puuid-mode enabled='true' id='046'/></bind>` in stream
        # features — opting in may unlock additional puuid info in
        # downstream stanzas (e.g. presence broadcasts may include real
        # puuids of MUC members instead of just resource ids).
        _send(
            "<iq id='_xmpp_bind1' type='set'>"
            "<bind xmlns='urn:ietf:params:xml:ns:xmpp-bind'>"
            "<puuid-mode/>"
            "</bind>"
            "</iq>",
            "bind-with-puuid-mode")
        bind_resp = _recv_for(2, "bind-resp")
        found |= _scan(bind_resp) - ally_set

        # `found` is the set of MATCH-TIED puuids. Friend-roster and
        # friend-presence puuids are NOT match enemies; they get logged
        # for visibility but are kept separate so the enemy table doesn't
        # populate with random friends.
        friend_pool = set()  # puuids from roster + non-match presence
        match_pool = set()   # puuids from MUC stanzas + decoded tokens

        # Helper to extract enemy puuids ONLY from from='UUID@ares-
        # pregame.*' JIDs in any response. Defined here so steps 5+
        # (disco, MUC, rxep, pubsub) all share the same closure over
        # `match_pool` and `ally_set`.
        match_id_lower = (pregame_match_id or "").lower()

        def _extract_room_members(blob, label):
            hits = 0
            for fjid in re.findall(
                    r"from=['\"]([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}"
                    r"-[0-9a-f]{4}-[0-9a-f]{12})@([^/'\"]+)",
                    blob, re.IGNORECASE):
                puuid_l = fjid[0].lower()
                host_l = fjid[1].lower()
                # Skip:
                # - the user's own puuid + party allies
                # - the match_id itself (rejection echoes have
                #   from='{matchId}@ares-pregame...'). Match IDs ARE
                #   UUIDs so they pass the regex but aren't players.
                if puuid_l in ally_set:
                    continue
                if match_id_lower and puuid_l == match_id_lower:
                    continue
                if "ares-pregame" in host_l:
                    if puuid_l not in match_pool:
                        log(f"[v3-xmpp] {label} enemy candidate "
                            f"{puuid_l[:8]} on {host_l}")
                    match_pool.add(puuid_l)
                    hits += 1
            return hits

        # 4a. Legacy <session> IQ. Some XMPP servers (and historically
        # Riot's) require this before queries are honoured.
        _send(
            "<iq type='set' id='_v3_session1'>"
            "<session xmlns='urn:ietf:params:xml:ns:xmpp-session'/>"
            "</iq>",
            "session")
        session_resp = _recv_for(3, "session-resp")
        # Extract the user's display name + tagline from the session
        # response — required by Riot's MUC join (per server's 400 echo).
        user_game_name = ""
        user_tagline = ""
        m_name = re.search(
            r"<id\s+name=(['\"])(.*?)\1\s+tagline=(['\"])(.*?)\3",
            session_resp)
        if m_name:
            user_game_name = m_name.group(2)
            user_tagline = m_name.group(4)
            log(f"[v3-xmpp] parsed user identity: "
                f"game_name={user_game_name!r} tagline={user_tagline!r}")
        else:
            log("[v3-xmpp] could not parse <id name= tagline=/> "
                "from session response")

        # 4b. Rich self-presence. Bare `<presence/>` may be ignored by
        # Riot's chat server because it has no <show>/<status>/games
        # payload. Mimic the official Valorant client shape.
        rich_presence = (
            "<presence>"
            "<show>chat</show>"
            "<status>"
            "<![CDATA["
            '{"valorant":{"version":"1"}}'
            "]]>"
            "</status>"
            "<games>"
            "<keystone>"
            "<s.p>keystone</s.p>"
            "<s.l>en_US</s.l>"
            "</keystone>"
            "<valorant>"
            "<s.p>valorant</s.p>"
            "<m>0</m>"
            "</valorant>"
            "</games>"
            "</presence>"
        )
        _send(rich_presence, "self-presence-rich")
        early_listen = _recv_for(6, "post-presence-listen")
        # Friends' presence broadcasts — NOT match enemies
        friend_pool |= _scan(early_listen) - ally_set

        # 5. Roster IQs — try Riot's custom namespace AND the standard
        # one, since we don't know which is honoured in 2026.
        _send(
            "<iq type='get' id='_v3_roster_riot'>"
            "<query xmlns='jabber:iq:riotgames:roster' last_state='true'/>"
            "</iq>",
            "roster-riot")
        roster_riot = _recv_for(5, "roster-riot-resp")
        friend_pool |= _scan(roster_riot) - ally_set
        _send(
            "<iq type='get' id='_v3_roster_std'>"
            "<query xmlns='jabber:iq:roster'/>"
            "</iq>",
            "roster-std")
        roster_std = _recv_for(5, "roster-std-resp")
        friend_pool |= _scan(roster_std) - ally_set

        # 6. disco#items on the only useful target. Past runs proved
        # that bare server + conference + muc + ares-coregame +
        # ares-parties always return 501/503/0b. Only ares-pregame is
        # alive on this shard. Skip the dead ones to save ~15s.
        domain_prefix = domain_to.split(".", 1)[0]
        muc_host = f"ares-pregame.{domain_prefix}.pvp.net"
        disco_targets = [muc_host]
        discovered_room_jids = []
        for i, tgt in enumerate(disco_targets):
            _send(
                f"<iq type='get' id='_v3_disco_{i}' to='{tgt}'>"
                f"<query xmlns='http://jabber.org/protocol/disco#items'/>"
                f"</iq>",
                f"disco-items({tgt})")
            disco_resp = _recv_for(3, f"disco-resp({tgt})")
            for item_jid in re.findall(
                    r"<item[^>]*\bjid=['\"]([^'\"]+)['\"]",
                    disco_resp):
                if item_jid and item_jid not in discovered_room_jids:
                    discovered_room_jids.append(item_jid)
                    log(f"[v3-xmpp] disco discovered item jid={item_jid!r} "
                        f"(via {tgt})")
            _extract_room_members(disco_resp, f"disco({tgt})")
            friend_pool |= _scan(disco_resp) - ally_set

        # 7. MUC join — 2-pass approach.
        #
        # Pass 1: Send a naked <presence/> with just `<x xmlns='...muc'/>`.
        # Riot's server rejects it with `400 bad-request` but the rejection
        # echo contains an `<access_token>` JWT minted by the chat server
        # for our session, plus shows what fields were missing.
        #
        # Pass 2: Re-send the MUC presence with all the fields Riot's echo
        # told us were needed: <access_token>, <names>, <platform>.
        # If accepted, we're inside the enemy team's chat room and the
        # server will broadcast presence stanzas for every member of
        # that room — including the 5 enemies.
        if pregame_match_id:
            # ---- Pass 0a: extract MUCName/TeamMatchToken from
            # pregame response itself. Riot embeds the MUC room JID and
            # password directly in `/pregame/v1/matches/{id}` — we just
            # never read those fields. ----
            try:
                pgr = _req("GET",
                    f"{requests_obj.glz_url}/pregame/v1/matches/{pregame_match_id}",
                    headers=requests_obj.get_headers(), timeout=4)
                if pgr.status_code == 200:
                    pgd = pgr.json() if pgr.text else {}
                    embed_room = (pgd.get("MUCName")
                                  or pgd.get("MucName")
                                  or pgd.get("Mucname")
                                  or "")
                    embed_token = (pgd.get("TeamMatchToken")
                                   or pgd.get("MatchToken")
                                   or pgd.get("ChatToken")
                                   or "")
                    log(f"[v3-pgembed] keys={sorted(pgd.keys())}")
                    log(f"[v3-pgembed] MUCName={embed_room!r} "
                        f"TeamMatchToken=len={len(embed_token)}")
                    if embed_room and embed_token:
                        # Decode the TeamMatchToken JWT and surface its
                        # participantPuuids list. Cross-reference vs
                        # AllyTeam: any puuid NOT in AllyTeam is either
                        # an enemy or an observer/coach.
                        try:
                            tparts = embed_token.split(".")
                            if len(tparts) >= 2:
                                tclaims = json.loads(
                                    base64.urlsafe_b64decode(
                                        tparts[1] + "==="))
                                log(f"[v3-pgembed] TeamMatchToken claims "
                                    f"keys={sorted(tclaims.keys())}")
                                for tk in sorted(tclaims.keys()):
                                    tv = tclaims[tk]
                                    if isinstance(tv, (str, int, float, bool)):
                                        log(f"[v3-pgembed] token.{tk}={tv!r}")
                                    elif isinstance(tv, list):
                                        log(f"[v3-pgembed] token.{tk}="
                                            f"<list len={len(tv)}>: {tv}")
                                pps = tclaims.get("participantPuuids", [])
                                if isinstance(pps, list):
                                    ally_lower = {
                                        a.lower() for a in ally_set if a}
                                    not_in_ally = [
                                        p for p in pps
                                        if p.lower() not in ally_lower]
                                    log(f"[v3-pgembed] participantPuuids "
                                        f"count={len(pps)}, "
                                        f"NOT-in-ally count="
                                        f"{len(not_in_ally)}: "
                                        f"{not_in_ally}")
                                    # Anything in the token that's NOT
                                    # an ally is an enemy candidate
                                    for p in not_in_ally:
                                        match_pool.add(p.lower())
                        except Exception as e:
                            log(f"[v3-pgembed] token decode exc: "
                                f"{type(e).__name__}: {e}")
                        # Build a properly-formatted password MUC join
                        # with the verbatim embedded room JID + token.
                        sub_for_nick = (
                            claims.get("sub") or "probeuser").lower()
                        embed_stanza = (
                            f"<presence to='{embed_room}/{sub_for_nick}'>"
                            f"<x xmlns='http://jabber.org/protocol/muc'>"
                            f"<password>{embed_token}</password>"
                            f"</x>"
                            f"</presence>"
                        )
                        _send(embed_stanza, "muc-pgembed")
                        embed_resp = _recv_for(5, "muc-pgembed-resp")
                        log(f"[v3-pgembed] muc-pgembed-resp full "
                            f"({len(embed_resp)}b): "
                            f"{embed_resp[:2000]!r}")
                        _extract_room_members(embed_resp, "muc-pgembed")
                else:
                    log(f"[v3-pgembed] HTTP {pgr.status_code} skip")
            except Exception as e:
                log(f"[v3-pgembed] outer exc: "
                    f"{type(e).__name__}: {e}")

            # ---- Pass 0: chattoken + password MUC join ----
            # The recent-writeups research found that Riot's pregame MUC
            # requires <password>{Token}</password> inside <x>. Tokens
            # come from /pregame/v1/matches/{id}/chattoken (and sister
            # endpoints). Each returns { Token, Room }. We use the Room
            # verbatim as the to= JID and embed the Token as MUC
            # password. Also probes speculative ?team=2 / enemychattoken
            # variants.
            sub_for_nick = (claims.get("sub") or "probeuser").lower()
            try:
                tok_results = _v3_fetch_chattokens(
                    requests_obj, pregame_match_id, sub_for_nick, log)
            except Exception as e:
                log(f"[v3-chattoken] outer exc: "
                    f"{type(e).__name__}: {e}")
                tok_results = []
            for label, tok, room in tok_results:
                # Use the verbatim Room JID Riot returned (don't
                # construct it ourselves). Resource is the user's puuid
                # (Riot's official client uses puuid as MUC nickname).
                stanza = (
                    f"<presence to='{room}/{sub_for_nick}'>"
                    f"<x xmlns='http://jabber.org/protocol/muc'>"
                    f"<password>{tok}</password>"
                    f"</x>"
                    f"</presence>"
                )
                _send(stanza, f"muc-tok({label})")
            if tok_results:
                tok_resp = _recv_for(6, "muc-tok-resp")
                log(f"[v3-xmpp] muc-tok-resp full ({len(tok_resp)}b): "
                    f"{tok_resp[:3000]!r}")
                _extract_room_members(tok_resp, "muc-tok")
            else:
                log("[v3-xmpp] no chattokens minted; skipping Pass 0")

            # ---- Pass 1: naked join (need this first to capture the
            # access_token from rejection echo before firing Pass 2). ----
            for suffix in ("-2", ""):
                room_jid = f"{pregame_match_id}{suffix}@{muc_host}"
                _send(
                    f"<presence to='{room_jid}/probeuser'>"
                    f"<x xmlns='http://jabber.org/protocol/muc'/>"
                    f"</presence>",
                    f"muc-naked{suffix or '-base'}")
            muc_resp = _recv_for(4, "muc-naked-resp")
            log(f"[v3-xmpp] muc-naked-resp full ({len(muc_resp)}b): "
                f"{muc_resp[:2000]!r}")
            _extract_room_members(muc_resp, "muc-naked")

            # Capture access_token Riot's chat server minted for us
            captured_tokens = re.findall(
                r"<access_token[^>]*>([^<]+)</access_token>", muc_resp)
            captured_token = captured_tokens[0].strip() if captured_tokens else ""
            if captured_token:
                log(f"[v3-xmpp] captured access_token len="
                    f"{len(captured_token)}; will fire formed in batch")
                try:
                    parts = captured_token.split(".")
                    if len(parts) >= 2:
                        tok_claims = json.loads(base64.urlsafe_b64decode(
                            parts[1] + "==="))
                        log(f"[v3-xmpp] captured token claims="
                            f"{sorted(tok_claims.keys())}")
                except Exception as e:
                    log(f"[v3-xmpp] captured token decode exc: "
                        f"{type(e).__name__}: {e}")

            # ---- Pass 2: properly-formed MUC join with captured token ----
            # Empirically still gets 400 bad-request (Riot's MUC
            # protocol uses a custom RXEP shape we don't have), but we
            # keep this attempt because the stanza is captured in the
            # traffic dump for offline analysis.
            if captured_token and (user_game_name or user_tagline):
                def _xml_attr_escape(s):
                    return (s.replace("&", "&amp;").replace("<", "&lt;")
                              .replace(">", "&gt;").replace("'", "&apos;")
                              .replace("\"", "&quot;"))
                gn = _xml_attr_escape(user_game_name)
                tl = _xml_attr_escape(user_tagline)
                for suffix in ("-2", ""):
                    room_jid = f"{pregame_match_id}{suffix}@{muc_host}"
                    _send(
                        f"<presence to='{room_jid}/probeuser'>"
                        f"<access_token>{captured_token}</access_token>"
                        f"<names game_name='{gn}' tagline='{tl}'>"
                        f"<platform_names/>"
                        f"</names>"
                        f"<platform>windows</platform>"
                        f"<x xmlns='http://jabber.org/protocol/muc'/>"
                        f"</presence>",
                        f"muc-formed{suffix or '-base'}")
            else:
                log("[v3-xmpp] skipping Pass 2: "
                    f"have_token={bool(captured_token)} "
                    f"have_name={bool(user_game_name or user_tagline)}")

            # ---- Drain ----
            # Capture all responses to Pass 1 + Pass 2.
            big_drain = _recv_for(12, "muc-drain")
            log(f"[v3-xmpp] muc-drain full ({len(big_drain)}b): "
                f"{big_drain[:3000]!r}")
            _extract_room_members(big_drain, "drain")

        # 8. Final drain — listen for any unsolicited presence/event
        # broadcasts. Mostly friend updates again, but tag separately
        # in case anything match-room-related shows up here.
        budget_left = max(5, budget_seconds - 50)
        listen = _recv_for(budget_left, "final-drain")
        for puuid in _scan(listen) - ally_set:
            # Heuristic: anything appearing in a stanza tied to the
            # match room JID is match-tied; otherwise it's friend data.
            if pregame_match_id and pregame_match_id in listen:
                # crude — needs better stanza-level routing in a future
                # iteration, but for now anything in a chunk that mentions
                # the match id is more likely match-tied
                match_pool.add(puuid)
            else:
                friend_pool.add(puuid)

        log(f"[v3-xmpp] match_pool={len(match_pool)} "
            f"friend_pool={len(friend_pool)} "
            f"(only match_pool will populate the enemy table)")
        found |= match_pool

        # Clean disconnect: send <presence type='unavailable'/> so the
        # chat server stops broadcasting our resource as online (this
        # prevents the menu.py duplicate-row issue from appearing in
        # the official Riot Client until our resource times out).
        try:
            sock.sendall(b"<presence type='unavailable'/>")
            sock.sendall(b"</stream:stream>")
        except Exception:
            pass
    except Exception as e:
        log(f"[v3-xmpp] outer exc: {type(e).__name__}: {e}")
    finally:
        try:
            sock.close()
        except Exception:
            pass

    log(f"[v3-xmpp] done — {len(found)} non-ally UUID-shaped strings seen")
    return found


def xmpp_probe_v3_async(requests_obj, pregame_match_id, ally_puuids,
                        own_puuid, log, budget_seconds=60):
    """Run XMPP probe in a daemon thread. Returns the thread."""
    if not _claim_dispatch("xmpp", pregame_match_id):
        log(f"[v3-xmpp] already running for match={pregame_match_id[:8] if pregame_match_id else '?'}; skip")
        return None
    ally_set = {p.lower() for p in ally_puuids if p}
    if own_puuid:
        ally_set.add(own_puuid.lower())

    def _runner():
        log(f"[v3-xmpp] starting (budget={budget_seconds}s)")
        try:
            puuids = _xmpp_run(requests_obj, pregame_match_id, ally_set, log,
                               budget_seconds)
            if puuids:
                log(f"[v3-xmpp] non-ally UUID-shaped: "
                    f"{[p[:8] for p in list(puuids)[:10]]}")
                confirmed = _verify_candidates_via_name_service(
                    requests_obj, puuids, ally_set, log)
                if confirmed:
                    log(f"[v3-xmpp] *** ENEMY REVEAL *** "
                        f"{len(confirmed)} confirmed real")
                    with _V3_LOCK:
                        entry = _V3_RESULTS.setdefault(
                            pregame_match_id,
                            {"status": "done", "confirmed": {}})
                        entry["confirmed"].update(confirmed)
        except Exception as e:
            log(f"[v3-xmpp] runner exc: {type(e).__name__}: {e}")
        finally:
            _release_dispatch("xmpp", pregame_match_id)

    t = threading.Thread(target=_runner, name="vry-xmpp-v3", daemon=True)
    t.start()
    return t


# =====================================================================
# Aggregator — runs all sync vectors then verifies any candidates.
# XMPP runs separately in a daemon thread (different lifetime).
# =====================================================================
def probe_v3_main(requests_obj, pregame_match_id, own_puuid, ally_puuids,
                  log):
    """Run all V3 sync vectors. Returns dict {vector: confirmed_resolutions}."""
    if not pregame_match_id:
        log("[v3-main] no pregame_match_id, abort")
        return {}
    with _V3_LOCK:
        _V3_RESULTS[pregame_match_id] = {
            "status": "running", "confirmed": {}}
    ally_set = {p.lower() for p in ally_puuids if p}
    if own_puuid:
        ally_set.add(own_puuid.lower())
    log(f"[v3-main] STARTING for match={pregame_match_id[:8]} "
        f"ally_count={len(ally_set)}")

    all_candidates = set()
    per_vector = {}

    vectors = [
        # Always-useful: confirms current pregame schema, surfaces
        # MUCName / TeamMatchToken / EnemyTeam=null
        ("raw-dump", lambda: _v3_raw_dump(requests_obj, pregame_match_id,
                                          ally_set, log)),
        # Scrapes ShooterGame.log for MUC stanzas + chat-presence puuids
        # in the current-match scope
        ("client-logs", lambda: _v3_scrape_client_logs(
            requests_obj, pregame_match_id, ally_set, log)),
        # Local Riot Client REST API (DM listings + named MUC paths)
        ("local-chat", lambda: _v3_local_chat_api(
            requests_obj, ally_set, log)),
        # Decodes every JWT VRY currently holds (informational)
        ("jwt-decode", lambda: _v3_decode_all_jwts(requests_obj, log)),
        # ShooterGame.exe remoting API — fetches /help (282KB catalog)
        # and probes any discovered match-related paths
        ("shootergame-remoting", lambda: _v3_shootergame_remoting(
            requests_obj, pregame_match_id, own_puuid, log)),
    ]

    def _run(name, fn):
        try:
            return name, (fn() or set())
        except Exception as e:
            log(f"[v3-main] vector={name} exc: {type(e).__name__}: {e}")
            return name, set()

    with _cf.ThreadPoolExecutor(max_workers=len(vectors)) as pool:
        futures = [pool.submit(_run, n, f) for n, f in vectors]
        for fut in _cf.as_completed(futures, timeout=60):
            try:
                name, puuids = fut.result()
            except Exception as e:
                log(f"[v3-main] future exc: {type(e).__name__}: {e}")
                continue
            non_ally = puuids - ally_set
            per_vector[name] = non_ally
            all_candidates |= non_ally
            log(f"[v3-main] vector={name} non_ally={len(non_ally)}")

    if not all_candidates:
        log(f"[v3-main] DONE — all sync vectors empty for match="
            f"{pregame_match_id[:8]}")
        with _V3_LOCK:
            _V3_RESULTS[pregame_match_id]["status"] = "done"
        return {}

    log(f"[v3-main] aggregated {len(all_candidates)} non-ally candidate(s); "
        f"verifying...")
    confirmed = _verify_candidates_via_name_service(
        requests_obj, all_candidates, ally_set, log)
    if confirmed:
        log(f"[v3-main] *** ENEMY REVEAL *** {len(confirmed)} confirmed real:")
        for puuid, name in confirmed.items():
            log(f"[v3-main]   {puuid[:8]} -> {name}")
    else:
        log(f"[v3-main] DONE — all candidates are cosmetic; V3 closed for "
            f"this match")
    with _V3_LOCK:
        _V3_RESULTS[pregame_match_id] = {
            "status": "done", "confirmed": dict(confirmed)}
    return {"per_vector": per_vector, "confirmed": confirmed}


def probe_v3_main_async(requests_obj, pregame_match_id, own_puuid,
                        ally_puuids, log):
    """Run probe_v3_main in a daemon thread so it doesn't block PREGAME
    rendering. Results land in _V3_RESULTS keyed by match_id; read via
    get_confirmed_names()."""
    if not _claim_dispatch("main", pregame_match_id):
        log(f"[v3-main] already running for match={pregame_match_id[:8] if pregame_match_id else '?'}; skip")
        return None

    def _runner():
        try:
            probe_v3_main(requests_obj, pregame_match_id, own_puuid,
                          ally_puuids, log)
        except Exception as e:
            log(f"[v3-main] async runner exc: {type(e).__name__}: {e}")
            with _V3_LOCK:
                _V3_RESULTS[pregame_match_id] = {
                    "status": "done", "confirmed": {}}
        finally:
            _release_dispatch("main", pregame_match_id)

    t = threading.Thread(target=_runner, name="vry-probe-v3-main",
                         daemon=True)
    t.start()
    return t
