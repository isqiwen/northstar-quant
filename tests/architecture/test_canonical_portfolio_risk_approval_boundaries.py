"""P10-WP05 keeps canonical portfolio-risk approval inside the P3 boundary."""

from __future__ import annotations

import ast
from dataclasses import fields
import inspect
from pathlib import Path

import northstar_quant.portfolio_risk.portfolio.approval as approval


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "src" / "northstar_quant" / "portfolio_risk" / "portfolio" / "approval.py"


def _imports() -> set[str]:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"), filename=str(MODULE))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _called_attributes() -> set[str]:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"), filename=str(MODULE))
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def test_canonical_portfolio_risk_gate_has_only_p3_dependencies_and_no_legacy_dataframe_path() -> None:
    imports = _imports()
    forbidden_prefixes = (
        "northstar_quant.application",
        "northstar_quant.research",
        "northstar_quant.trading_execution",
        "northstar_quant.foundation.db",
        "northstar_quant.portfolio_risk.portfolio.multi_strategy",
        "northstar_quant.portfolio_risk.portfolio.strategy_pipeline",
        "polars",
    )

    assert not any(
        imported == prefix or imported.startswith(prefix + ".")
        for imported in imports
        for prefix in forbidden_prefixes
    )
    assert "transition" not in _called_attributes()


def test_gate_has_a_small_non_submitting_surface_and_accepts_only_derived_inputs() -> None:
    public_functions = {
        name
        for name, value in vars(approval).items()
        if inspect.isfunction(value)
        and value.__module__ == approval.__name__
        and not name.startswith("_")
    }
    gate_methods = {
        name
        for name, value in inspect.getmembers(
            approval.PortfolioRiskApprovalGate,
            predicate=inspect.isfunction,
        )
        if not name.startswith("_")
    }
    request_fields = {item.name for item in fields(approval.PortfolioRiskReviewRequest)}
    approval_fields = {item.name for item in fields(approval.PortfolioRiskApprovalEvidence)}

    assert public_functions == set()
    assert gate_methods == {"evaluate", "review"}
    assert request_fields == {
        "composition",
        "account_snapshot",
        "instrument_snapshots",
        "risk_state",
        "policy",
        "evaluated_at",
    }
    assert not request_fields.intersection(
        {
            "approved_target",
            "broker_order",
            "execution_plan",
            "exposure",
            "limit_checks",
            "measurements",
            "portfolio_target",
            "receipt",
            "stress_checks",
        }
    )
    assert not approval_fields.intersection(
        {"broker_order", "durable_intent", "execution_plan", "receipt", "submit"}
    )


def test_approval_artifacts_remain_explicitly_non_executable_and_cover_exact_seven_stresses() -> None:
    source = MODULE.read_text(encoding="utf-8")

    assert "len(self.scenario_limits) != len(ScenarioKind)" in source
    assert "set(kinds) != set(ScenarioKind)" in source
    assert "eligible_for_execution" in source
    assert "eligible_for_broker_order" in source
    assert "ExecutionPlan" not in source
    assert "BrokerOrder" not in source
