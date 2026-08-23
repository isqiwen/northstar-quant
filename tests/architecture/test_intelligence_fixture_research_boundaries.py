"""P10-WP03 fixture-only replay remains outside P1, P3, P5, and application."""

from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "src" / "northstar_quant" / "research" / "intelligence_fixture_replay.py"
ACTIVATOR = ROOT / "src" / "northstar_quant" / "application" / "research_strategy_activation.py"
FIXTURE = ROOT / "tests" / "research" / "golden" / "p10_intelligence_fixture_replay_v1.json"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    }


def _walk_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*( _walk_keys(item) for item in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_walk_keys(item) for item in value)) if value else set()
    return set()


def test_fixture_replay_runtime_has_no_p1_p3_p5_or_application_dependency() -> None:
    imports = _imports(MODULE)
    forbidden_prefixes = (
        "northstar_quant.application",
        "northstar_quant.data_platform.market",
        "northstar_quant.data_platform.sources",
        "northstar_quant.portfolio_risk",
        "northstar_quant.trading_execution",
        "tests",
    )
    assert not any(
        imported == prefix or imported.startswith(prefix + ".")
        for imported in imports
        for prefix in forbidden_prefixes
    )
    forbidden_names = {
        "FeatureValue",
        "FeatureLineage",
        "MarketDataSnapshot",
        "DataSourcePublisher",
        "AuthorizedMarketContext",
        "StrategyTarget",
        "PortfolioTarget",
        "ExecutionPlan",
        "BrokerOrder",
    }
    assert not _names(MODULE).intersection(forbidden_names)


def test_companion_fixture_has_no_p1_authorization_or_market_contract_payload() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    keys = _walk_keys(payload)
    forbidden = {
        "publication_authorization_hash",
        "publication_scope",
        "dataset_version_hash",
        "snapshot_id",
        "selected_frame_hash",
        "source_id",
        "adapter_id",
        "contract_id",
        "calendar_id",
        "rule_id",
    }
    assert not keys.intersection(forbidden)
    assert payload["fixture_only"] is True
    assert payload["research_only"] is True
    assert all(value is False for value in payload["authority"].values())


def test_strategy_target_activator_rejects_fixture_replay_before_target_construction() -> None:
    source = ACTIVATOR.read_text(encoding="utf-8")
    assert "ResearchInputEvidenceKind.DATASET_VERSIONED" in source
    assert "fixture-only intelligence replay evidence cannot activate a StrategyTarget" in source
