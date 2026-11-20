import atexit
import json
import os
import time

STATS_FILE = "stats.json"
# Keep the newest PRUNE_KEEP entries per player. Entries are first collapsed
# per run of consecutive same-match_id entries (the app used to append one
# entry per player per 2-second scoreboard cycle, so one match could hold
# hundreds of duplicates; see research/ssdfix-w1-stats.md). After collapsing,
# every consumer of the list (main.py INGAME block) reads only the newest
# entry and the newest entry from a different match; the "Total played"
# counter reads distinct match_ids, whose real maximum over two months of
# history was 18, so 25 keeps every visible number intact.
PRUNE_KEEP = 25
# At most one disk rewrite per 10 minutes keeps SSD wear bounded
# (~6 flushes/hour worst case, ~47 KB each with the prune); a final flush at
# exit covers the rest. Mirrors the name_cache.py flush policy.
FLUSH_INTERVAL_SECONDS = 600

_now = time.monotonic


class Stats:
    def __init__(self, log=None, path=None):
        # main.py constructs Stats() bare; the default logger prints, which is
        # consistent with the app's own console output. Tests inject a stub.
        self._log = log if log is not None else print
        if path is None:
            base = os.path.join(os.getenv("APPDATA") or os.getcwd(), "vry")
            path = os.path.join(base, STATS_FILE)
        self.path = path
        self.data = {}
        self._dirty = False
        # 0.0 makes the first dirty save flush immediately (contract: 100
        # saves within the throttle window -> exactly one file write); every
        # later save inside 600 s of a flush writes nothing.
        self._last_flush = 0.0
        self._load()
        atexit.register(self._atexit_flush)

    def save_data(self, data):
        # In-memory append + mark dirty; the actual disk write is throttled.
        if not isinstance(data, dict):
            return
        for puuid, entry in data.items():
            entries = self.data.get(puuid)
            if not isinstance(entries, list):
                entries = []
            entries.append(entry)
            self.data[puuid] = _prune(entries)
        self._dirty = True
        self._maybe_flush()

    def read_data(self):
        # The in-memory dict is the source of truth; zero file I/O.
        return self.data

    def convert_time(self, s):
        s = int(s)
        if s < 60:
            if s == 1:
                return f"{s} second"
            else:
                return f"{s} seconds"
        elif s < 3600:
            if s // 60 == 1:
                return f"{s // 60} minute"
            else:
                return f"{s // 60} minutes"
        elif s < 86400:
            if s // 3600 == 1:
                return f"{s // 3600} hours"
            else:
                return f"{s // 3600} hours"
        else:
            if s // 86400 == 1:
                return f"{s // 86400} days"
            else:
                return f"{s // 86400} days"

    # ---- internals -----------------------------------------------------

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                text = f.read()
        except (FileNotFoundError, NotADirectoryError, OSError):
            return  # missing file: normal first run, fresh start
        try:
            loaded = json.loads(text)
        except ValueError:
            loaded = self._salvage(text)
            self._quarantine()
            if not isinstance(loaded, dict):
                self._log(
                    "stats.json corrupt: moved to stats.json.corrupt, "
                    "starting fresh"
                )
                return
            self.data = {
                puuid: _prune(entries)
                for puuid, entries in loaded.items()
                if isinstance(entries, list)
            }
            self._log(
                f"stats.json corrupt: salvaged {len(self.data)} players "
                "from the truncated stats.json prefix"
            )
            return
        if not isinstance(loaded, dict):
            self._quarantine()
            self._log(
                "stats.json corrupt: moved to stats.json.corrupt, "
                "starting fresh"
            )
            return
        self.data = {
            puuid: _prune(entries)
            for puuid, entries in loaded.items()
            if isinstance(entries, list)
        }
        self._log(f"stats loaded: {len(self.data)} players")

    def _salvage(self, text):
        """Timeboxed best-effort recovery of a truncated JSON object.

        First a plain raw_decode (works when the truncation is whitespace
        only). If that fails, cut the text at structural closers from the
        end and balance the remaining open brackets, trying at most 30
        candidates within ~10 seconds; beyond that, fresh start.
        """
        decoder = json.JSONDecoder()
        try:
            loaded, _ = decoder.raw_decode(text)
            if isinstance(loaded, dict):
                return loaded
        except ValueError:
            pass
        stack = []
        in_str = False
        escaped = False
        closers = []  # (index, tuple of brackets still open after the closer)
        for i, ch in enumerate(text):
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append(ch)
            elif ch in "}]":
                if stack:
                    stack.pop()
                    closers.append((i, tuple(stack)))
                    if len(closers) > 200:
                        del closers[:100]
        deadline = _now() + 10.0
        tries = 0
        for cut, open_brackets in reversed(closers):
            if tries >= 30 or _now() > deadline:
                break
            tries += 1
            candidate = text[: cut + 1] + "".join(
                "}" if b == "{" else "]" for b in reversed(open_brackets)
            )
            try:
                loaded, _ = decoder.raw_decode(candidate)
                if isinstance(loaded, dict):
                    return loaded
            except ValueError:
                continue
        return None

    def _quarantine(self):
        """Move the unreadable file to stats.json.corrupt, preserving
        evidence. Plain os.rename per the fix contract; os.replace only as
        the documented fallback when a previous .corrupt already exists."""
        corrupt_path = self.path + ".corrupt"
        try:
            os.rename(self.path, corrupt_path)
        except OSError:
            try:
                os.replace(self.path, corrupt_path)
            except OSError:
                self._log(
                    "stats.json corrupt: could not move it aside, "
                    "starting fresh"
                )

    def _maybe_flush(self):
        if not self._dirty:
            return
        if _now() - self._last_flush < FLUSH_INTERVAL_SECONDS:
            return
        self._flush()

    def _flush(self):
        payload = json.dumps(self.data, ensure_ascii=False).encode("utf-8")
        tmp_path = self.path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(tmp_path, "wb") as f:
                f.write(payload)
            os.replace(tmp_path, self.path)
        except Exception as e:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            self._log(f"stats flush failed: {e}")
            return
        self._dirty = False
        self._last_flush = _now()
        self._log(f"stats flush: {len(payload)} bytes")

    def flush_now(self):
        # Public final flush for exit paths (os._exit skips atexit, so the
        # registered final flush is lost there): bypasses the 10-minute
        # throttle window but stays a no-op when clean. Same atomic write and
        # byte-count log line as a throttled flush.
        if self._dirty:
            self._flush()

    def _atexit_flush(self):
        # One final flush on interpreter exit, bypassing the throttle window.
        try:
            self.flush_now()
        except Exception:
            pass


def _prune(entries):
    """Collapse consecutive same-match_id runs to their last entry, then cap
    to the newest PRUNE_KEEP entries. Keep-last preserves exactly what the
    consumers read: the newest entry's epoch and the walk-back target (the
    last entry of the previous distinct match). Non-dict entries and entries
    without a match_id are never collapsed."""
    collapsed = []
    prev_match_id = None
    for entry in entries:
        match_id = entry.get("match_id") if isinstance(entry, dict) else None
        if match_id is not None and match_id == prev_match_id:
            collapsed[-1] = entry
        else:
            collapsed.append(entry)
        prev_match_id = match_id
    return collapsed[-PRUNE_KEEP:]
