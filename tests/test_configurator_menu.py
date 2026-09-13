"""Guard tests for the configurator's Discord Appearance menu wiring.

Pins that the 'Discord Appearance' menu entry in src/configurator.py is
wired to src.questions.rpc_overrides_questions (PLURAL), that the option
schema covers exactly the eight rpc_overrides keys (the six original
value overrides plus the rpc_state pin and the map override), and that
DEFAULT_CONFIG carries the same section with 'real' defaults. Also
guards against: duplicate definitions of the questions/options in
src/questions.py, and the name-seam regression where the configurator
called the singular 'rpc_override_questions' (never defined) instead of
the plural.
"""
import ast
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.constants import DEFAULT_CONFIG

import src.configurator as configurator
import src.questions as questions

CONTRACT_KEYS = {
    "rank",
    "agent",
    "party_size",
    "party_max",
    "score_ally",
    "score_enemy",
    "rpc_state",
    "map",
    "game_mode",
}
CONFIGURATOR_SOURCE = pathlib.Path(configurator.__file__).read_text(encoding="utf-8")
PLURAL_NAME = "rpc_overrides_questions"
SINGULAR_NAME = "rpc_override_questions"


class ConfiguratorMenuWiringTests(unittest.TestCase):
    def test_configurator_module_imports(self):
        self.assertTrue(hasattr(configurator, "configure"))

    def test_menu_wires_rpc_overrides_questions(self):
        self.assertIn(PLURAL_NAME, CONFIGURATOR_SOURCE)
        self.assertIn("Discord Appearance", CONFIGURATOR_SOURCE)

    def test_no_singular_call_remains_in_configurator(self):
        # 'rpc_override_questions' is not a substring of the plural name,
        # so this catches a regression to the singular spelling exactly.
        self.assertNotIn(SINGULAR_NAME + "(", CONFIGURATOR_SOURCE)

    def test_questions_module_resolves_plural_not_singular(self):
        # Runtime resolution: the name the configurator calls must exist on
        # the questions module, and the singular spelling must not.
        self.assertTrue(callable(getattr(questions, PLURAL_NAME)))
        self.assertFalse(hasattr(questions, SINGULAR_NAME))

    def test_rpc_overrides_questions_is_callable(self):
        self.assertTrue(callable(questions.rpc_overrides_questions))

    def test_options_cover_exactly_the_contract_keys(self):
        self.assertEqual(set(questions.RPC_OVERRIDE_OPTS.keys()), CONTRACT_KEYS)

    def test_default_config_has_contract_real_defaults(self):
        section = DEFAULT_CONFIG["rpc_overrides"]
        self.assertEqual(set(section.keys()), CONTRACT_KEYS)
        for value in section.values():
            self.assertEqual(value, "real")

    def test_no_duplicate_option_definitions_in_questions_source(self):
        source = pathlib.Path(questions.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count("RPC_OVERRIDE_OPTS = {"), 1)
        self.assertEqual(source.count(PLURAL_NAME + " ="), 1)


class QuestionsFunctionBehaviorTests(unittest.TestCase):
    def test_questions_use_default_config_for_missing_section(self):
        result = questions.rpc_overrides_questions({})
        self.assertEqual(len(result), len(CONTRACT_KEYS))
        for question in result:
            self.assertEqual(question["default"], "real")

    def test_questions_prefill_from_config_section(self):
        config = {"rpc_overrides": {"rank": "Immortal 1", "agent": "real"}}
        result = questions.rpc_overrides_questions(config)
        by_name = {question["name"]: question["default"] for question in result}
        self.assertEqual(by_name["rank"], "Immortal 1")
        self.assertEqual(by_name["agent"], "real")
        self.assertEqual(by_name["party_size"], "real")


if __name__ == "__main__":
    unittest.main()
