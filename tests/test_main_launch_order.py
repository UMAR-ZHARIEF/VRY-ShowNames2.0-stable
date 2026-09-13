"""Launch-order guard: main.py must not touch ctx before creating it.

Third occurrence of the 'checks green, runtime dead' class (screenshot
23:27:45: NameError at line 48, where ctx was used 193 lines before
MatchContext() was created; py_compile and function-scope tests are blind
to module-level execution order). This suite parses main.py with ast and
enforces the order mechanically.

Semantics:
- Statements are visited in module-level import-time execution order
  (if/try/with/for/while bodies, else/finally, and except handlers are
  walked in order; their bodies execute at import).
- Function/class bodies and lambdas are late-bound and are skipped.
- The creation statement is 'ctx = MatchContext()'. Any bare 'ctx' load
  or 'ctx.<attr>' access reached before it is a violation.
"""
import ast
import os
import unittest

MAIN = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"
)

_LATE_BOUND = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_COMPOUND = (ast.If, ast.Try, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)


def is_ctx_creation(stmt):
    return (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
        and stmt.targets[0].id == "ctx"
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Name)
        and stmt.value.func.id == "MatchContext"
    )


def ctx_uses_shallow(stmt):
    """ctx uses in this statement's own expression level: nested statements
    are walked by scan_flow itself (in execution order), and defs/lambdas
    are late-bound and never run at import."""
    out = []

    def visit(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                continue
            if isinstance(child, _LATE_BOUND):
                continue
            if isinstance(child, ast.Name) and child.id == "ctx":
                if isinstance(child.ctx, ast.Load):
                    out.append(child)
            elif isinstance(child, ast.Attribute) and isinstance(
                child.value, ast.Name
            ) and child.value.id == "ctx":
                out.append(child)
            visit(child)

    visit(stmt)
    return out


def scan_flow(stmts, created):
    """Walk statements in execution order. Returns (violation, created)."""
    for stmt in stmts:
        if isinstance(stmt, _LATE_BOUND):
            continue
        if is_ctx_creation(stmt):
            created = True
        else:
            for use in ctx_uses_shallow(stmt):
                if not created:
                    return (f"'ctx' used at line {use.lineno} before "
                            f"'ctx = MatchContext()'", created)
        for attr in ("body", "orelse", "finalbody"):
            inner = getattr(stmt, attr, None)
            if inner:
                violation, created = scan_flow(inner, created)
                if violation:
                    return (violation, created)
        for handler in getattr(stmt, "handlers", []) or []:
            violation, created = scan_flow(handler.body, created)
            if violation:
                return (violation, created)
    return (None, created)


def find_violation(source):
    violation, _ = scan_flow(ast.parse(source).body, False)
    return violation


class MainLaunchOrderTests(unittest.TestCase):
    def setUp(self):
        with open(MAIN, "r", encoding="utf-8") as handle:
            self.source = handle.read()

    def test_main_exists_and_parses(self):
        self.assertTrue(os.path.exists(MAIN))
        ast.parse(self.source)

    def test_ctx_created_before_any_use(self):
        self.assertIsNone(find_violation(self.source))

    def test_guard_catches_use_before_creation(self):
        bad = "ctx.v3_probed_matches = set()\nctx = MatchContext()\n"
        self.assertIsNotNone(find_violation(bad))

    def test_guard_allows_use_after_creation(self):
        good = "ctx = MatchContext()\nctx.v3_probed_matches = set()\n"
        self.assertIsNone(find_violation(good))

    def test_guard_ignores_function_and_lambda_bodies(self):
        nested = (
            "def helper():\n"
            "    return ctx.enemy_level_cache\n"
            "other = lambda: ctx.match_player_cache\n"
            "ctx = MatchContext()\n"
        )
        self.assertIsNone(find_violation(nested))

    def test_guard_catches_use_inside_module_level_try(self):
        bad = (
            "try:\n"
            "    ctx.v3_probed_matches = set()\n"
            "except Exception:\n"
            "    pass\n"
            "ctx = MatchContext()\n"
        )
        self.assertIsNotNone(find_violation(bad))

    def test_guard_allows_creation_inside_module_level_try(self):
        good = (
            "try:\n"
            "    ctx = MatchContext()\n"
            "    ctx.v3_probed_matches = set()\n"
            "except Exception:\n"
            "    pass\n"
        )
        self.assertIsNone(find_violation(good))


if __name__ == "__main__":
    unittest.main(verbosity=2)
