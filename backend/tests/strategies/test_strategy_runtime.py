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
