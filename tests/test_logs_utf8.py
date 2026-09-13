"""Logs must keep non-ASCII characters verbatim (UTF-8, no '?' mangling).

Regression background: src/logs.py used to write every line through
log_string.encode('ascii', 'replace'), which turned the Discord card's
middle-dot separator ('·') into '?' inside log files, hiding real card
content from anyone reading the log. The writer is now UTF-8.
"""
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from src.logs import Logging  # noqa: E402


class LogsUtf8Test(unittest.TestCase):
    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        # Fresh directory: the logger starts numbering at log-1.txt here.
        self.logger = Logging()

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def _log_file_text(self):
        path = self.logger.logFileName
        self.assertTrue(os.path.isfile(path), f"log file missing: {path}")
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_middle_dot_separator_survives(self):
        line = "RPC: sent state=INGAME details=Competitive // 4 - 9 · Waylay small=23 party=In a Party (4 of 5)"
        self.logger.log(line)
        text = self._log_file_text()
        self.assertIn(line, text)
        self.assertIn("·", text)
        self.assertNotIn("?", text.replace("line 1", ""))

    def test_emoji_and_cjk_survive(self):
        samples = ["card emoji 🙂 test", "CJK: 中文エージェント"]
        for sample in samples:
            self.logger.log(sample)
        text = self._log_file_text()
        for sample in samples:
            self.assertIn(sample, text)

    def test_ascii_lines_still_written(self):
        self.logger.log("plain ascii line 123")
        text = self._log_file_text()
        self.assertIn("plain ascii line 123", text)

    def test_numbering_starts_at_log_1_in_fresh_dir(self):
        self.logger.log("first")
        self.assertTrue(os.path.isfile("logs/log-1.txt"))


if __name__ == "__main__":
    unittest.main()
