from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from northstar_quant.intelligence.analogue import AnalogueError, EventProfile, rank_analogues
from northstar_quant.intelligence.ontology import load_ontology


def _profile(event_id: str, *, severity: int = 3, commodity: str = "copper", available_at: datetime | None = None) -> EventProfile:
    event_time = datetime(2026, 8, 20, 9, tzinfo=UTC)
    return EventProfile(event_id, "SUPPLY", severity, "Chile", commodity, "draw", "strong", "high", "backwardation", event_time, available_at or event_time)


def test_analogue_engine_uses_all_required_structured_dimensions_before_optional_embedding():
    reference = _profile("event-1")
    close = _profile("event-2")
    different = _profile("event-3", severity=1, commodity="gold")
    matches = rank_analogues(reference=reference, candidates=(different, close), ontology=load_ontology(Path("ontology")), simulation_time=reference.event_time, embedding_similarities={"event-3": 1.0, "event-2": 0.0})
    assert matches[0].analogue_event_id == "event-2"
    assert matches[0].structured_similarity == 1.0
    assert matches[1].structured_similarity < matches[0].structured_similarity


def test_analogue_engine_rejects_future_or_unmodelled_profiles():
    reference = _profile("event-1")
    with pytest.raises(AnalogueError, match="available"):
        rank_analogues(reference=reference, candidates=(_profile("event-2", available_at=reference.event_time + timedelta(minutes=1)),), ontology=load_ontology(Path("ontology")), simulation_time=reference.event_time)
    with pytest.raises(AnalogueError, match="ontology"):
        rank_analogues(reference=reference, candidates=(_profile("event-3", commodity="lithium"),), ontology=load_ontology(Path("ontology")), simulation_time=reference.event_time)
