"""A routed research event is all-or-nothing; emitted facts cannot be rewritten."""

from dataclasses import replace
from decimal import Decimal

import pytest

from northstar_quant.accounting.fifo import Account
from northstar_quant.messaging import DispatchFailed
from northstar_quant.research.backtesting import run_research
from northstar_quant.research.backtesting import session as module
from northstar_quant.research.backtesting.session import STEP_COMPLETED, TradingSession
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.strategies.registry import resolve
from tests.test_research import dataset


def session(data):
    return TradingSession(
        data.market, ResearchConfig(), snapshot_id=data.snapshot_id, content_hash=data.content_hash
    )


@pytest.mark.parametrize("failure", ["strategy", "risk"])
def test_failed_event_rolls_back_fill_strategy_orders_metrics_then_retries(monkeypatch, failure):
    data = dataset(("100", "110", "111", "112"))
    clean, actual = session(data), session(data)
    seen = []
    actual.bus.subscribe(STEP_COMPLETED, seen.append)
    accepted = []
    for bar in data.bars[:2]:
        clean.advance(bar)
        accepted.append(actual.advance(bar))
    before = actual.checkpoint()
    assert actual.pending is not None

    def fail(*args, **kwargs):
        raise ValueError("injected computation failure")

    with monkeypatch.context() as patch:
        if failure == "risk":
            patch.setattr(module, "evaluate_risk", fail)
        else:
            patch.setattr(resolve(actual.config.strategy.strategy_id), "decide", fail)
        with pytest.raises(ValueError, match="injected"):
            actual.advance(data.bars[2])
    assert actual.checkpoint() == before
    assert len(seen) == 2
    expected = clean.advance(data.bars[2])
    completed = actual.advance(data.bars[2])
    assert completed.fill is not None
    assert completed == expected
    assert actual.checkpoint() == clean.checkpoint()
    assert seen[-1] == completed
    assert actual.advance(data.bars[2]) is None
    assert len(seen) == 3
    account = Account(actual.config.simulation.initial_cash, data.market)
    for step in [*accepted, completed]:
        if step.fill:
            account.apply(step.fill.fact)
    resumed = TradingSession.from_checkpoint(
        data.market,
        actual.config,
        snapshot_id=data.snapshot_id,
        content_hash=data.content_hash,
        checkpoint=actual.checkpoint(),
        account=account,
    )
    assert resumed.advance(data.bars[3]) == clean.advance(data.bars[3])


def test_subscriber_cannot_change_report_and_post_commit_failure_does_not_undo_facts():
    data = dataset(("100", "110", "111"))
    actual = session(data)
    seen = []

    def malicious(step):
        point = step.point
        point["cash"] = "0"
        point["strategy"]["kind"] = "WRONG"

    actual.bus.subscribe(STEP_COMPLETED, malicious)
    actual.bus.subscribe(STEP_COMPLETED, seen.append)
    for bar in data.bars[:2]:
        actual.advance(bar)
    assert seen[-1].point["cash"] != "0"
    assert seen[-1].point["strategy"]["kind"] != "WRONG"

    def fail(step):
        raise ValueError("observer failed after commit")

    actual.bus.subscribe(STEP_COMPLETED, fail)
    with pytest.raises(DispatchFailed):
        actual.advance(data.bars[2])
    assert actual.account.fill_count == 1
    assert len(seen) == 3
    with pytest.raises(RuntimeError, match="faulted"):
        actual.advance(replace(data.bars[2], close=Decimal("112")))
    # The production report collector uses subscriptions on a fresh instance.
    assert run_research(data, actual.config).to_dict()["summary"] == actual.summary()
