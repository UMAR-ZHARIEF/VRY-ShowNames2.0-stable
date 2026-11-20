import atexit
import json
import os
import threading
import time

CACHE_FILE = "name_cache.json"
# At most one disk rewrite per 10 minutes keeps SSD wear bounded
# (~6 flushes/hour worst case) while losing at most that window of new
# name entries on a crash; a final flush at exit covers the rest.
FLUSH_INTERVAL_SECONDS = 600


class NameCache:
    def __init__(self, log):
        self.log = log
        self.cache = {}
        self._dirty = False
        self._last_flush = time.monotonic()
        # RLock (reentrant) because save_now() nests _flush(); a background
        # bootstrap thread may write while the main loop reads.
        self._lock = threading.RLock()
        self._load()
        atexit.register(self._atexit_flush)

    def _load(self):
        with self._lock:
            if os.path.exists(CACHE_FILE):
                try:
                    with open(CACHE_FILE, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if not isinstance(loaded, dict):
                        raise ValueError("name cache top level is not an object")
                    self.cache = loaded
                    self.log(f"Name cache loaded: {len(self.cache)} entries")
                except Exception as e:
                    self.cache = {}
                    corrupt_path = CACHE_FILE + ".corrupt"
                    try:
                        os.replace(CACHE_FILE, corrupt_path)
                        self.log(
                            f"NAME CACHE FILE CORRUPT ({e}); moved to "
                            f"{corrupt_path} and starting fresh"
                        )
                    except OSError:
                        self.log(
                            f"NAME CACHE FILE CORRUPT ({e}); could not move "
                            "it, starting fresh"
                        )

    def _flush(self):
        # Caller must hold self._lock.
        payload = json.dumps(
            self.cache, indent=2, ensure_ascii=False
        ).encode("utf-8")
        tmp_path = CACHE_FILE + ".tmp"
        try:
            with open(tmp_path, "wb") as f:
                f.write(payload)
            os.replace(tmp_path, CACHE_FILE)
        except Exception as e:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            self.log(f"Name cache save failed: {e}")
            return
        self._dirty = False
        self._last_flush = time.monotonic()
        self.log(f"name cache flush: {len(payload)} bytes")

    def get(self, puuid):
        with self._lock:
            entry = self.cache.get(puuid)
            return entry.get("name", "") if entry else ""

    def set(self, puuid, name, source="unknown"):
        with self._lock:
            if not puuid or not name or not name.split("#")[0]:
                return
            existing = self.cache.get(puuid, {})
            if existing.get("name") == name:
                return
            self.cache[puuid] = {
                "name": name,
                "last_seen": int(time.time()),
                "source": source,
            }
            self._dirty = True

    def save_now(self):
        with self._lock:
            if not self._dirty:
                return
            if time.monotonic() - self._last_flush < FLUSH_INTERVAL_SECONDS:
                return
            self._flush()

    def flush_now(self):
        # Public final flush for exit paths (os._exit skips atexit, so the
        # registered final flush is lost there): bypasses the 10-minute
        # throttle window but stays a no-op when clean. Same atomic write and
        # byte-count log line as a throttled flush.
        with self._lock:
            if self._dirty:
                self._flush()

    def _atexit_flush(self):
        try:
            self.flush_now()
        except Exception:
            pass
