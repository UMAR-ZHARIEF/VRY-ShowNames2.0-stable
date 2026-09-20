"""Shared keep-alive HTTP connection pool for outbound requests.

Background: every call site in src/requestsV.py and src/names.py used the
module-level one-shot helpers of the requests library, which open a fresh
TCP+TLS connection per call. This module holds one tuned requests.Session
per destination family, created lazily and reused for the life of the
process, so the TCP+TLS handshake cost is paid once per host instead of
once per call.

Families (routed from the URL):
  - "local": the Riot client API on the literal https://127.0.0.1 host
  - ("pd", shard): one session per pd.<shard>.a.pvp.net shard, so the
    names.py multi-shard name-service probe keeps a warm connection per
    shard; the home shard shares this with Requests.fetch("pd") calls
  - "glz": every glz-* game/PartySync host (urllib3 keys its inner
    pools by scheme+host+port, so hosts inside one family session
    still never share connections with each other)
  - "shared": everything else (content-service style hosts reached
    through fetch("custom"))

PooledRequests is a small facade mirroring exactly the requests-module
surface those two modules use (.request, .get, .put, .exceptions). Both
modules bind the shared facade instance under the name `requests`, so
every call site keeps its literal shape and its explicit per-request
verify= flag: the TLS guard in tests/test_secrets_redaction.py keeps
scanning real call sites, verify=True stays on all remote hosts and
verify=False stays only on the literal https://127.0.0.1 client API.

Thread safety: session lookup/creation is guarded by a lock (double
checked; the steady-state fast path is an atomic dict read). The sessions
themselves are deliberately shared across threads: urllib3 connection
pools are thread-safe, which is what lets parallel fetches reuse warm
connections.
"""
import re
import threading

import requests
from requests.adapters import HTTPAdapter

# Tuned for ~12 concurrent fetches: 16 cached connections per family
# session, and enough host-pool slots for the multi-host families.
# pool_block stays False on purpose: when all pooled connections of a
# family are busy, urllib3 opens an extra connection instead of blocking
# the caller, because the glz/pd calls carry no timeout today and must
# never wait indefinitely on a stuck peer (busy-period connections
# degrade to one-shot instead of deadlocking).
_POOL_CONNECTIONS = 16
_POOL_MAXSIZE = 16

_PD_HOST_RE = re.compile(r"^https://pd\.([^.]+)\.a\.pvp\.net", re.IGNORECASE)


def family_for_url(url):
    """Destination family for a URL; one keep-alive session per family."""
    if url.startswith("https://127.0.0.1"):
        return "local"
    match = _PD_HOST_RE.match(url)
    if match:
        return ("pd", match.group(1).lower())
    if url.startswith("https://glz-"):
        return "glz"
    return "shared"


class SessionPool:
    """Lazily created, cached requests.Session per destination family."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions = {}

    def session_for_url(self, url):
        family = family_for_url(url)
        # Atomic dict read: the common path never takes the lock.
        session = self._sessions.get(family)
        if session is not None:
            return session
        with self._lock:
            session = self._sessions.get(family)
            if session is None:
                session = requests.Session()
                adapter = HTTPAdapter(pool_connections=_POOL_CONNECTIONS,
                                      pool_maxsize=_POOL_MAXSIZE,
                                      pool_block=False)
                session.mount("https://", adapter)
                session.mount("http://", adapter)
                self._sessions[family] = session
            return session

    def session_count(self):
        return len(self._sessions)


class PooledRequests:
    """Facade mirroring the requests surface used by requestsV/names.

    Routes every call through the shared SessionPool while passing all
    per-request kwargs (headers, verify, json, timeout) through untouched,
    so each call site's TLS policy is applied exactly as before.
    `exceptions` forwards to the real requests.exceptions namespace for
    the except clauses in the callers.
    """

    exceptions = requests.exceptions

    def __init__(self, pool=None):
        self.pool = pool if pool is not None else SessionPool()

    def request(self, method, url, **kwargs):
        return self.pool.session_for_url(url).request(method, url, **kwargs)

    def get(self, url, **kwargs):
        return self.pool.session_for_url(url).get(url, **kwargs)

    def put(self, url, **kwargs):
        return self.pool.session_for_url(url).put(url, **kwargs)


# Process-wide facade instance; callers bind it under the name their HTTP
# call sites already use.
pooled_requests = PooledRequests()
