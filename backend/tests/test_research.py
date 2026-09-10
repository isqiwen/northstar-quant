import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, localcontext
from uuid import UUID

import pytest

from northstar_quant.accounting.fifo import Account, FillFact
from northstar_quant.data_management.research import ResearchDataset
from northstar_quant.factors.evaluation import Binding
from northstar_quant.market_data import Market, MarketBar
from northstar_quant.research.backtesting import run_research
from northstar_quant.research.backtesting.report import build_result
from northstar_quant.research.backtesting.session import TradingSession, TradingStep
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.risk.configuration import RiskConfig
from northstar_quant.simulation.configuration import SimulationConfig
from northstar_quant.strategies.configuration import StrategyConfig

AT = datetime(2026, 1, 5, 1, tzinfo=UTC)


def dataset(prices: tuple[str, ...]) -> ResearchDataset:
    return ResearchDataset(
        UUID(int=100),
        "a" * 64,
        Market(UUID(int=200), "RB2605", "Asia/Shanghai", "CNY", "TON", Decimal(1), Decimal(10), 60),
        tuple(
            MarketBar(
                UUID(int=index + 1),
                AT + timedelta(minutes=index),
                AT + timedelta(minutes=index + 1),
                AT + timedelta(minutes=index + 1),
                date(2026, 1, 5),
                Decimal(price),
                Decimal(100),
            )
            for index, price in enumerate(prices)
        ),
    )


def test_repeated_decisions_use_fills_and_costs_to_close_both_directions() -> None:
    data = dataset(("100", "110", "112", "112", "108", "106", "106"))
    config = ResearchConfig(
        risk=RiskConfig(max_lots=2),
        strategy=StrategyConfig.create(supplied={"threshold": str(Decimal("0.03"))}),
    )
    result = run_research(data, config).to_dict()
    assert result["summary"] == {
        "bar_count": 7,
        "decision_count": 6,
        "fill_count": 4,
        "initial_cash": "100000",
        "ending_cash": "99952",
        "ending_position_lots": 0,
        "realized_pnl": "-40",
        "unrealized_pnl": "0",
        "total_fees": "8",
        "ending_equity": "99952",
        "total_return": "-0.00048",
        "max_drawdown": "48",
        "max_drawdown_fraction": "0.00048",
    }
    assert [
        (item["side"], item["quantity_lots"], item["price"], item["position_lots"])
        for item in result["fills"]
    ] == [
        ("BUY", 1, "113", 1),
        ("SELL", 1, "111", 0),
        ("SELL", 1, "105", -1),
        ("BUY", 1, "107", 0),
    ]
    assert [item["offset"] for item in result["fills"]] == [
        "OPEN",
        "CLOSE_TODAY",
        "OPEN",
        "CLOSE_TODAY",
    ]
    assert result["pending_order"] is None


def test_late_bars_do_not_fill_before_the_price_was_economically_available() -> None:
    data = dataset(("100", "110", "115", "120"))
    bars = list(data.bars)
    bars[1] = replace(bars[1], available_at=AT + timedelta(minutes=5))
    bars[2] = replace(bars[2], available_at=AT + timedelta(minutes=5))
    bars[3] = replace(
        bars[3],
        event_time=AT + timedelta(minutes=5),
        completed_at=AT + timedelta(minutes=6),
        available_at=AT + timedelta(minutes=6, seconds=1),
    )
    result = run_research(
        replace(data, bars=tuple(bars)),
        ResearchConfig(
            risk=RiskConfig(max_lots=2, max_adverse_price_move_fraction=Decimal("0.5")),
            simulation=SimulationConfig(slippage_ticks=0),
            strategy=StrategyConfig.create(supplied={"threshold": str(Decimal("0.01"))}),
        ),
    ).to_dict()
    assert len(result["fills"]) == 1
    assert result["fills"][0]["observation_id"] == str(bars[3].observation_id)
    assert result["fills"][0]["filled_at"] == bars[3].available_at.isoformat()


def test_risk_rejection_and_incremental_retries_are_observable_and_deterministic() -> None:
    data = dataset(("100", "110", "120", "120"))
    config = ResearchConfig(risk=RiskConfig(max_gross_notional=Decimal(1)))
    batch = run_research(data, config).to_dict()
    session = TradingSession(
        data.market, config, snapshot_id=data.snapshot_id, content_hash=data.content_hash
    )
    steps = []
    for bar in data.bars:
        step = session.advance(bar)
        assert step is not None
        steps.append(step)
        assert session.advance(bar) is None
    assert build_result(session, steps).to_dict() == batch
    assert batch["fills"] == []
    assert batch["decisions"][0]["reason"] == "NO_PERMITTED_POSITION"
    with pytest.raises(ValueError, match="reused"):
        session.advance(replace(data.bars[-1], close=Decimal(121)))
    assert build_result(session, steps).to_dict() == batch
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        assert run_research(data, config).to_dict() == batch
    with pytest.raises(ValueError, match="completion"):
        run_research(
            replace(data, bars=(replace(data.bars[0], available_at=AT), *data.bars[1:])), config
        )


def test_configuration_is_complete_and_changed_strategy_changes_result_identity() -> None:
    data = dataset(("100", "110", "120"))
    config = ResearchConfig.from_mapping(
        {"strategy": {"parameters": {"threshold": "0.1"}}, "risk": {"max_lots": 2}}
    )
    assert ResearchConfig.from_mapping(config.to_dict()) == config
    first = run_research(data, config).to_dict()
    second = run_research(
        data, replace(config, strategy=StrategyConfig.create(supplied={"threshold": "0.01"}))
    ).to_dict()
    assert first["result_hash"] != second["result_hash"]
    assert first["fills"] == []
    assert len(second["fills"]) == 1


def test_report_rejects_altered_fill_even_when_history_counts_match() -> None:
    data = dataset(("100", "110", "120", "120"))
    session = TradingSession(
        data.market, ResearchConfig(), snapshot_id=data.snapshot_id, content_hash=data.content_hash
    )
    try:
        steps = [session.advance(bar) for bar in data.bars]
        assert all(step is not None for step in steps)
        committed = [step for step in steps if step is not None]
        original = build_result(session, committed).to_dict()
        index = next(i for i, step in enumerate(committed) if step.fill is not None)
        fill = committed[index].fill
        assert fill is not None
        altered = list(committed)
        document = committed[index].to_dict()
        document["fill"] = replace(fill, cash=fill.cash + Decimal(1)).to_dict()
        altered[index] = TradingStep.from_dict(document)
        with pytest.raises(ValueError, match="complete committed"):
            build_result(session, altered)
        assert build_result(session, committed).to_dict() == original
    finally:
        session.close()


def test_checkpoint_recovery_preserves_pending_fifo_warmup_and_complete_result() -> None:
    data = dataset(("100", "110", "112", "112", "108", "106", "106", "112"))
    config = ResearchConfig(
        risk=RiskConfig(max_lots=4),
        strategy=StrategyConfig.create(
            supplied={"threshold": str(Decimal("0.03"))},
            factors={"momentum": Binding.create("trend.return", {"window_bars": 2})},
        ),
    )
    batch = run_research(data, config).to_dict()
    session = TradingSession(
        data.market, config, snapshot_id=data.snapshot_id, content_hash=data.content_hash
    )
    assert session.summary()["bar_count"] == 0
    assert session.summary()["ending_equity"] == "100000"
    checkpoint = json.loads(json.dumps(session.checkpoint()))
    ledger = []
    steps = []
    for bar in data.bars:
        rebuilt = Account(config.simulation.initial_cash, data.market)
        for fill in ledger:
            assert rebuilt.apply(FillFact.from_dict(fill)).to_dict() == fill
        session = TradingSession.from_checkpoint(
            data.market,
            config,
            snapshot_id=data.snapshot_id,
            content_hash=data.content_hash,
            checkpoint=checkpoint,
            account=rebuilt,
        )
        step = session.advance(bar)
        assert step is not None
        restored_step = TradingStep.from_dict(json.loads(json.dumps(step.to_dict())))
        assert restored_step == step
        steps.append(restored_step)
        if step.fill is not None:
            ledger.append(step.fill.to_dict())
        checkpoint = json.loads(json.dumps(session.checkpoint()))
        assert len(checkpoint["history"]) <= (config.strategy.history_bars - 1) + 1
        assert "decisions" not in checkpoint and "equity_curve" not in checkpoint
        assert session.advance(bar) is None
    assert build_result(session, steps).to_dict() == batch
    assert session.summary() == batch["summary"]
    with pytest.raises(ValueError, match="counters"):
        TradingSession.from_checkpoint(
            data.market,
            config,
            snapshot_id=data.snapshot_id,
            content_hash=data.content_hash,
            checkpoint={**checkpoint, "decision_count": checkpoint["bar_count"] + 1},
            account=rebuilt,
        )
    with pytest.raises(ValueError, match="drawdown"):
        TradingSession.from_checkpoint(
            data.market,
            config,
            snapshot_id=data.snapshot_id,
            content_hash=data.content_hash,
            checkpoint={**checkpoint, "maximum_drawdown": "0"},
            account=rebuilt,
        )
    rebuilt.cash += Decimal(1)
    with pytest.raises(ValueError, match="verified fill ledger"):
        TradingSession.from_checkpoint(
            data.market,
            config,
            snapshot_id=data.snapshot_id,
            content_hash=data.content_hash,
            checkpoint=checkpoint,
            account=rebuilt,
        )
    changed_plan = {
        **checkpoint,
        "evaluation_plan": {**checkpoint["evaluation_plan"], "sample_use": "OUT_OF_SAMPLE"},
    }
    with pytest.raises(ValueError, match="fixed input or configuration"):
        TradingSession.from_checkpoint(
            data.market,
            config,
            snapshot_id=data.snapshot_id,
            content_hash=data.content_hash,
            checkpoint=changed_plan,
            account=rebuilt,
        )
    with pytest.raises(ValueError, match="fixed input or configuration"):
        TradingSession.from_checkpoint(
            data.market,
            replace(config, risk=replace(config.risk, max_lots=5)),
            snapshot_id=data.snapshot_id,
            content_hash=data.content_hash,
            checkpoint=checkpoint,
            account=rebuilt,
        )


def test_overlapping_market_interval_cannot_fill_a_pending_order() -> None:
    data = dataset(("100", "110", "112"))
    session = TradingSession(
        data.market, ResearchConfig(), snapshot_id=data.snapshot_id, content_hash=data.content_hash
    )
    session.advance(data.bars[0])
    session.advance(data.bars[1])
    assert session.pending is not None
    before = session.checkpoint()
    overlap = replace(
        data.bars[2],
        event_time=data.bars[2].event_time - timedelta(seconds=30),
        completed_at=data.bars[2].completed_at - timedelta(seconds=30),
    )
    with pytest.raises(ValueError, match="overlapping"):
        session.advance(overlap)
    assert session.checkpoint() == before
    # The rejected input did not consume the observation or the pending order.
    step = session.advance(data.bars[2])
    assert step is not None and step.fill is not None
    session.close()


def test_partial_execution_and_target_replacement_preserve_order_quantities_and_reasons() -> None:
    data = dataset(("100", "110", "120", "120", "100"))
    data = replace(data, bars=tuple(replace(bar, volume=Decimal(10)) for bar in data.bars))
    config = ResearchConfig(
        risk=RiskConfig(max_lots=100), simulation=SimulationConfig(slippage_ticks=0)
    )
    report = run_research(data, config).to_dict()
    assert report["fills"]
    assert all(item["quantity_lots"] == 1 for item in report["fills"])
    partial = [item for item in report["orders"] if item["status"] == "PARTIALLY_FILLED"]
    canceled = [item for item in report["orders"] if item["status"] == "CANCELED"]
    assert partial and canceled
    assert {item["order_id"] for item in canceled} <= {item["order_id"] for item in partial}
    assert any(item["reason"] == "AUTHORIZATION_RETAINED" for item in report["orders"])
    for update in report["orders"]:
        assert update["filled_lots"] + update["remaining_lots"] == update["quantity_lots"]
    assert all(item["reason"] == "TARGET_REPLACED" for item in canceled)
    # A partial fill consumes only its slice; the residual hold is not booked
    # as a fee. Replacement cancels it before reserving the new close order.
    curve = report["equity_curve"]
    assert Decimal(curve[1]["reserved_fee"]) > Decimal(curve[2]["reserved_fee"]) > 0
    assert Decimal(curve[1]["reserved_margin"]) > Decimal(curve[2]["reserved_margin"]) > 0
    assert curve[-1]["reserved_margin"] == "0"
    assert curve[-1]["reserved_close_lots"] == report["summary"]["ending_position_lots"]
    assert all("available_after_reservations" not in point for point in curve)
    empty = replace(data, bars=tuple(replace(bar, volume=Decimal(0)) for bar in data.bars))
    unfilled = run_research(empty, config).to_dict()
    assert unfilled["fills"] == []
    assert any(item["reason"] == "NO_EXECUTABLE_VOLUME" for item in unfilled["orders"])
    assert unfilled["summary"]["total_fees"] == "0"
    assert unfilled["summary"]["ending_equity"] == "100000"
    assert any(Decimal(point["reserved_fee"]) > 0 for point in unfilled["equity_curve"])


def test_a_shorter_risk_window_replaces_the_old_working_order(monkeypatch) -> None:
    import northstar_quant.research.backtesting.session as loop

    data = dataset(("100", "110", "120"))
    data = replace(data, bars=tuple(replace(bar, volume=Decimal(0)) for bar in data.bars))
    session = TradingSession(
        data.market,
        ResearchConfig(risk=RiskConfig(max_lots=100)),
        snapshot_id=data.snapshot_id,
        content_hash=data.content_hash,
    )
    session.advance(data.bars[0])
    session.advance(data.bars[1])
    original = session.pending
    assert original is not None
    new_expiry = data.bars[2].available_at + timedelta(seconds=1)
    assert new_expiry < original.expires_at
    evaluate = loop.evaluate_risk

    def shortened(*args, **kwargs):
        result = evaluate(*args, **kwargs)
        return replace(
            result,
            side=original.side,
            quantity_lots=original.remaining_lots,
            minimum_fill_price=original.minimum_fill_price,
            maximum_fill_price=original.maximum_fill_price,
            expires_at=new_expiry,
        )

    monkeypatch.setattr(loop, "evaluate_risk", shortened)
    step = session.advance(data.bars[2])
    assert step is not None and step.fill is None
    assert any(
        update.order.order_id == original.order_id and update.status.value == "CANCELED"
        for update in step.orders
    )
    assert session.pending is not None and session.pending.expires_at == new_expiry
    assert session.pending.order_id != original.order_id
    assert session.account.fill_count == 0
    session.close()


def test_report_rejects_changed_intermediate_valuation_with_unchanged_ledger() -> None:
    data = dataset(("100", "110", "112", "112", "108", "106", "106"))
    session = TradingSession(
        data.market,
        ResearchConfig(risk=RiskConfig(max_lots=2)),
        snapshot_id=data.snapshot_id,
        content_hash=data.content_hash,
    )
    steps = [session.advance(bar) for bar in data.bars]
    assert all(step is not None for step in steps)
    original = build_result(session, steps).to_dict()
    for field, changed in (
        ("cash", "100001"),
        ("equity", "999999"),
        ("total_fees", "999"),
        ("position_lots", 9),
        ("drawdown_fraction", "0.9"),
        ("margin_used", "0"),
        ("reserved_fee", "999"),
        ("reserved_margin", "999"),
        ("reserved_close_lots", 999),
    ):
        document = steps[2].to_dict()
        document["point"][field] = changed
        corrupted = list(steps)
        corrupted[2] = TradingStep.from_dict(document)
        with pytest.raises(ValueError, match="report (valuation|margin)"):
            build_result(session, corrupted)
    assert build_result(session, steps).to_dict() == original
    session.close()


def test_report_rejects_missing_submission_and_repeated_terminal_order() -> None:
    from northstar_quant.execution.orders import OrderStatus

    data = dataset(("100", "110", "112", "112", "108"))
    session = TradingSession(
        data.market,
        ResearchConfig(risk=RiskConfig(max_lots=2)),
        snapshot_id=data.snapshot_id,
        content_hash=data.content_hash,
    )
    steps = [session.advance(bar) for bar in data.bars]
    original = build_result(session, steps)
    submission = next(i for i, step in enumerate(steps) if step.orders)
    corrupted = list(steps)
    document = steps[submission].to_dict()
    document["orders"] = []
    corrupted[submission] = TradingStep.from_dict(document)
    with pytest.raises(ValueError, match="execution history"):
        build_result(session, corrupted)
    terminal = next(
        i
        for i, step in enumerate(steps)
        if any(update.status is OrderStatus.FILLED for update in step.orders)
    )
    corrupted = list(steps)
    document = steps[terminal].to_dict()
    final = next(update for update in document["orders"] if update["status"] == "FILLED")
    document["orders"].append(final)
    corrupted[terminal] = TradingStep.from_dict(document)
    with pytest.raises(ValueError, match="terminal state"):
        build_result(session, corrupted)
    assert build_result(session, steps) == original
    session.close()
