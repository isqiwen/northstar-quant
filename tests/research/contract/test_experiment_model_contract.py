"""P2-WP03 实验模型的结构性边界契约。"""

from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENTS_ROOT = PROJECT_ROOT / "src" / "northstar_quant" / "research" / "experiments"


def _tree(name: str) -> ast.AST:
    return ast.parse((EXPERIMENTS_ROOT / name).read_text(encoding="utf-8"))


def _runtime_imports(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_experiment_model_does_not_reach_backtest_validation_application_or_database():
    forbidden_prefixes = (
        "northstar_quant.application",
        "northstar_quant.platform.db",
        "northstar_quant.research.backtest",
        "northstar_quant.research.validation",
        "sqlalchemy",
        "alembic",
        "polars",
    )
    imports = _runtime_imports(_tree("models.py")) | _runtime_imports(_tree("registry.py"))

    assert not [imported for imported in imports if imported.startswith(forbidden_prefixes)], (
        imports
    )


def test_experiment_model_never_reads_clock_or_generates_run_identity():
    forbidden_calls = {
        ("datetime", "now"),
        ("datetime", "utcnow"),
        ("time", "time"),
        ("uuid", "uuid4"),
    }
    calls: set[tuple[str, str]] = set()
    for name in ("models.py", "registry.py"):
        for node in ast.walk(_tree(name)):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if isinstance(node.func.value, ast.Name):
                calls.add((node.func.value.id, node.func.attr))

    assert not calls.intersection(forbidden_calls)
