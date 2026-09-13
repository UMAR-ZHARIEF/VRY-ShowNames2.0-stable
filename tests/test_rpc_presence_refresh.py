"""Source-level guard: the Discord card presence must refresh in BOTH paths.

Regression context: under the polling path (game_chat off) rpc.set_rpc was
only called in the firstTime block, so the Discord card froze on the
session's first presence forever (card stuck "in game" minutes after the
match ended). This test pins that both the firstTime block and the polling
else-branch refresh the presence, each gated by the discord_rpc flag.

Pure source analysis (stdlib only): no imports of main, no execution.
"""
import os
import unittest

MAIN_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "main.py"
)

FIRSTTIME_MARK = "if firstTime:"
FIRSTTIME_END_MARK = 'log(f"first game state: {game_state}")'
POLL_MARK = "next_poll_seconds(game_state"
POLL_END_MARK = 'log(f"new game state: {game_state}")'
CALL = "rpc.set_rpc(private_presence)"
FLAG = 'get_feature_flag("discord_rpc")'
PRESENCE_GUARD = "private_presence is not None"


def _block(source, start_mark, end_mark):
    start = source.index(start_mark)
    end = source.index(end_mark, start)
    return source[start:end]


class PresenceRefreshSourceTests(unittest.TestCase):
    def setUp(self):
        with open(MAIN_PATH, "r", encoding="utf-8") as handle:
            self.source = handle.read()

    def test_firsttime_block_refreshes_presence_gated(self):
        block = _block(self.source, FIRSTTIME_MARK, FIRSTTIME_END_MARK)
        self.assertIn(CALL, block)
        self.assertIn(FLAG, block)
        self.assertIn(PRESENCE_GUARD, block)

    def test_polling_block_refreshes_presence_gated(self):
        block = _block(self.source, POLL_MARK, POLL_END_MARK)
        self.assertIn(CALL, block)
        self.assertIn(FLAG, block)
        self.assertIn(PRESENCE_GUARD, block)

    def test_polling_block_refresh_happens_per_poll_cycle(self):
        # The refresh must sit inside the per-poll block (between the poll
        # sleep and the state derivation), not at some unrelated spot.
        block = _block(self.source, "time.sleep(next_poll_seconds(", POLL_END_MARK)
        call_at = block.index(CALL)
        derive_at = block.index("derive_game_state(private_presence)")
        self.assertLess(call_at, derive_at)

    def test_exactly_two_refresh_call_sites_in_main(self):
        # One in firstTime, one in the polling branch. More would mean the
        # call leaked into another block; fewer means a path lost its guard.
        self.assertEqual(self.source.count(CALL), 2)


if __name__ == "__main__":
    unittest.main()
