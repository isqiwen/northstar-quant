from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from northstar_quant.intelligence.context import MarketContextError, MarketContextSnapshot, context_as_of
from northstar_quant.intelligence.ontology import load_ontology


def _snapshot(*, available_at: datetime | None = None) -> MarketContextSnapshot:
    as_of = datetime(2026, 8, 22, 9, tzinfo=UTC)
    return MarketContextSnapshot("context-1", "copper", "shfe", "dataset-1", as_of, available_at or as_of, 120.0, -0.1, 5.0, 0.25, 0.18, 100.0, 7.2, "slowdown", "Q3")


def test_market_context_contains_all_required_observations_and_is_pit_available():
    snapshot = _snapshot()
    result = context_as_of(snapshot=snapshot, ontology=load_ontology(Path("ontology")), simulation_time=snapshot.as_of)
    assert result.inventory == 120.0
    assert result.macro_regime == "slowdown"
    assert result.is_available_at(snapshot.as_of)


def test_market_context_fails_closed_for_future_or_unmodelled_observations():
    snapshot = _snapshot(available_at=datetime(2026, 8, 22, 10, tzinfo=UTC))
    with pytest.raises(MarketContextError, match="not yet available"):
        context_as_of(snapshot=snapshot, ontology=load_ontology(Path("ontology")), simulation_time=snapshot.as_of)
    with pytest.raises(MarketContextError, match="finite"):
        MarketContextSnapshot("context-2", "copper", "shfe", "dataset-1", snapshot.as_of, snapshot.as_of, float("nan"), -0.1, 5.0, 0.25, 0.18, 100.0, 7.2, "slowdown", "Q3")
    with pytest.raises(MarketContextError, match="present in ontology"):
        context_as_of(snapshot=MarketContextSnapshot("context-3", "lithium", "shfe", "dataset-1", snapshot.as_of, snapshot.as_of + timedelta(minutes=1), 1.0, 0.0, 0.0, 0.0, 0.1, 100.0, 7.2, "normal", "Q3"), ontology=load_ontology(Path("ontology")), simulation_time=snapshot.as_of + timedelta(minutes=1))
