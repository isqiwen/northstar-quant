"""Causal replay and failure isolation for the shared strategy runtime."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from northstar_quant.strategies import runtime as module
from northstar_quant.strategies.configuration import StrategyConfig
from northstar_quant.strategies.runtime import StrategyRuntime
from tests.test_research import dataset


def test_restore_and_retries_preserve_the_next_decision_without_replaying_effects():
    data = dataset(("100", "103", "107", "101"))
    config = StrategyConfig.create(supplied={"threshold": "0.01"})
    runtime = StrategyRuntime(config, data.market.contract_id)
    for bar in data.bars[:3]:
        runtime.advance(bar)
    saved = runtime.history, runtime.state
    assert runtime.advance(data.bars[2]) is None
    assert (runtime.history, runtime.state) == saved
    resumed = StrategyRuntime(config, data.market.contract_id, history=saved[0], state=saved[1])
    assert resumed.advance(data.bars[3]) == runtime.advance(data.bars[3])
    assert (resumed.history, resumed.state) == (runtime.history, runtime.state)
    assert saved[0] != runtime.history


def test_revised_late_and_failed_inputs_cannot_advance_strategy_state(monkeypatch):
    data = dataset(("100", "110", "120"))
    runtime = StrategyRuntime(StrategyConfig.create(), data.market.contract_id)
    runtime.advance(data.bars[0])
    runtime.advance(data.bars[1])
    saved = runtime.history, runtime.state
    with pytest.raises(ValueError, match="different facts"):
        runtime.advance(replace(data.bars[1], close=Decimal("111")))
    with pytest.raises(ValueError, match="late or revised"):
        runtime.advance(replace(data.bars[0], observation_id=data.bars[2].observation_id))
    with pytest.raises(ValueError, match="clock precedes"):
        runtime.advance(data.bars[2], at=data.bars[2].available_at - timedelta(seconds=1))

    def failed_strategy(*args, **kwargs):
        raise RuntimeError("synthetic strategy failure")

    monkeypatch.setattr(module, "step", failed_strategy)
    with pytest.raises(RuntimeError, match="synthetic strategy failure"):
        runtime.advance(data.bars[2])
    assert (runtime.history, runtime.state) == saved


def test_restore_refuses_duplicate_or_overlong_warmup():
    data = dataset(("100", "110", "120"))
    config = StrategyConfig.create()
    for history in ((data.bars[0], data.bars[0]), data.bars):
        with pytest.raises(ValueError):
            StrategyRuntime(config, data.market.contract_id, history=history)


@pytest.mark.parametrize("invalid", ["overlap", "trading_day"])
def test_invalid_time_sequence_never_reaches_strategy_and_cannot_restore(monkeypatch, invalid):
    data = dataset(("100", "103"))
    first, second = data.bars
    if invalid == "overlap":
        second = replace(
            second,
            event_time=second.event_time - timedelta(seconds=30),
            completed_at=second.completed_at - timedelta(seconds=30),
        )
        reason = "overlapping"
    else:
        second = replace(second, trading_day=first.trading_day - timedelta(days=1))
        reason = "decreasing trading days"
    config = StrategyConfig.create()
    runtime = StrategyRuntime(config, data.market.contract_id)
    runtime.advance(first)
    saved = runtime.history, runtime.state
    called = []
    original = module.step

    def observed(*args, **kwargs):
        called.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "step", observed)
    with pytest.raises(ValueError, match=reason):
        runtime.advance(second)
    assert (runtime.history, runtime.state) == saved
    assert called == []
    with pytest.raises(ValueError, match=reason):
        StrategyRuntime(config, data.market.contract_id, history=(first, second))


def test_explicit_weekend_trading_day_and_session_gap_preserve_only_received_bars():
    from datetime import UTC, date, datetime

    data = dataset(("100", "103"))
    bars = tuple(
        replace(
            bar,
            event_time=at,
            completed_at=at + timedelta(minutes=1),
            available_at=at + timedelta(minutes=1),
            trading_day=date(2026, 9, 14),
        )
        for bar, at in zip(
            data.bars,
            (datetime(2026, 9, 11, 13, tzinfo=UTC), datetime(2026, 9, 14, 1, tzinfo=UTC)),
            strict=True,
        )
    )
    runtime = StrategyRuntime(StrategyConfig.create(), data.market.contract_id)
    for bar in bars:
        runtime.advance(bar)
    assert runtime.history == bars
    restored = StrategyRuntime(
        StrategyConfig.create(),
        data.market.contract_id,
        history=runtime.history,
        state=runtime.state,
    )
    assert restored.history == bars
