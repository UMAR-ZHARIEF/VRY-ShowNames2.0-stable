"""Tests for the reworked src/stats.py disk behavior (SSD write fix).

Contract under test:
    - load once in __init__ (tolerant of missing/corrupt files)
    - read_data() serves the in-memory dict with zero file I/O
    - save_data() appends to memory, marks dirty, flushes at most once per
      10 minutes
    - per-player history is run-collapsed by match_id and capped to the
      newest PRUNE_KEEP entries
    - corrupt files are quarantined to stats.json.corrupt with a loud log
      line; a truncated file is salvaged best-effort from its valid prefix
    - every flush writes atomically (tmp + os.replace) and logs byte count
Run with the py launcher from the repo root.
"""
import contextlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.stats as stats_mod
from src.stats import PRUNE_KEEP, Stats


@contextlib.contextmanager
def tempfile_dir():
    d = tempfile.mkdtemp(prefix="vry-stats-test-")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


class _StubLog:
    def __init__(self):
        self.lines = []

    def __call__(self, message):
        self.lines.append(message)

    def text(self):
        return "\n".join(self.lines)


def _entry(match_id, epoch=1000.0, agent="Jett"):
    return {
        "name": "Player#0000",
        "agent": agent,
        "map": "N/A",
        "rank": 5,
        "rr": 0,
        "match_id": match_id,
        "epoch": epoch,
    }


def _flushable(stats, now):
    """Force the next save_data to be allowed to flush."""
    stats._last_flush = now - stats_mod.FLUSH_INTERVAL_SECONDS


class TestLoad(unittest.TestCase):
    def test_missing_file_starts_fresh(self):
        with tempfile_dir() as d:
            log = _StubLog()
            stats = Stats(log=log, path=os.path.join(d, "stats.json"))
            self.assertEqual(stats.read_data(), {})
            self.assertFalse(
                os.path.exists(os.path.join(d, "stats.json.corrupt"))
            )

    def test_valid_file_loaded_and_pruned(self):
        with tempfile_dir() as d:
            path = os.path.join(d, "stats.json")
            entries = [_entry(f"m{i}") for i in range(PRUNE_KEEP + 5)]
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"puuid": entries}, f)
            stats = Stats(log=_StubLog(), path=path)
            loaded = stats.read_data()["puuid"]
            self.assertEqual(len(loaded), PRUNE_KEEP)
            self.assertEqual(loaded[-1]["match_id"], f"m{PRUNE_KEEP + 4}")

    def test_read_data_does_no_file_io(self):
        with tempfile_dir() as d:
            path = os.path.join(d, "stats.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"puuid": [_entry("m1")]}, f)
            stats = Stats(log=_StubLog(), path=path)
            os.remove(path)
            # file is gone; read_data must still serve the in-memory dict
            with mock.patch(
                "builtins.open", side_effect=AssertionError("no I/O expected")
            ):
                self.assertEqual(stats.read_data()["puuid"][0]["match_id"], "m1")


class TestPrune(unittest.TestCase):
    def test_prune_caps_to_newest(self):
        with tempfile_dir() as d:
            stats = Stats(log=_StubLog(), path=os.path.join(d, "stats.json"))
            total = PRUNE_KEEP + 15
            for i in range(total):
                stats.save_data({"p": _entry(f"match-{i}")})
            kept = stats.read_data()["p"]
            self.assertEqual(len(kept), PRUNE_KEEP)
            self.assertEqual(kept[0]["match_id"], f"match-{total - PRUNE_KEEP}")
            self.assertEqual(kept[-1]["match_id"], f"match-{total - 1}")

    def test_same_match_appends_collapse_to_last(self):
        with tempfile_dir() as d:
            stats = Stats(log=_StubLog(), path=os.path.join(d, "stats.json"))
            for epoch in (1.0, 2.0, 3.0):
                stats.save_data({"p": _entry("same-match", epoch=epoch)})
            stats.save_data({"p": _entry("other-match")})
            kept = stats.read_data()["p"]
            self.assertEqual(len(kept), 2)
            self.assertEqual(kept[0]["match_id"], "same-match")
            self.assertEqual(kept[0]["epoch"], 3.0)
            self.assertEqual(kept[1]["match_id"], "other-match")

    def test_flushed_file_is_run_collapsed_and_capped(self):
        with tempfile_dir() as d:
            path = os.path.join(d, "stats.json")
            stats = Stats(log=_StubLog(), path=path)
            for i in range(PRUNE_KEEP + 30):
                stats.save_data({"p": _entry(f"m{i // 3}")})
            _flushable(stats, stats_mod._now())
            stats.save_data({"p": _entry("final")})
            with open(path, "r", encoding="utf-8") as f:
                on_disk = json.load(f)
            self.assertLessEqual(len(on_disk["p"]), PRUNE_KEEP)
            self.assertEqual(on_disk["p"][-1]["match_id"], "final")


class TestThrottle(unittest.TestCase):
    def test_100_saves_in_window_write_exactly_once(self):
        with tempfile_dir() as d:
            path = os.path.join(d, "stats.json")
            log = _StubLog()
            stats = Stats(log=log, path=path)
            with mock.patch.object(stats_mod, "_now", return_value=100000.0):
                # _last_flush starts at 0.0: the first save flushes at once,
                # the next 99 stay inside the 600 s window and write nothing.
                for i in range(100):
                    stats.save_data({"p": _entry(f"m{i}")})
            flushes = [ln for ln in log.lines if ln.startswith("stats flush:")]
            self.assertEqual(len(flushes), 1)
            with open(path, "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["p"][0]["match_id"], "m0")

    def test_flush_allowed_after_window(self):
        with tempfile_dir() as d:
            path = os.path.join(d, "stats.json")
            log = _StubLog()
            stats = Stats(log=log, path=path)

            def flush_count():
                return len(
                    [ln for ln in log.lines if ln.startswith("stats flush:")]
                )

            stats._last_flush = (
                stats_mod._now() - stats_mod.FLUSH_INTERVAL_SECONDS
            )
            stats.save_data({"p": _entry("m1")})
            self.assertEqual(flush_count(), 1)
            stats._last_flush = stats_mod._now()  # inside the window again
            for i in range(5):
                stats.save_data({"p": _entry("m2")})
            self.assertEqual(flush_count(), 1)
            stats._last_flush -= stats_mod.FLUSH_INTERVAL_SECONDS
            stats.save_data({"p": _entry("m3")})
            self.assertEqual(flush_count(), 2)

    def test_atexit_flush_writes_even_inside_window(self):
        with tempfile_dir() as d:
            path = os.path.join(d, "stats.json")
            stats = Stats(log=_StubLog(), path=path)
            stats._last_flush = (
                stats_mod._now() - stats_mod.FLUSH_INTERVAL_SECONDS
            )
            stats.save_data({"p": _entry("m1")})
            stats._last_flush = stats_mod._now()  # throttled now
            stats.save_data({"p": _entry("m2")})
            with open(path, "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["p"][0]["match_id"], "m1")
            stats._atexit_flush()  # final flush bypasses the window
            with open(path, "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["p"][-1]["match_id"], "m2")


class TestAtomicWrite(unittest.TestCase):
    def test_crash_between_tmp_and_replace_leaves_old_file_valid(self):
        with tempfile_dir() as d:
            path = os.path.join(d, "stats.json")
            log = _StubLog()
            stats = Stats(log=log, path=path)
            stats.save_data({"p": _entry("old-match")})
            _flushable(stats, stats_mod._now())
            stats.save_data({"p": _entry("old-match")})
            with open(path, "r", encoding="utf-8") as f:
                good = f.read()
            stats.save_data({"p": _entry("new-match")})
            with mock.patch.object(
                stats_mod.os, "replace", side_effect=OSError("boom")
            ):
                _flushable(stats, stats_mod._now())
                stats._flush()  # crash point: tmp fully written, replace dies
            with open(path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), good)
            self.assertFalse(os.path.exists(path + ".tmp"))
            self.assertIn("stats flush failed", log.text())

    def test_flushed_file_is_atomic_valid_json(self):
        with tempfile_dir() as d:
            path = os.path.join(d, "stats.json")
            stats = Stats(log=_StubLog(), path=path)
            stats.save_data({"p": _entry("m1")})
            _flushable(stats, stats_mod._now())
            stats.save_data({"p": _entry("m1")})
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["p"][0]["match_id"], "m1")


class TestCorruptFile(unittest.TestCase):
    def test_garbage_starts_fresh_and_quarantines(self):
        with tempfile_dir() as d:
            path = os.path.join(d, "stats.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{this is not json at all")
            log = _StubLog()
            stats = Stats(log=log, path=path)
            self.assertEqual(stats.read_data(), {})
            self.assertTrue(os.path.exists(path + ".corrupt"))
            self.assertFalse(os.path.exists(path))
            self.assertIn(
                "stats.json corrupt: moved to stats.json.corrupt, "
                "starting fresh",
                log.text(),
            )

    def test_truncated_file_is_salvaged_and_quarantined(self):
        with tempfile_dir() as d:
            path = os.path.join(d, "stats.json")
            payload = json.dumps(
                {
                    "aaa": [_entry("m1"), _entry("m2")],
                    "bbb": [_entry("m3"), _entry("m4"), _entry("m5")],
                }
            )
            # Cut mid-value inside the LAST entry (the real file was cut
            # inside a trailing epoch number): everything before the last
            # incomplete entry must survive the salvage.
            cut = payload.rindex('"epoch"') + len('"epoch": ')
            with open(path, "w", encoding="utf-8") as f:
                f.write(payload[:cut])
            log = _StubLog()
            stats = Stats(log=log, path=path)
            data = stats.read_data()
            self.assertEqual(set(data), {"aaa", "bbb"})
            self.assertEqual(
                [e["match_id"] for e in data["bbb"]], ["m3", "m4"]
            )
            self.assertTrue(os.path.exists(path + ".corrupt"))
            self.assertIn("salvaged", log.text())

    def test_non_dict_top_level_starts_fresh(self):
        with tempfile_dir() as d:
            path = os.path.join(d, "stats.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(["not", "a", "dict"], f)
            log = _StubLog()
            stats = Stats(log=log, path=path)
            self.assertEqual(stats.read_data(), {})
            self.assertIn("stats.json corrupt", log.text())


class TestFlushLog(unittest.TestCase):
    def test_flush_logs_byte_count(self):
        with tempfile_dir() as d:
            path = os.path.join(d, "stats.json")
            log = _StubLog()
            stats = Stats(log=log, path=path)
            stats._last_flush = stats_mod._now()  # suppress the first flush
            stats.save_data({"p": _entry("m1")})
            _flushable(stats, stats_mod._now())
            stats.save_data({"p": _entry("m1")})
            expected = os.path.getsize(path)
            matching = [
                ln
                for ln in log.lines
                if ln == f"stats flush: {expected} bytes"
            ]
            self.assertEqual(len(matching), 1)


class TestFlushNow(unittest.TestCase):
    def test_flush_now_writes_when_dirty_ignoring_interval(self):
        with tempfile_dir() as d:
            path = os.path.join(d, "stats.json")
            log = _StubLog()
            stats = Stats(log=log, path=path)
            stats._last_flush = stats_mod._now()  # deep inside the window
            stats.save_data({"p": _entry("m1")})
            # Throttled: nothing on disk yet.
            self.assertFalse(os.path.exists(path))
            # flush_now ignores the interval and writes immediately.
            stats.flush_now()
            with open(path, "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["p"][0]["match_id"], "m1")
            self.assertFalse(stats._dirty)
            self.assertIn(
                f"stats flush: {os.path.getsize(path)} bytes", log.text()
            )

    def test_flush_now_is_clean_noop_when_not_dirty(self):
        with tempfile_dir() as d:
            path = os.path.join(d, "stats.json")
            log = _StubLog()
            stats = Stats(log=log, path=path)
            stats.flush_now()  # nothing saved: no file, no log line
            self.assertFalse(os.path.exists(path))
            self.assertEqual(log.lines, [])
            # One pending save (throttle suppressed), flushed by flush_now.
            stats._last_flush = stats_mod._now()
            stats.save_data({"p": _entry("m1")})
            stats.flush_now()
            with open(path, "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["p"][0]["match_id"], "m1")
            flushes = [ln for ln in log.lines if ln.startswith("stats flush:")]
            self.assertEqual(len(flushes), 1)
            # Clean now: a second flush_now writes nothing further.
            stats.flush_now()
            self.assertEqual(
                [ln for ln in log.lines if ln.startswith("stats flush:")],
                flushes,
            )

    def test_log_lines_go_through_the_injected_callable(self):
        with tempfile_dir() as d:
            path = os.path.join(d, "stats.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"puuid": [_entry("m1")]}, f)
            log = _StubLog()
            with mock.patch(
                "builtins.print", side_effect=AssertionError("print leaked")
            ):
                stats = Stats(log=log, path=path)
                stats.save_data({"p": _entry("m2")})
                stats.flush_now()
            self.assertTrue(
                any(ln.startswith("stats loaded:") for ln in log.lines)
            )
            self.assertTrue(
                any(ln.startswith("stats flush:") for ln in log.lines)
            )


class TestSignaturesUnchanged(unittest.TestCase):
    def test_bare_construction_and_call_shapes(self):
        with tempfile_dir() as d:
            os.environ_backup = os.environ.get("APPDATA")
            os.environ["APPDATA"] = d
            try:
                stats = Stats()  # main.py calls Stats() with no args
                self.assertEqual(stats.read_data(), {})
                stats.save_data({"puuid": _entry("m1")})
                self.assertEqual(stats.read_data(), {"puuid": [_entry("m1")]})
            finally:
                if os.environ_backup is None:
                    os.environ.pop("APPDATA", None)
                else:
                    os.environ["APPDATA"] = os.environ_backup


if __name__ == "__main__":
    unittest.main()
