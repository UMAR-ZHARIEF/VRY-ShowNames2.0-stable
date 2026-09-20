"""Keep-alive pooling: sessions must be shared, per-family, and tuned.

Waves before 2026-09-20 opened a fresh TCP+TLS connection for every
outbound call (bare one-shot requests calls). src/http_pool.py now holds
one cached requests.Session per destination family, and requestsV.py /
names.py route every call through it. These tests pin, offline (no
network):
  - family routing (local / glz / one session per pd shard / shared),
  - the SAME session object is reused across two fetch calls to the same
    family (the Session class is mocked, so the adapter layer is the seam),
  - per-shard sessions for the names.py multi-shard probe are created
    lazily and cached (home shard skipped, exactly as the probe always did),
  - adapter tuning (pool_connections=16, pool_maxsize=16, non-blocking),
  - Requests.get_headers is single-flight under concurrency: N parallel
    callers with an empty header cache produce exactly ONE entitlements
    call and all receive the same complete header dict.
"""
import os
import sys
import threading
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import src.http_pool as http_pool  # noqa: E402
from src.http_pool import PooledRequests, SessionPool, family_for_url  # noqa: E402
from src.names import Names  # noqa: E402
from src.requestsV import Requests  # noqa: E402

GLZ_URL = "https://glz-na-1.na.a.pvp.net"


def mock_response(status_code=200, ok=True, json_value=None, text=""):
    response = mock.MagicMock(name="response")
    response.status_code = status_code
    response.ok = ok
    response.text = text
    response.json.return_value = json_value
    return response


def bare_requests(log):
    """A Requests instance without running __init__ (no lockfile reads,
    no entitlements call, no network), mirroring the helper in
    tests/test_fetch_empty_response.py."""
    instance = Requests.__new__(Requests)
    instance.Error = None
    instance.version = "test-version"
    instance.headers = {}
    instance._headers_lock = threading.Lock()
    instance.log = log
    instance.lockfile = {"name": "ShooterGame", "PID": "1", "port": "2999",
                         "password": "pw", "protocol": "https"}
    instance.region = "na"
    instance.pd_url = "https://pd.na.a.pvp.net"
    instance.glz_url = GLZ_URL
    instance.puuid = "puuid-test"
    return instance


def patch_session_factory(facade):
    """Patch the Session constructor http_pool uses, returning a fresh
    mock session per family and recording the factory calls. Returns
    (context manager, created_sessions, constructed counter)."""
    created = []
    constructed = []

    def factory(*args, **kwargs):
        constructed.append(1)
        session = mock.MagicMock(name="session")
        session.request.side_effect = \
            lambda method, url, **kw: mock_response(json_value={"ok": True})
        session.get.side_effect = \
            lambda url, **kw: mock_response(json_value={"entitlements": True})
        session.put.side_effect = \
            lambda url, **kw: mock_response(json_value=[], status_code=200)
        created.append(session)
        return session

    # Two patches: the Session class used inside http_pool, and the facade
    # object the owned modules bind, so the test exercises its own pool.
    session_patch = mock.patch.object(http_pool.requests, "Session",
                                      side_effect=factory)
    facade_patch = mock.patch("src.requestsV.requests", facade)
    session_patch.start()
    facade_patch.start()
    return (session_patch, facade_patch), created, constructed


class FamilyRoutingTest(unittest.TestCase):
    def test_local_glz_shared_families(self):
        self.assertEqual(
            family_for_url("https://127.0.0.1:2999/entitlements/v1/token"),
            "local")
        self.assertEqual(family_for_url(GLZ_URL + "/x"), "glz")
        self.assertEqual(
            family_for_url("https://shared.na.a.pvp.net/content-service/v3/content"),
            "shared")

    def test_pd_families_are_per_shard(self):
        self.assertEqual(family_for_url("https://pd.na.a.pvp.net/x"),
                         ("pd", "na"))
        self.assertEqual(family_for_url("https://pd.eu.a.pvp.net/x"),
                         ("pd", "eu"))
        self.assertNotEqual(family_for_url("https://pd.na.a.pvp.net/x"),
                            family_for_url("https://pd.eu.a.pvp.net/x"))


class AdapterTuningTest(unittest.TestCase):
    def test_adapters_tuned_and_sessions_cached_per_family(self):
        pool = SessionPool()
        session = pool.session_for_url("https://pd.na.a.pvp.net/a")
        adapter = session.get_adapter("https://pd.na.a.pvp.net/a")
        self.assertEqual(adapter._pool_connections, 16)
        self.assertEqual(adapter._pool_maxsize, 16)
        self.assertIs(adapter._pool_block, False)

        # Same family -> same cached session object (home pd URL reused by
        # both Requests.fetch("pd") and the names.py name-service calls).
        self.assertIs(pool.session_for_url("https://pd.na.a.pvp.net/b"),
                      session)
        self.assertIs(
            pool.session_for_url(
                "https://pd.na.a.pvp.net/name-service/v2/players"),
            session)

        # Different families -> different session objects.
        self.assertIsNot(pool.session_for_url("https://pd.eu.a.pvp.net/a"),
                         session)
        self.assertIsNot(pool.session_for_url("https://127.0.0.1:2999/x"),
                         session)
        self.assertIsNot(pool.session_for_url(GLZ_URL + "/x"), session)
        self.assertEqual(pool.session_count(), 4)


class SameSessionAcrossFetchesTest(unittest.TestCase):
    """Two fetch calls to the same family must reuse the SAME session
    object (one TLS handshake per host instead of one per call)."""

    def test_two_glz_fetches_share_one_session(self):
        logs = []
        instance = bare_requests(logs.append)
        instance.headers = {"Authorization": "Bearer initial"}
        instance.get_headers = lambda: {"Authorization": "Bearer initial"}
        facade = PooledRequests()
        patches, created, constructed = patch_session_factory(facade)
        try:
            first = instance.fetch("glz",
                                   "/core-game/v1/matches/m1/loadouts", "get")
            second = instance.fetch("glz",
                                    "/core-game/v1/matches/m1/loadouts", "get")
        finally:
            for patch in patches:
                patch.stop()

        self.assertEqual(first, {"ok": True})
        self.assertEqual(second, {"ok": True})
        # Exactly one Session built, and it is the one both calls used.
        self.assertEqual(len(constructed), 1)
        self.assertIs(facade.pool.session_for_url(GLZ_URL), created[0])
        self.assertEqual(created[0].request.call_count, 2)
        # Per-request flags survive the pool: verify=True still passed.
        self.assertIs(created[0].request.call_args.kwargs["verify"], True)

    def test_local_and_glz_fetches_use_distinct_sessions(self):
        logs = []
        instance = bare_requests(logs.append)
        instance.headers = {"Authorization": "Bearer initial"}
        instance.get_headers = lambda: {"Authorization": "Bearer initial"}
        facade = PooledRequests()
        patches, created, constructed = patch_session_factory(facade)
        try:
            instance.fetch("glz", "/x", "get")
            instance.fetch("local", "/entitlements/v1/token", "get")
        finally:
            for patch in patches:
                patch.stop()

        self.assertEqual(len(constructed), 2)
        # local family session got the verify=False per-request flag.
        self.assertIs(created[1].request.call_args.kwargs["verify"], False)


class PerShardSessionCacheTest(unittest.TestCase):
    """names.py multi-shard probe: one lazily created session per shard,
    cached across calls; the home shard is skipped exactly as before."""

    def _patch_names_facade(self, facade):
        created = []
        constructed = []

        def factory(*args, **kwargs):
            constructed.append(1)
            session = mock.MagicMock(name="session")
            session.put.side_effect = \
                lambda url, **kw: mock_response(json_value=[], status_code=200)
            created.append(session)
            return session

        session_patch = mock.patch.object(http_pool.requests, "Session",
                                          side_effect=factory)
        names_patch = mock.patch("src.names.requests", facade)
        session_patch.start()
        names_patch.start()
        return (session_patch, names_patch), created, constructed

    def test_multi_region_probe_caches_one_session_per_shard(self):
        class StubRequests:
            shard = "na"
            pd_url = "https://pd.na.a.pvp.net"

            def get_headers(self, refresh=False):
                return {"Authorization": "Bearer stub"}

        logs = []
        names = Names(StubRequests(), logs.append)
        facade = PooledRequests()
        patches, created, constructed = self._patch_names_facade(facade)
        try:
            first = names.try_multi_region_name_service(["puuid-1"])
            second = names.try_multi_region_name_service(["puuid-1"])
        finally:
            for patch in patches:
                patch.stop()

        self.assertEqual(first, {})
        self.assertEqual(second, {})
        shards = ("na", "eu", "latam", "br", "kr", "ap", "pbe")
        remote = [s for s in shards if s != "na"]
        # One session per remote shard, created lazily, and the second call
        # constructed none (all cached).
        self.assertEqual(len(constructed), len(remote))
        self.assertEqual(len(created), len(remote))
        # Each shard hit its own cached session on BOTH probe calls
        # (two PUTs per session, no new session on the second call), with
        # the per-request verify flag intact.
        put_urls = set()
        for session in created:
            self.assertEqual(session.put.call_count, 2)
            put_urls.add(session.put.call_args.args[0])
            self.assertIs(session.put.call_args.kwargs["verify"], True)
        self.assertEqual(
            put_urls,
            {f"https://pd.{shard}.a.pvp.net/name-service/v2/players"
             for shard in remote})
        self.assertNotIn("https://pd.na.a.pvp.net/name-service/v2/players",
                         put_urls)  # home shard skipped, as before


class GetHeadersConcurrencyTest(unittest.TestCase):
    """Parallel fetches with an empty header cache must be single-flight:
    exactly one entitlements call, and every caller gets the same
    complete header dict (never a half-built one)."""

    def test_eight_parallel_callers_trigger_one_entitlements_call(self):
        logs = []
        instance = bare_requests(logs.append)
        instance.get_current_version = lambda: "release-test-01"
        entitlements = mock_response(json_value={
            "accessToken": "tok", "token": "ent", "subject": "puuid-x"})

        calls = []
        lock = threading.Lock()

        def fake_get(url, **kwargs):
            with lock:
                calls.append(url)
            return entitlements

        results = []
        errors = []
        barrier = threading.Barrier(8)

        def worker():
            try:
                barrier.wait()
                results.append(instance.get_headers())
            except Exception as exc:  # pragma: no cover - diagnostics only
                errors.append(exc)

        with mock.patch("src.requestsV.requests.get", side_effect=fake_get):
            threads = [threading.Thread(target=worker) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 8)
        self.assertEqual(instance.puuid, "puuid-x")
        for headers in results:
            self.assertEqual(headers["Authorization"], "Bearer tok")
            self.assertEqual(headers["X-Riot-Entitlements-JWT"], "ent")
            self.assertEqual(headers["X-Riot-ClientVersion"],
                             "release-test-01")
        self.assertEqual(results[0], instance.headers)


if __name__ == "__main__":
    unittest.main()
