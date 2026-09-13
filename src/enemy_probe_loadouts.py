"""Pregame loadouts enemy probe.

The pregame match endpoint returns EnemyTeam=null during agent select,
but the pregame LOADOUTS endpoint (the one listing each player's weapon
loadout) carries a Subject per entry and is not restricted to the ally
team. Polling it during agent select therefore reveals the enemy team's
puuids (and locked agents) as soon as their loadout entries appear.

API-only. No memory access. No game-file modification.
"""
import re
import threading
import time

_PUUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_LOCK = threading.Lock()
_ACTIVE = set()   # match_ids that currently have a live probe thread
_DONE = set()     # match_ids whose probe finished (complete or error);
                  # never restarted for the same match_id
_RESULTS = {}     # match_id -> {"enemies": [...], "agents": {puuid: agent},
                  #                "status": str, "last_subject_count": int}


def _new_state():
    return {"enemies": [], "agents": {}, "status": "polling",
            "last_subject_count": 0}


def start_loadouts_probe(Requests, match_id, ally_puuids, log,
                         poll_seconds=2.0, budget_seconds=120):
    """Fire-and-forget daemon probe for one match_id; no-op if already
    running or already finished (complete/error) for this match_id."""
    if not match_id:
        return
    with _LOCK:
        if match_id in _DONE:
            return
        if match_id in _ACTIVE:
            return
        _ACTIVE.add(match_id)
        state = _RESULTS.setdefault(match_id, _new_state())
        state["status"] = "polling"
    thread = threading.Thread(
        target=_probe_loop,
        args=(Requests, match_id, set(ally_puuids or []), log,
              poll_seconds, budget_seconds),
        daemon=True,
    )
    thread.start()


def _probe_loop(Requests, match_id, ally_set, log, poll_seconds, budget_seconds):
    deadline = time.time() + budget_seconds
    consecutive_hard_failures = 0
    logged_enemy_count = 0
    try:
        while time.time() < deadline:
            error_msg = _poll_once(Requests, match_id, ally_set)
            enemy_count = _enemy_count(match_id)
            if error_msg:
                consecutive_hard_failures += 1
                if consecutive_hard_failures == 1:
                    log(f"loadouts probe: hard error: {error_msg}")
                elif consecutive_hard_failures >= 10:
                    _set_status(match_id, f"error:{error_msg}")
                    log("loadouts probe: giving up after 10 consecutive failures")
                    return
            else:
                consecutive_hard_failures = 0
                if enemy_count > logged_enemy_count:
                    subjects = _subject_count(match_id)
                    log(f"loadouts probe: {subjects} subjects, "
                        f"{enemy_count} enemies so far")
                    logged_enemy_count = enemy_count
                    if enemy_count >= 5:
                        _set_status(match_id, "complete")
                        return
                if 1 <= enemy_count <= 4:
                    _set_status(match_id, "partial")
            time.sleep(poll_seconds)
        # budget exhausted: whatever we have is what the table gets
        _set_status(match_id, "complete")
    finally:
        with _LOCK:
            _ACTIVE.discard(match_id)
            state = _RESULTS.get(match_id)
            if state is not None and (state["status"] == "complete"
                                      or str(state["status"]).startswith("error:")):
                _DONE.add(match_id)


def _poll_once(Requests, match_id, ally_set):
    """One loadouts poll. Returns None on success, single-line error string on failure."""
    try:
        data = Requests.fetch("glz", f"/pregame/v1/matches/{match_id}/loadouts", "get")
    except Exception as exc:
        return " ".join(f"{type(exc).__name__}: {exc}".split())
    if not isinstance(data, dict):
        return "unexpected response type"
    if data.get("errorCode"):
        return " ".join(str(data.get("errorCode")).split())
    entries = data.get("Loadouts") or []
    if not isinstance(entries, list):
        return "unexpected Loadouts shape"
    with _LOCK:
        state = _RESULTS.get(match_id)
        if state is None:
            return None
        state["last_subject_count"] = len(entries)
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            subject = entry.get("Subject") or ""
            if not subject or not _PUUID_RE.match(subject):
                continue
            agent = entry.get("CharacterID") or ""
            # record CharacterID when present; never overwrite a known agent with ""
            if agent or subject not in state["agents"]:
                state["agents"][subject] = agent
            if subject not in ally_set and subject not in state["enemies"]:
                state["enemies"].append(subject)
    return None


def _enemy_count(match_id):
    with _LOCK:
        state = _RESULTS.get(match_id)
        return len(state["enemies"]) if state else 0


def _subject_count(match_id):
    with _LOCK:
        state = _RESULTS.get(match_id)
        return state["last_subject_count"] if state else 0


def _set_status(match_id, status):
    with _LOCK:
        state = _RESULTS.get(match_id)
        if state is not None:
            state["status"] = status


def get_enemy_puuids(match_id):
    """Enemy puuids in discovery order (thread-safe copy)."""
    with _LOCK:
        state = _RESULTS.get(match_id)
        return list(state["enemies"]) if state else []


def get_enemy_agents(match_id):
    """puuid -> CharacterID for every subject seen so far (thread-safe copy)."""
    with _LOCK:
        state = _RESULTS.get(match_id)
        return dict(state["agents"]) if state else {}


def get_loadouts_status(match_id):
    """One of: idle, polling, partial, complete, error:<msg>."""
    with _LOCK:
        state = _RESULTS.get(match_id)
        return state["status"] if state else "idle"
