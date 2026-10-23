"""Map a weakness id (V1..V7) to the regression test that proves it closed.

Convention: tests/test_regressions.py::test_<vuln id, lowercased>_<slug>.
"""
from __future__ import annotations

import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGRESSIONS = "tests/test_regressions.py"


def regression_tests() -> list[str]:
    with open(os.path.join(ROOT, REGRESSIONS)) as f:
        tree = ast.parse(f.read())
    return [f"{REGRESSIONS}::{n.name}" for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]


def regression_test_for(vuln_id: str) -> str:
    prefix = f"{REGRESSIONS}::test_{vuln_id.lower()}_"
    return next((t for t in regression_tests() if t.startswith(prefix)), "")
