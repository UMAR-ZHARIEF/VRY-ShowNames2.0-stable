"""Process-wide cache for static valorant-api.com payloads.

Every consumer used to re-download these payloads on every call or table
render. This module fetches each path once per process (thread-safe),
caches the parsed JSON for the process lifetime, and returns None on
failure WITHOUT caching the failure, so the next call retries. Callers
treat None exactly like a network exception (raise or short-circuit to
their existing failure path).
"""
import threading

import requests

_BASE = "https://valorant-api.com"
_LOCK = threading.Lock()
_CACHE = {}  # path (incl. query string) -> parsed JSON dict


def get_json(path, timeout=10):
    """Parsed JSON for a valorant-api.com path, fetched once per process.

    The path should start with "/" (a leading slash is added if missing)
    and may include a query string; each distinct path is cached
    separately. Returns None on any network/parse failure without caching
    the failure.
    """
    if not path.startswith("/"):
        path = "/" + path
    with _LOCK:
        if path in _CACHE:
            return _CACHE[path]
    # Fetch outside the lock: a slow download must not block other
    # lookups. Duplicate concurrent first-fetches are possible and
    # harmless; the first writer wins the cache slot.
    try:
        response = requests.get(_BASE + path, timeout=timeout)
        data = response.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    with _LOCK:
        if path not in _CACHE:
            _CACHE[path] = data
        return _CACHE[path]


def _reset_cache_for_tests():
    with _LOCK:
        _CACHE.clear()
