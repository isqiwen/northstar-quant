"""Causal replay and failure isolation for the shared strategy runtime."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from northstar_quant.market_data.engine import BarStream
from northstar_quant.strategies import trader as module
from northstar_quant.strategies.configuration import StrategyConfig
from northstar_quant.strategies.trader import StrategyBinding, Trader
from tests.test_research import dataset


def test_restore_and_retries_preserve_the_next_decision_without_replaying_effects():
    data = dataset(("100", "103", "107", "101"))
    config = StrategyConfig.create(supplied={"threshold": "0.01"})
    runtime = Trader((StrategyBinding("main", config, BarStream(data.market.contract_id, 60)),))
    for bar in data.bars[:3]:
        runtime.advance(BarStream(data.market.contract_id, 60), bar)
    saved = runtime.history("main"), runtime.state("main")
    assert runtime.advance(BarStream(data.market.contract_id, 60), data.bars[2]) == {}
    assert (runtime.history("main"), runtime.state("main")) == saved
    resumed = Trader(
        (StrategyBinding("main", config, BarStream(data.market.contract_id, 60)),),
        history={BarStream(data.market.contract_id, 60): saved[0]},
        states={"main": saved[1]},
    )
    assert resumed.advance(BarStream(data.market.contract_id, 60), data.bars[3]) == runtime.advance(
        BarStream(data.market.contract_id, 60), data.bars[3]
    )
    assert (resumed.history("main"), resumed.state("main")) == (
        runtime.history("main"),
        runtime.state("main"),
    )
    assert saved[0] != runtime.history("main")


def test_revised_late_and_failed_inputs_cannot_advance_strategy_state(monkeypatch):
    data = dataset(("100", "110", "120"))
    runtime = Trader(
        (StrategyBinding("main", StrategyConfig.create(), BarStream(data.market.contract_id, 60)),)
    )
    runtime.advance(BarStream(data.market.contract_id, 60), data.bars[0])
    runtime.advance(BarStream(data.market.contract_id, 60), data.bars[1])
    saved = runtime.history("main"), runtime.state("main")
    with pytest.raises(ValueError, match="different facts"):
        runtime.advance(
            BarStream(data.market.contract_id, 60), replace(data.bars[1], close=Decimal("111"))
        )
    with pytest.raises(ValueError, match="late or revised"):
        runtime.advance(
            BarStream(data.market.contract_id, 60),
            replace(data.bars[0], observation_id=data.bars[2].observation_id),
        )
    with pytest.raises(ValueError, match="clock precedes"):
        runtime.advance(
            BarStream(data.market.contract_id, 60),
            data.bars[2],
            at=data.bars[2].available_at - timedelta(seconds=1),
        )

    def failed_strategy(*args, **kwargs):
        raise RuntimeError("synthetic strategy failure")

    monkeypatch.setattr(module, "step", failed_strategy)
    with pytest.raises(RuntimeError, match="synthetic strategy failure"):
        runtime.advance(BarStream(data.market.contract_id, 60), data.bars[2])
    assert (runtime.history("main"), runtime.state("main")) == saved


def test_restore_refuses_duplicate_or_overlong_warmup():
    data = dataset(("100", "110", "120"))
    config = StrategyConfig.create()
    for history in ((data.bars[0], data.bars[0]), data.bars):
        with pytest.raises(ValueError):
            Trader(
                (StrategyBinding("main", config, BarStream(data.market.contract_id, 60)),),
                history={BarStream(data.market.contract_id, 60): history},
            )


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
    runtime = Trader((StrategyBinding("main", config, BarStream(data.market.contract_id, 60)),))
    runtime.advance(BarStream(data.market.contract_id, 60), first)
    saved = runtime.history("main"), runtime.state("main")
    called = []
    original = module.step

    def observed(*args, **kwargs):
        called.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "step", observed)
    with pytest.raises(ValueError, match=reason):
        runtime.advance(BarStream(data.market.contract_id, 60), second)
    assert (runtime.history("main"), runtime.state("main")) == saved
    assert called == []
    with pytest.raises(ValueError, match=reason):
        Trader(
            (StrategyBinding("main", config, BarStream(data.market.contract_id, 60)),),
            history={BarStream(data.market.contract_id, 60): (first, second)},
        )


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
    runtime = Trader(
        (StrategyBinding("main", StrategyConfig.create(), BarStream(data.market.contract_id, 60)),)
    )
    for bar in bars:
        runtime.advance(BarStream(data.market.contract_id, 60), bar)
    assert runtime.history("main") == bars
    restored = Trader(
        (StrategyBinding("main", StrategyConfig.create(), BarStream(data.market.contract_id, 60)),),
        history={BarStream(data.market.contract_id, 60): runtime.history("main")},
        states={"main": runtime.state("main")},
    )
    assert restored.history("main") == bars


def test_multiple_instances_share_input_but_never_commit_partial_fanout(monkeypatch):
    data = dataset(("100", "110", "120"))
    stream = BarStream(data.market.contract_id, 60)
    config = StrategyConfig.create()
    bindings = (StrategyBinding("b", config, stream), StrategyBinding("a", config, stream))
    trader = Trader(bindings)
    trader.advance(stream, data.bars[0])
    before = {k: (trader.history(k), trader.state(k)) for k in ("a", "b")}
    original = module.step
    calls = []

    def second_failure(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("second instance fails")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "step", second_failure)
    with pytest.raises(RuntimeError, match="second instance"):
        trader.advance(stream, data.bars[1])
    assert before == {k: (trader.history(k), trader.state(k)) for k in ("a", "b")}
    monkeypatch.setattr(module, "step", original)
    results = trader.advance(stream, data.bars[1])
    assert list(results) == ["a", "b"]
    assert results["a"].intent.target_fraction == results["b"].intent.target_fraction
    assert results["a"].intent.intent_id != results["b"].intent.intent_id
    assert dict(results["a"].intent.evidence)["strategy_binding"] == bindings[1].binding_id
    assert trader.advance(stream, data.bars[1]) == {}
    restored = Trader(
        bindings,
        history={stream: trader.history("a")},
        states={k: trader.state(k) for k in ("a", "b")},
    )
    assert restored.advance(stream, data.bars[2]) == trader.advance(stream, data.bars[2])


def test_multiple_streams_route_only_to_their_bound_instances():
    from uuid import UUID

    data = dataset(("100", "110"))
    first = BarStream(data.market.contract_id, 60)
    second = BarStream(UUID(int=999), 60)
    config = StrategyConfig.create()
    trader = Trader(
        (StrategyBinding("first", config, first), StrategyBinding("second", config, second))
    )
    assert list(trader.advance(first, data.bars[0])) == ["first"]
    assert trader.history("second") == ()
    assert list(trader.advance(second, data.bars[1])) == ["second"]
    with pytest.raises(ValueError, match="backwards"):
        trader.advance(
            first,
            replace(
                data.bars[0],
                observation_id=UUID(int=9999),
                event_time=data.bars[0].completed_at,
                completed_at=data.bars[1].completed_at,
                available_at=data.bars[1].available_at,
            ),
            at=data.bars[0].available_at,
        )
    assert trader.history("first") == (data.bars[0],)


def test_reentrant_cross_thread_and_closed_ingress_do_not_run_strategies(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    data = dataset(("100", "110"))
    stream = BarStream(data.market.contract_id, 60)
    trader = Trader((StrategyBinding("main", StrategyConfig.create(), stream),))
    original = module.step

    def recurse(*args, **kwargs):
        trader.advance(stream, data.bars[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "step", recurse)
    with pytest.raises(RuntimeError, match="reentrant"):
        trader.advance(stream, data.bars[0])
    assert trader.history("main") == ()
    with ThreadPoolExecutor(1) as pool:
        with pytest.raises(RuntimeError, match="core thread"):
            pool.submit(trader.advance, stream, data.bars[0]).result()
    monkeypatch.setattr(module, "step", original)
    trader.advance(stream, data.bars[0])
    trader.close()
    trader.close()
    with pytest.raises(RuntimeError, match="closed"):
        trader.advance(stream, data.bars[1])
