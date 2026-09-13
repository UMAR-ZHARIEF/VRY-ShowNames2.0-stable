"""Regression net for the log-553 crash class.

log-553: main.py called render_signature / should_redraw (defined in
src/state_polling.py) without importing them, and the app died at the
first draw with NameError. py_compile and the module unit tests cannot
catch a cross-module missing import because main.py is a flat script
that is never imported, so nothing executes its module body except a
live run. This test parses main.py statically instead: every public
name defined by the small helper modules must be imported by main.py
whenever main.py references that bare name.

NOTE: on the PRE-FIX tree (before the h1 import fix in main.py) this
test FAILS BY DESIGN: render_signature and should_redraw are referenced
but not imported. After h1 lands it must pass, and it guards against
the same mistake in future edits.
"""
import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_PATH = os.path.join(ROOT, "main.py")
HELPER_MODULES = [
    os.path.join(ROOT, "src", "state_polling.py"),
    os.path.join(ROOT, "src", "enemy_table_data.py"),
    os.path.join(ROOT, "src", "enemy_probe_loadouts.py"),
    os.path.join(ROOT, "src", "static_content.py"),
]

# The exact pair that caused the log-553 NameError crash at first draw.
LOG_553_REGRESSION_NAMES = ("render_signature", "should_redraw")


def _parse(path):
    with open(path, "r", encoding="utf-8") as file:
        return ast.parse(file.read(), filename=path)


def _imported_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _public_top_level_names(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.add(target.id)
    return names


def _module_scope_name_loads(tree):
    """Name loads at module scope, including try/while/if blocks, but not
    inside function or class bodies (those have their own scopes)."""
    loads = set()

    def visit(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                loads.add(child.id)
            visit(child)

    visit(tree)
    return loads


class TestMainImportsHelperModuleNames(unittest.TestCase):
    def test_every_used_helper_name_is_imported(self):
        main_tree = _parse(MAIN_PATH)
        main_imports = _imported_names(main_tree)
        main_loads = _module_scope_name_loads(main_tree)

        missing = []
        for module_path in HELPER_MODULES:
            module_tree = _parse(module_path)
            for name in _public_top_level_names(module_tree):
                if name in main_loads and name not in main_imports:
                    missing.append(f"{os.path.basename(module_path)} -> {name}")

        self.assertEqual(
            [], missing,
            "main.py uses these helper-module names without importing them: "
            + ", ".join(missing),
        )

    def test_log553_regressions_are_imported(self):
        imports = _imported_names(_parse(MAIN_PATH))
        for name in LOG_553_REGRESSION_NAMES:
            self.assertIn(
                name, imports,
                f"main.py must import '{name}' from src.state_polling "
                f"(log-553 NameError at the first draw).",
            )


if __name__ == "__main__":
    unittest.main()
