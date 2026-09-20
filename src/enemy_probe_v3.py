"""V3 enemy-preview probes — untried vectors only.

Every vector in this file was NOT exercised by V1 (`enemy_probe.py`) or
V2 (`enemy_probe_v2.py`). Research across 6 parallel agents converged on
"no evidence-based vector remains" — but the user explicitly asked for
methods we have not tried, regardless of expected outcome. So this
module attempts each one and logs the result for evidence.

API-only. No memory access. No game-file modification.

2026-09-19 readonly hardening: the raw-socket XMPP probe path and the
three POST chattoken variants were removed. Only the read-only HTTP
vectors and the traffic dump remain.
"""
import re
import json
import time
import base64
import threading
import concurrent.futures as _cf
import urllib.parse
import requests


import os
import glob

_PUUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

# Hard guard: only ONE active dispatch per (kind, match_id). Without
# this, two concurrent probe sessions for the same Riot account and
# match duplicate work and race on the shared result store.
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

# Auth header values must never reach the traffic dump: Authorization
# carries the RSO bearer token and lockfile Basic credentials, and the
# entitlements header carries a live JWT. Header names stay readable.
_SECRET_DUMP_HEADERS = ("authorization", "x-riot-entitlements-jwt")


def _redact_dump_headers(headers):
    redacted = {}
    for key, value in dict(headers or {}).items():
        if str(key).lower() in _SECRET_DUMP_HEADERS:
            redacted[key] = "***"
        elif isinstance(value, str) and value.startswith(("Basic ", "Bearer ")):
            redacted[key] = value.partition(" ")[0] + " ***"
        else:
            redacted[key] = value
    return redacted


# Secrets that must also stay out of the dump *bodies* (headers are
# handled by _redact_dump_headers above). The PAS service endpoints
# return raw service JWTs, and the local remoting vector's responses
# embed the remoting-auth-token (product-session launch arguments) or
# could echo its Basic credential. Vectors register live secret
# strings here; _dump_traffic scrubs them from the body fields.
_DUMP_BODY_SECRETS = set()
_DUMP_BODY_SECRET_LOCK = threading.Lock()
_PAS_DUMP_BODY_PLACEHOLDER = "<redacted: token body>"
_REDACTED_DUMP_BODY = "<redacted>"


def _register_dump_body_secret(value):
    """Register a live secret string that must never appear in a
    traffic-dump body. Strings shorter than 8 chars are ignored so
    ordinary field values can never be mangled."""
    value = str(value or "")
    if len(value) < 8:
        return
    with _DUMP_BODY_SECRET_LOCK:
        _DUMP_BODY_SECRETS.add(value)


def _scrub_dump_body(url, text):
    """Redact secret-bearing bodies before they reach the JSONL dump.
    PAS token-mint bodies are replaced wholesale (the response IS the
    secret); every other body keeps its bytes except for the
    remoting-auth-token launch argument and any registered secret
    string. The launch-argument pattern runs even for the
    product-session response that first carries the token — that
    response is dumped before the token has been parsed and
    registered, so the static pattern is what protects it."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    if not text:
        return text
    if "pas.si.riotgames.com" in str(url or ""):
        return _PAS_DUMP_BODY_PLACEHOLDER
    text = re.sub(r"(-remoting-auth-token=)[A-Za-z0-9_\-]+",
                  r"\1<redacted>", text)
    with _DUMP_BODY_SECRET_LOCK:
        secrets = list(_DUMP_BODY_SECRETS)
    for secret in secrets:
        if secret in text:
            text = text.replace(secret, _REDACTED_DUMP_BODY)
    return text


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
            "req_headers": _redact_dump_headers(req_headers),
            "req_body": _scrub_dump_body(url, req_body),
            "status": status,
            "resp_headers": _redact_dump_headers(resp_headers),
            "resp_text": _scrub_dump_body(url, resp_text or "")[:8000],
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


def _verify_for(url):
    # verify=True for every remote host; verify=False only for the Riot
    # client's local API (https://127.0.0.1), whose self-signed cert
    # breaks startup if verified.
    return not str(url).startswith("https://127.0.0.1")


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
                             verify=_verify_for(url), timeout=timeout, **kwargs)
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
        # ChatToken, etc. — anything that might gate token-gated reads.
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
                headers=headers, verify=_verify_for(f"{glz}/core-game/v1/matches/{match_id}"), timeout=2)
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
    """Run the coregame race-poll in a daemon thread alongside the
    V3-main probes. Writes confirmed enemy names into _V3_RESULTS
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
    # Same leak class as the remoting token: if a local endpoint ever
    # echoes this Basic credential into a body, the dump must scrub it.
    _register_dump_body_secret(rc_auth)
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
                # Register the raw token immediately: every dump body
                # written from here on must scrub it.
                _register_dump_body_secret(remoting_token)
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
    _register_dump_body_secret(rg_auth)
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
# Aggregator — runs all sync vectors then verifies any candidates.
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
