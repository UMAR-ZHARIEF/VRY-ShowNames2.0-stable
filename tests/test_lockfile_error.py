"""LockfileError behavior when VALORANT is not running.

Read-only hardening wave 1: the auto-start of VALORANT via the account
manager was removed. When the lockfile does not exist, LockfileError must
print the restart guidance and exit cleanly (os._exit(0)) instead of
launching the Riot client and waiting for the lockfile to appear.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.errors import Error


class LockfileErrorTests(unittest.TestCase):
    def setUp(self):
        self.err = Error(log=lambda message: None)

    def test_missing_lockfile_prints_message_and_exits_cleanly(self):
        log_lines = []
        err = Error(log=log_lines.append)
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = os.path.join(tmp, "lockfile")
            with mock.patch("builtins.print") as fake_print, mock.patch(
                "os._exit", side_effect=SystemExit
            ) as fake_exit:
                with self.assertRaises(SystemExit):
                    err.LockfileError(missing_path)
        fake_exit.assert_called_once_with(0)
        fake_print.assert_called_once_with(
            "VALORANT is not running. Start the game, then reopen VRY ShowNames."
        )
        self.assertTrue(
            any("Lockfile does not exist" in line for line in log_lines),
            "LockfileError must log that the lockfile is missing",
        )

    def test_existing_lockfile_returns_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing_path = os.path.join(tmp, "lockfile")
            with open(existing_path, "w") as lockfile:
                lockfile.write("name:1:port:pass:https")
            self.assertTrue(self.err.LockfileError(existing_path))

    def test_existing_lockfile_with_ignoreLockfile_returns_true(self):
        # ignoring lockfile is for when lockfile exists but it's not really
        # valid; semantics must stay intact for requestsV.get_lockfile.
        with tempfile.TemporaryDirectory() as tmp:
            existing_path = os.path.join(tmp, "lockfile")
            with open(existing_path, "w") as lockfile:
                lockfile.write("name:1:port:pass:https")
            with mock.patch("os.system") as fake_system:
                self.assertTrue(self.err.LockfileError(existing_path, ignoreLockfile=True))
            fake_system.assert_called_once_with("cls")


if __name__ == "__main__":
    unittest.main()
