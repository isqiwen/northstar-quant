import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, localcontext
from uuid import UUID

import pytest

from northstar_quant.accounting import Account, FillFact
from northstar_quant.data.research import Market, ResearchBar, ResearchDataset
from northstar_quant.research import ResearchConfig, TradingSession, TradingStep, run_research

AT = datetime(2026, 1, 5, 1, tzinfo=UTC)


def dataset(prices: tuple[str, ...]) -> ResearchDataset:
    return ResearchDataset(
        UUID(int=100),
        "a" * 64,
        Market(UUID(int=200), "RB2605", "Asia/Shanghai", "CNY", "TON", Decimal(1), Decimal(10), 60),
        tuple(
            ResearchBar(
                UUID(int=index + 1),
                AT + timedelta(minutes=index),
                AT + timedelta(minutes=index + 1),
                AT + timedelta(minutes=index + 1, seconds=1),
                date(2026, 1, 5),
                Decimal(price),
                Decimal(100),
            )
            for index, price in enumerate(prices)
        ),
    )


def test_repeated_decisions_use_fills_and_costs_to_close_both_directions() -> None:
    data = dataset(("100", "110", "112", "112", "108", "106", "106"))
    config = ResearchConfig(threshold=Decimal("0.03"), max_lots=2)
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
            threshold=Decimal("0.01"),
            max_lots=2,
            slippage_ticks=0,
            max_adverse_price_move_fraction=Decimal("0.5"),
        ),
    ).to_dict()
    assert len(result["fills"]) == 1
    assert result["fills"][0]["observation_id"] == str(bars[3].observation_id)
    assert result["fills"][0]["filled_at"] == bars[3].available_at.isoformat()


def test_risk_rejection_and_incremental_retries_are_observable_and_deterministic() -> None:
    data = dataset(("100", "110", "120", "120"))
    config = ResearchConfig(max_gross_notional=Decimal(1))
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
    assert session.result(steps).to_dict() == batch
    assert batch["fills"] == []
    assert batch["decisions"][0]["reason"] == "NO_PERMITTED_POSITION"
    with pytest.raises(ValueError, match="reused"):
        session.advance(replace(data.bars[-1], close=Decimal(121)))
    assert session.result(steps).to_dict() == batch
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
    config = ResearchConfig.from_mapping({"threshold": "0.1", "max_lots": 2})
    assert ResearchConfig.from_mapping(config.to_dict()) == config
    first = run_research(data, config).to_dict()
    second = run_research(data, replace(config, threshold=Decimal("0.01"))).to_dict()
    assert first["result_hash"] != second["result_hash"]
    assert first["fills"] == []
    assert len(second["fills"]) == 1


def test_checkpoint_recovery_preserves_pending_fifo_warmup_and_complete_result() -> None:
    data = dataset(("100", "110", "112", "112", "108", "106", "106", "112"))
    config = ResearchConfig(lookback=2, threshold=Decimal("0.03"), max_lots=4)
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
        rebuilt = Account(config.initial_cash, data.market)
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
        assert len(checkpoint["history"]) <= config.lookback + 1
        assert "decisions" not in checkpoint and "equity_curve" not in checkpoint
        assert session.advance(bar) is None
    assert session.result(steps).to_dict() == batch
    assert session.summary() == batch["summary"]
    with pytest.raises(ValueError, match="counters"):
        TradingSession.from_checkpoint(
            data.market,
            config,
            snapshot_id=data.snapshot_id,
            content_hash=data.content_hash,
            checkpoint={**checkpoint, "bar_count": checkpoint["bar_count"] + 1},
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
    with pytest.raises(ValueError, match="fixed input or configuration"):
        TradingSession.from_checkpoint(
            data.market,
            replace(config, max_lots=5),
            snapshot_id=data.snapshot_id,
            content_hash=data.content_hash,
            checkpoint=checkpoint,
            account=rebuilt,
        )
