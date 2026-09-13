import json
import os
import threading
import time

CACHE_FILE = "name_cache.json"


class NameCache:
    def __init__(self, log):
        self.log = log
        self.cache = {}
        self._dirty = False
        # RLock (reentrant) because save_now() nests _save(); a background
        # bootstrap thread may write while the main loop reads.
        self._lock = threading.RLock()
        self._load()

    def _load(self):
        with self._lock:
            if os.path.exists(CACHE_FILE):
                try:
                    with open(CACHE_FILE, "r", encoding="utf-8") as f:
                        self.cache = json.load(f)
                    self.log(f"Name cache loaded: {len(self.cache)} entries")
                except Exception as e:
                    self.log(f"Name cache load failed: {e}")
                    self.cache = {}

    def _save(self):
        with self._lock:
            try:
                with open(CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.cache, f, indent=2, ensure_ascii=False)
                self._dirty = False
            except Exception as e:
                self.log(f"Name cache save failed: {e}")

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
        if self._dirty:
            self._save()
