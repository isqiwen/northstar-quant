"""P10 fixture-only intelligence corpus must remain outside execution semantics."""

from __future__ import annotations

from tests.helpers.paths import PROJECT_ROOT


def test_impact_graph_and_fixture_crosswalk_cannot_import_broker_or_trading_contracts() -> None:
    impact_graph = (
        PROJECT_ROOT
        / "src"
        / "northstar_quant"
        / "intelligence"
        / "impact_graph"
        / "graph.py"
    ).read_text(encoding="utf-8")
    fixture_loader = (
        PROJECT_ROOT / "tests" / "intelligence" / "golden" / "_fixture_corpus.py"
    ).read_text(encoding="utf-8")

    for source in (impact_graph, fixture_loader):
        assert "northstar_quant.trading_execution" not in source
        assert "ctp_contract_mapping" not in source
        assert "BrokerOrder" not in source
        assert "ExecutionPlan" not in source

    assert "AuthorizedMarketContext" not in fixture_loader
    assert "EventEvidenceAvailability" not in fixture_loader
    assert "FeatureLineage(" not in fixture_loader
    assert "FeatureValue(" not in fixture_loader
