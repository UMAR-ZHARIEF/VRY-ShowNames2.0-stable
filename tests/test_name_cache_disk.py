"""Disk-behavior tests for the name cache (src/name_cache.py): throttled
flushes, change detection, atomic writes, corrupt-file recovery, and the
flush log line. Run with the py launcher from the repo root."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.name_cache as nc


class _StubLog:
    def __init__(self):
        self.lines = []

    def __call__(self, message):
        self.lines.append(message)


class NameCacheDiskTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_path = os.path.join(self._tmp.name, "name_cache.json")
        self._old_cache_file = nc.CACHE_FILE
        nc.CACHE_FILE = self.cache_path
        self.addCleanup(setattr, nc, "CACHE_FILE", self._old_cache_file)
        # Controllable monotonic clock: tests move self.now forward.
        self.now = 1000.0
        patcher = mock.patch.object(
            nc.time, "monotonic", side_effect=lambda: self.now
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.log = _StubLog()

    def _flush_lines(self):
        return [l for l in self.log.lines if l.startswith("name cache flush:")]


class NoChangeNoWriteTests(NameCacheDiskTests):
    def test_save_now_with_no_changes_ever_writes_nothing(self):
        cache = nc.NameCache(self.log)
        self.now += nc.FLUSH_INTERVAL_SECONDS + 1
        cache.save_now()
        self.assertFalse(os.path.exists(self.cache_path))
        self.assertEqual(self._flush_lines(), [])

    def test_setting_same_name_again_after_flush_writes_nothing(self):
        cache = nc.NameCache(self.log)
        cache.set("puuid-1", "Player#EUW", source="test")
        self.now += nc.FLUSH_INTERVAL_SECONDS + 1
        cache.save_now()
        self.assertEqual(len(self._flush_lines()), 1)
        # Unchanged cycle: same name re-set never marks dirty or flushes.
        cache.set("puuid-1", "Player#EUW", source="test")
        self.now += nc.FLUSH_INTERVAL_SECONDS + 1
        cache.save_now()
        self.assertEqual(len(self._flush_lines()), 1)

    def test_save_now_without_dirty_inside_window_writes_nothing(self):
        cache = nc.NameCache(self.log)
        cache.set("puuid-1", "Player#EUW", source="test")
        cache._dirty = False  # simulate data already persisted elsewhere
        cache.save_now()
        self.assertFalse(os.path.exists(self.cache_path))


class OneWritePerWindowTests(NameCacheDiskTests):
    def _counting_replace(self):
        real_replace = nc.os.replace
        calls = []

        def counting(src, dst):
            calls.append(dst)
            return real_replace(src, dst)

        return calls, counting

    def test_changed_data_flushes_once_per_window(self):
        calls, counting = self._counting_replace()
        cache = nc.NameCache(self.log)
        cache.set("puuid-1", "Player#EUW", source="test")
        # Inside the window: no write yet.
        cache.save_now()
        self.assertEqual(calls, [])
        self.assertFalse(os.path.exists(self.cache_path))
        # Past the window: exactly one write.
        self.now += nc.FLUSH_INTERVAL_SECONDS + 1
        with mock.patch.object(nc.os, "replace", side_effect=counting):
            cache.save_now()
        self.assertEqual(calls, [self.cache_path])
        self.assertTrue(os.path.exists(self.cache_path))
        # New change while still inside the new window: no second write.
        cache.set("puuid-2", "Rival#EUW", source="test")
        self.now += nc.FLUSH_INTERVAL_SECONDS - 1
        with mock.patch.object(nc.os, "replace", side_effect=counting):
            cache.save_now()
        self.assertEqual(calls, [self.cache_path])
        # Same pending change, window now passed: exactly one more write.
        self.now += 2
        with mock.patch.object(nc.os, "replace", side_effect=counting):
            cache.save_now()
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self._flush_lines()), 2)

    def test_new_puuid_marks_dirty_but_set_of_known_name_does_not(self):
        cache = nc.NameCache(self.log)
        cache.set("puuid-1", "Player#EUW", source="test")
        self.assertTrue(cache._dirty)
        cache._dirty = False
        cache.set("puuid-1", "Player#EUW", source="another-source")
        self.assertFalse(cache._dirty)


class AtomicWriteTests(NameCacheDiskTests):
    def test_flush_leaves_no_temp_file_and_valid_json(self):
        cache = nc.NameCache(self.log)
        cache.set("puuid-1", "Player#EUW", source="test")
        self.now += nc.FLUSH_INTERVAL_SECONDS + 1
        cache.save_now()
        self.assertFalse(os.path.exists(self.cache_path + ".tmp"))
        with open(self.cache_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["puuid-1"]["name"], "Player#EUW")

    def test_failed_replace_keeps_old_file_and_stays_dirty(self):
        cache = nc.NameCache(self.log)
        cache.set("puuid-1", "Player#EUW", source="test")
        self.now += nc.FLUSH_INTERVAL_SECONDS + 1
        cache.save_now()
        with open(self.cache_path, encoding="utf-8") as f:
            old_content = f.read()
        # Change data, then make the atomic rename fail.
        cache.set("puuid-2", "Rival#EUW", source="test")
        self.now += nc.FLUSH_INTERVAL_SECONDS + 1
        with mock.patch.object(
            nc.os, "replace", side_effect=OSError("disk fault")
        ):
            cache.save_now()
        with open(self.cache_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), old_content)
        self.assertFalse(os.path.exists(self.cache_path + ".tmp"))
        self.assertTrue(cache._dirty)
        self.assertTrue(
            any("Name cache save failed" in l for l in self.log.lines)
        )
        # Next attempt in a fresh window recovers and writes the new data.
        self.now += nc.FLUSH_INTERVAL_SECONDS + 1
        cache.save_now()
        with open(self.cache_path, encoding="utf-8") as f:
            self.assertIn("Rival#EUW", f.read())
        self.assertFalse(cache._dirty)


class CorruptLoadTests(NameCacheDiskTests):
    def test_corrupt_json_is_renamed_and_cache_starts_fresh(self):
        with open(self.cache_path, "w", encoding="utf-8") as f:
            f.write("{definitely not json")
        cache = nc.NameCache(self.log)
        self.assertEqual(cache.cache, {})
        corrupt_path = self.cache_path + ".corrupt"
        self.assertTrue(os.path.exists(corrupt_path))
        with open(corrupt_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{definitely not json")
        self.assertFalse(os.path.exists(self.cache_path))
        joined = " ".join(self.log.lines)
        self.assertIn("CORRUPT", joined)
        self.assertIn(".corrupt", joined)

    def test_non_dict_top_level_is_treated_as_corrupt(self):
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
        cache = nc.NameCache(self.log)
        self.assertEqual(cache.cache, {})
        self.assertTrue(os.path.exists(self.cache_path + ".corrupt"))
        self.assertFalse(os.path.exists(self.cache_path))

    def test_valid_load_still_works(self):
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump({"puuid-1": {"name": "Player#EUW"}}, f)
        cache = nc.NameCache(self.log)
        self.assertEqual(cache.get("puuid-1"), "Player#EUW")
        self.assertTrue(
            any("Name cache loaded: 1 entries" in l for l in self.log.lines)
        )
        self.assertFalse(os.path.exists(self.cache_path + ".corrupt"))


class FlushLogTests(NameCacheDiskTests):
    def test_flush_log_line_reports_exact_byte_count(self):
        cache = nc.NameCache(self.log)
        cache.set("puuid-1", "Player#EUW", source="test")
        self.now += nc.FLUSH_INTERVAL_SECONDS + 1
        cache.save_now()
        flush_lines = self._flush_lines()
        self.assertEqual(len(flush_lines), 1)
        reported = int(flush_lines[0].split(":")[1].strip().split()[0])
        self.assertEqual(reported, os.path.getsize(self.cache_path))


class FlushNowTests(NameCacheDiskTests):
    def test_flush_now_writes_when_dirty_ignoring_window(self):
        cache = nc.NameCache(self.log)
        cache.set("puuid-1", "Player#EUW", source="test")
        # Still inside the window: save_now writes nothing...
        cache.save_now()
        self.assertFalse(os.path.exists(self.cache_path))
        # ...but flush_now ignores the window and writes immediately.
        cache.flush_now()
        self.assertTrue(os.path.exists(self.cache_path))
        self.assertFalse(cache._dirty)
        self.assertEqual(len(self._flush_lines()), 1)
        with open(self.cache_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["puuid-1"]["name"], "Player#EUW")

    def test_flush_now_is_clean_noop_when_not_dirty(self):
        cache = nc.NameCache(self.log)
        cache.flush_now()  # never dirty: no file, no log line
        self.assertFalse(os.path.exists(self.cache_path))
        self.assertEqual(self._flush_lines(), [])


class AtexitFlushTests(NameCacheDiskTests):
    def test_exit_flush_ignores_window(self):
        cache = nc.NameCache(self.log)
        cache.set("puuid-1", "Player#EUW", source="test")
        # Still inside the flush window.
        cache._atexit_flush()
        self.assertTrue(os.path.exists(self.cache_path))
        self.assertFalse(cache._dirty)
        self.assertEqual(len(self._flush_lines()), 1)


if __name__ == "__main__":
    unittest.main()
