"""P10-WP04 canonical composer stays inside the structured P3 boundary."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSER = ROOT / "src" / "northstar_quant" / "portfolio_risk" / "portfolio" / "composition.py"
TARGETS = ROOT / "src" / "northstar_quant" / "portfolio_risk" / "portfolio" / "targets.py"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def test_canonical_composer_has_no_application_execution_or_legacy_dataframe_dependency() -> None:
    imports = _imports(COMPOSER)
    forbidden_prefixes = (
        "northstar_quant.application",
        "northstar_quant.research",
        "northstar_quant.trading_execution",
        "northstar_quant.foundation.db",
        "northstar_quant.portfolio_risk.portfolio.multi_strategy",
        "polars",
    )
    assert not any(
        imported == prefix or imported.startswith(prefix + ".")
        for imported in imports
        for prefix in forbidden_prefixes
    )
    assert not _names(COMPOSER).intersection(
        {"ApprovedPortfolioTarget", "ExecutionPlan", "BrokerOrder", "OrderRouter"}
    )
    assert "strict=False" not in COMPOSER.read_text(encoding="utf-8")


def test_portfolio_target_v2_binds_the_canonical_composition_hash() -> None:
    source = TARGETS.read_text(encoding="utf-8")
    assert "northstar.portfolio-target.v2" in source
    assert '"composition_hash": composition_hash' in source
