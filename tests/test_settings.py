"""Tests for the declared settings schema (src/settings.py) and the
typo-loudness of Config's accessors. Run with the py launcher from the
repo root."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.constants import DEFAULT_CONFIG
from src.settings import (
    KNOWN_FLAGS,
    KNOWN_TABLE_FLAGS,
    KNOWN_TOP_KEYS,
    validate_config_dict,
)


class _StubLog:
    def __init__(self):
        self.lines = []

    def __call__(self, message):
        self.lines.append(message)


class KnownKeysTests(unittest.TestCase):
    def test_known_sets_match_defaults_programmatically(self):
        self.assertEqual(KNOWN_TOP_KEYS, frozenset(DEFAULT_CONFIG.keys()))
        self.assertEqual(
            KNOWN_TABLE_FLAGS, frozenset(DEFAULT_CONFIG["table"].keys())
        )
        self.assertEqual(KNOWN_FLAGS, frozenset(DEFAULT_CONFIG["flags"].keys()))


class ValidateConfigDictTests(unittest.TestCase):
    def setUp(self):
        import copy

        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.log = _StubLog()

    def test_valid_config_produces_zero_warnings_and_no_mutation(self):
        validate_config_dict(self.config, self.log)
        self.assertEqual(self.log.lines, [])
        self.assertEqual(
            self.config["cooldown"], DEFAULT_CONFIG["cooldown"]
        )

    def test_unknown_keys_each_produce_exactly_one_named_warning(self):
        self.config["flags"]["not_a_real_flag"] = True
        self.config["table"]["not_a_real_column"] = True
        self.config["not_a_real_top_key"] = 1
        validate_config_dict(self.config, self.log)
        self.assertEqual(len(self.log.lines), 3)
        joined = " ".join(self.log.lines)
        for key in ("not_a_real_flag", "not_a_real_column", "not_a_real_top_key"):
            self.assertIn(key, joined)
            self.assertIn("unknown", joined)

    def test_wrong_typed_value_warns_without_raising_or_mutating(self):
        self.config["cooldown"] = "ten"
        self.config["flags"]["discord_rpc"] = "yes"
        self.config["table"]["rr"] = "always"
        validate_config_dict(self.config, self.log)
        self.assertEqual(len(self.log.lines), 3)
        self.assertIn("wrong type", " ".join(self.log.lines))
        # Never mutates: wrong-typed value is kept, not replaced.
        self.assertEqual(self.config["cooldown"], "ten")

    def test_unknown_and_wrong_typed_inside_non_dict_containers(self):
        self.config["flags"] = "garbage"
        self.config["table"] = 5
        validate_config_dict(self.config, self.log)
        self.assertEqual(len(self.log.lines), 2)
        self.assertIn("wrong type", self.log.lines[0])


class ConfigAccessorTypoLoudnessTests(unittest.TestCase):
    def _bare_config(self, flags=None, table=None):
        from src.config import Config

        config = object.__new__(Config)
        config.log = _StubLog()
        if flags is not None:
            config.__dict__["flags"] = flags
        if table is not None:
            config.__dict__["table"] = table
        return config

    def test_get_feature_flag_unknown_key_raises_keyerror(self):
        config = self._bare_config(flags={})
        with self.assertRaises(KeyError):
            config.get_feature_flag("not_a_real_flag")

    def test_get_table_flag_missing_key_falls_back_to_default(self):
        config = self._bare_config(table={})
        self.assertEqual(
            config.get_table_flag("rr"),
            DEFAULT_CONFIG["table"]["rr"],
        )


if __name__ == "__main__":
    unittest.main()
