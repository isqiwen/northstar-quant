"""P2-WP05：逐决策 target replay 不得悄悄接入回测、准入或交易。"""

from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
TARGET_MODEL = (
    PROJECT_ROOT
    / "src"
    / "northstar_quant"
    / "research"
    / "validation"
    / "decision_replay.py"
)
COMPOSITION_ROOT = (
    PROJECT_ROOT
    / "src"
    / "northstar_quant"
    / "application"
    / "decision_replay_backtest.py"
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def _attribute_calls(path: Path) -> set[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
        ):
            calls.add((node.func.value.id, node.func.attr))
    return calls


def test_target_trace_model_has_no_application_database_or_trading_dependency() -> None:
    forbidden_prefixes = (
        "northstar_quant.application",
        "northstar_quant.platform.db",
        "northstar_quant.trading_execution",
        "sqlalchemy",
        "alembic",
    )
    imports = _imports(TARGET_MODEL)
    assert not [item for item in imports if item.startswith(forbidden_prefixes)], imports


def test_composition_root_does_not_call_backtest_admission_or_any_execution_path() -> None:
    forbidden_prefixes = (
        "northstar_quant.platform.db",
        "northstar_quant.trading_execution",
        "northstar_quant.application.live_service",
        "northstar_quant.application.scheduler",
        "northstar_quant.application.target_service",
    )
    imports = _imports(COMPOSITION_ROOT)
    assert not [item for item in imports if item.startswith(forbidden_prefixes)], imports
    source = COMPOSITION_ROOT.read_text(encoding="utf-8")
    forbidden_names = (
        "run_target_backtest",
        "evaluate_research_admission",
        "run_profile_backtest",
    )
    assert not [name for name in forbidden_names if name in source]
    assert "LookaheadGuard" in source
    assert "candidate_admission_eligible" in source


def test_target_replay_never_reads_current_clock_or_generates_random_identity() -> None:
    forbidden_calls = {
        ("datetime", "now"),
        ("datetime", "utcnow"),
        ("time", "time"),
        ("uuid", "uuid4"),
    }
    calls = _attribute_calls(TARGET_MODEL) | _attribute_calls(COMPOSITION_ROOT)
    assert not calls.intersection(forbidden_calls)
