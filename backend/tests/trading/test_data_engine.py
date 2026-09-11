"""Stream isolation, causal routing and whole-event rollback of market projections."""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from northstar_quant.market_data.engine import BarStream, DataEngine
from tests.test_research import dataset


def test_streams_route_independently_and_failed_candidates_preserve_all_views():
    data = dataset(("100", "103", "107"))
    first = BarStream(data.market.contract_id, 60, "fixed-first")
    second = BarStream(uuid4(), 60, "broker-second")
    engine = DataEngine().subscribe(first, 2).subscribe(second, 1)
    initial = engine
    engine, frame = engine.advance(first, data.bars[0], at=data.bars[0].available_at)
    assert frame.stream == first and frame.bars == (data.bars[0],)
    assert initial.window(first).bars == ()
    other = replace(data.bars[0], close=Decimal("200"))
    engine, frame = engine.advance(second, other, at=other.available_at)
    assert frame.stream == second and frame.bars == (other,)
    assert engine.window(first).bars == (data.bars[0],)
    assert engine.advance(first, data.bars[0], at=other.available_at) == (engine, None)
    before = engine
    with pytest.raises(ValueError, match="different facts"):
        engine.advance(first, other, at=other.available_at)
    candidate, _ = engine.advance(first, data.bars[1], at=data.bars[1].available_at)
    assert engine == before  # failing strategy/account transaction discards candidate
    engine, _ = candidate.advance(first, data.bars[2], at=data.bars[2].available_at)
    assert engine.window(first).bars == data.bars[1:]
    assert engine.window(second).bars == (other,)
    with pytest.raises(ValueError, match="backwards"):
        engine.advance(second, data.bars[1], at=data.bars[1].available_at)
    removed = engine.unsubscribe(second)
    with pytest.raises(ValueError, match="subscription"):
        removed.advance(second, data.bars[2], at=data.bars[2].available_at)
    assert removed.window(first) == engine.window(first)


def test_fixed_sources_and_intervals_cannot_be_accidentally_combined():
    data = dataset(("100", "103"))
    first = BarStream(data.market.contract_id, 60, "publication-a")
    revised = BarStream(data.market.contract_id, 60, "publication-b")
    engine = DataEngine().subscribe(first, 2, history=(data.bars[0],))
    with pytest.raises(ValueError, match="subscription"):
        engine.advance(revised, data.bars[1], at=data.bars[1].available_at)
    with pytest.raises(ValueError, match="binding"):
        engine.subscribe(first, 3)
    with pytest.raises(ValueError, match="completion"):
        DataEngine().subscribe(BarStream(first.contract_id, 900), 2, history=data.bars)
    engine = engine.subscribe(revised, 1)
    changed = replace(data.bars[0], close=Decimal("99"))
    engine, _ = engine.advance(revised, changed, at=changed.available_at)
    assert engine.window(first).bars[0].close == 100
    assert engine.window(revised).bars[0].close == 99
