"""国内期货逐日状态机回测的关键成交假设测试。"""

from datetime import date

import pytest

from northstar_quant.backtest.futures_daily import (
    FuturesDailyBar,
    FuturesInstrumentSpec,
    FuturesRollover,
    FuturesTarget,
    FuturesWeightTarget,
    run_daily_futures_backtest,
)


DAY_1 = date(2024, 1, 2)
DAY_2 = date(2024, 1, 3)
DAY_3 = date(2024, 1, 4)


def test_signal_executes_next_session_at_open_and_applies_directional_slippage():
    result = run_daily_futures_backtest(
        bars=[
            _bar(DAY_1, "rb2405", 100, 101, 99, 100),
            _bar(
                DAY_2,
                "rb2405",
                110,
                115,
                109,
                112,
                commission_open_per_lot=2,
            ),
        ],
        instrument_specs=[_spec("rb2405", slippage_ticks=1)],
        targets=[FuturesTarget(DAY_1, "rb2405", 1)],
        initial_cash=100_000,
    )

    entry = result.trades[0]
    assert entry.trading_day == DAY_2
    assert entry.side == "BUY"
    assert entry.price == 111  # DAY_2 open 110，加一档买入滑点。
    assert result.final_equity == pytest.approx(100_008)  # 价差盈利 10，扣除每手手续费 2。


def test_same_day_stop_target_uses_conservative_stop_rule():
    result = run_daily_futures_backtest(
        bars=[
            _bar(DAY_1, "rb2405", 100, 101, 99, 100),
            _bar(DAY_2, "rb2405", 100, 110, 90, 105),
            _bar(DAY_3, "rb2405", 80, 90, 70, 85),
        ],
        instrument_specs=[_spec("rb2405")],
        targets=[FuturesTarget(DAY_1, "rb2405", 1, stop_price=95, target_price=108)],
        initial_cash=100_000,
    )

    exit_trade = result.trades[-1]
    assert exit_trade.reason == "stop_loss"
    assert exit_trade.trading_day == DAY_2
    assert exit_trade.price == 95  # 同日触及止损与止盈，优先按止损成交。


def test_gap_stop_exits_at_open_instead_of_assumed_stop_price():
    result = run_daily_futures_backtest(
        bars=[
            _bar(DAY_1, "rb2405", 100, 101, 99, 100),
            _bar(DAY_2, "rb2405", 100, 104, 96, 102),
            _bar(DAY_3, "rb2405", 90, 92, 85, 88),
        ],
        instrument_specs=[_spec("rb2405")],
        targets=[FuturesTarget(DAY_1, "rb2405", 1, stop_price=95, target_price=108)],
        initial_cash=100_000,
    )

    exit_trade = result.trades[-1]
    assert (exit_trade.reason, exit_trade.price) == ("stop_loss", 90)


def test_explicit_roll_closes_old_contract_then_opens_new_contract():
    result = run_daily_futures_backtest(
        bars=[
            _bar(DAY_1, "rb2405", 100, 101, 99, 100),
            _bar(DAY_2, "rb2405", 101, 102, 100, 101),
            _bar(DAY_3, "rb2405", 103, 104, 102, 103),
            _bar(DAY_3, "rb2410", 103, 106, 102, 106),
        ],
        instrument_specs=[_spec("rb2405"), _spec("rb2410")],
        targets=[FuturesTarget(DAY_1, "rb2405", 2)],
        rollovers=[FuturesRollover(DAY_3, "rb2405", "rb2410")],
        initial_cash=100_000,
    )

    assert [(trade.reason, trade.instrument_id, trade.qty) for trade in result.trades] == [
        ("target_open", "RB2405", 2),
        ("roll_close", "RB2405", 2),
        ("roll_open", "RB2410", 2),
    ]
    assert result.final_equity == pytest.approx(100_100)


def test_target_is_rejected_when_initial_margin_is_insufficient():
    result = run_daily_futures_backtest(
        bars=[
            _bar(DAY_1, "rb2405", 100, 101, 99, 100, margin_rate=0.2),
            _bar(DAY_2, "rb2405", 100, 101, 99, 100, margin_rate=0.2),
        ],
        instrument_specs=[_spec("rb2405", multiplier=10)],
        targets=[FuturesTarget(DAY_1, "rb2405", 1)],
        initial_cash=100,
    )

    assert result.trades == []
    assert result.rejected_targets == [f"{DAY_2}/RB2405: 保证金不足，目标手数 1 被拒绝"]


def test_target_margin_check_includes_commission_and_slippage_costs():
    result = run_daily_futures_backtest(
        bars=[
            _bar(DAY_1, "rb2405", 100, 101, 99, 100),
            _bar(
                DAY_2,
                "rb2405",
                100,
                101,
                99,
                100,
                commission_open_per_lot=2,
            ),
        ],
        instrument_specs=[
            _spec(
                "rb2405",
                multiplier=10,
            )
        ],
        targets=[FuturesTarget(DAY_1, "rb2405", 1)],
        initial_cash=101,
    )

    assert result.trades == []
    assert len(result.rejected_targets) == 1


def test_margin_call_failure_fails_closed_when_forced_close_has_no_liquidity():
    with pytest.raises(ValueError, match="日终保证金不足且强平未能完整成交"):
        run_daily_futures_backtest(
            bars=[
                _bar(DAY_1, "rb2405", 100, 101, 99, 100),
                _bar(DAY_2, "rb2405", 100, 101, 99, 100),
                _bar(DAY_3, "rb2405", 200, 201, 199, 200, volume=0),
            ],
            instrument_specs=[_spec("rb2405", multiplier=10)],
            targets=[FuturesTarget(DAY_1, "rb2405", -1)],
            initial_cash=150,
        )


def test_rollover_shifts_protective_prices_by_contract_basis():
    result = run_daily_futures_backtest(
        bars=[
            _bar(DAY_1, "rb2405", 100, 101, 99, 100),
            _bar(DAY_2, "rb2405", 100, 102, 99, 101),
            _bar(DAY_3, "rb2405", 105, 106, 104, 105),
            _bar(DAY_3, "rb2410", 120, 126, 109, 121),
        ],
        instrument_specs=[_spec("rb2405"), _spec("rb2410")],
        targets=[
            FuturesTarget(
                DAY_1,
                "rb2405",
                1,
                stop_price=95,
                target_price=110,
            )
        ],
        rollovers=[FuturesRollover(DAY_3, "rb2405", "rb2410")],
        initial_cash=100_000,
    )

    assert [trade.reason for trade in result.trades] == [
        "target_open",
        "roll_close",
        "roll_open",
        "stop_loss",
    ]
    assert result.trades[-1].price == 110


def test_rollover_fails_before_mutation_when_new_contract_margin_is_unaffordable():
    with pytest.raises(ValueError, match="换月后保证金"):
        run_daily_futures_backtest(
            bars=[
                _bar(DAY_1, "rb2405", 10, 11, 9, 10),
                _bar(DAY_2, "rb2405", 10, 11, 9, 10),
                _bar(DAY_3, "rb2405", 10, 11, 9, 10),
                _bar(DAY_3, "rb2410", 100, 101, 99, 100),
            ],
            instrument_specs=[
                _spec("rb2405"),
                _spec("rb2410"),
            ],
            targets=[FuturesTarget(DAY_1, "rb2405", 1)],
            rollovers=[FuturesRollover(DAY_3, "rb2405", "rb2410")],
            initial_cash=50,
        )


def test_target_rejects_protective_prices_on_wrong_side_of_execution_price():
    with pytest.raises(ValueError, match="多头初始止损价必须低于执行价"):
        run_daily_futures_backtest(
            bars=[
                _bar(DAY_1, "rb2405", 100, 101, 99, 100),
                _bar(DAY_2, "rb2405", 100, 101, 99, 100),
            ],
            instrument_specs=[_spec("rb2405")],
            targets=[
                FuturesTarget(
                    DAY_1,
                    "rb2405",
                    1,
                    stop_price=101,
                    target_price=110,
                )
            ],
        )


def test_missing_bar_for_held_actual_contract_fails_closed():
    with pytest.raises(ValueError, match="持仓合约缺少日线"):
        run_daily_futures_backtest(
            bars=[_bar(DAY_1, "rb2405", 100, 101, 99, 100), _bar(DAY_2, "rb2405", 100, 101, 99, 100)],
            instrument_specs=[_spec("rb2405"), _spec("rb2410")],
            targets=[FuturesTarget(DAY_1, "rb2405", 1)],
            trading_calendar=[DAY_1, DAY_2, DAY_3],
            initial_cash=100_000,
        )


def test_continuous_research_symbol_is_rejected_as_a_tradeable_instrument():
    with pytest.raises(ValueError, match="不得使用连续研究合约"):
        _spec("RB_CONT")


def test_product_card_load_failure_blocks_daily_futures_backtest(monkeypatch):
    monkeypatch.setattr(
        "northstar_quant.backtest.futures_daily.engine.load_product_cards",
        lambda: (_ for _ in ()).throw(ValueError("品种卡配置无效")),
    )

    with pytest.raises(ValueError, match="品种卡配置无效"):
        run_daily_futures_backtest(
            bars=[_bar(DAY_1, "rb2405", 100, 101, 99, 100)],
            instrument_specs=[_spec("rb2405")],
            targets=[],
        )


def test_limit_up_blocks_buy_order_conservatively():
    result = run_daily_futures_backtest(
        bars=[
            _bar(DAY_1, "rb2405", 100, 101, 99, 100),
            _bar(
                DAY_2,
                "rb2405",
                110,
                110,
                108,
                110,
                upper_limit=110,
            ),
        ],
        instrument_specs=[_spec("rb2405")],
        targets=[FuturesTarget(DAY_1, "rb2405", 1)],
    )

    assert result.trades == []
    assert "买单触及涨停" in result.rejected_targets[0]


def test_volume_participation_produces_partial_fill():
    result = run_daily_futures_backtest(
        bars=[
            _bar(DAY_1, "rb2405", 100, 101, 99, 100),
            _bar(DAY_2, "rb2405", 100, 101, 99, 100, volume=10),
        ],
        instrument_specs=[_spec("rb2405")],
        targets=[FuturesTarget(DAY_1, "rb2405", 3)],
        max_volume_participation=0.1,
    )

    assert result.trades[0].qty == 1
    assert "请求 3 手，成交 1 手" in result.rejected_targets[0]


def test_same_day_exit_uses_close_today_commission():
    result = run_daily_futures_backtest(
        bars=[
            _bar(DAY_1, "rb2405", 100, 101, 99, 100),
            _bar(
                DAY_2,
                "rb2405",
                100,
                101,
                94,
                100,
                commission_open_per_lot=2,
                commission_close_per_lot=3,
                commission_close_today_per_lot=20,
            ),
        ],
        instrument_specs=[_spec("rb2405")],
        targets=[FuturesTarget(DAY_1, "rb2405", 1, stop_price=95)],
        initial_cash=100_000,
    )

    assert [trade.commission for trade in result.trades] == [2, 20]
    assert result.final_equity == pytest.approx(99_928)


def test_weight_target_uses_margin_allocation_and_integer_lots():
    result = run_daily_futures_backtest(
        bars=[
            _bar(DAY_1, "rb2405", 100, 101, 99, 100),
            _bar(DAY_2, "rb2405", 100, 101, 99, 100),
        ],
        instrument_specs=[_spec("rb2405")],
        weight_targets=[FuturesWeightTarget(DAY_1, "rb2405", 0.2)],
        initial_cash=1_000,
    )

    assert result.trades[0].qty == 2


def _spec(
    instrument_id: str,
    *,
    multiplier: float = 10,
    slippage_ticks: float = 0,
) -> FuturesInstrumentSpec:
    return FuturesInstrumentSpec(
        instrument_id=instrument_id.upper(),
        product="RB",
        exchange_id="SHFE",
        multiplier=multiplier,
        tick_size=1,
        slippage_ticks=slippage_ticks,
    )


def _bar(
    day: date,
    instrument_id: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    *,
    settlement: float | None = None,
    volume: float = 100_000,
    upper_limit: float | None = None,
    lower_limit: float | None = None,
    margin_rate: float = 0.1,
    commission_open_per_lot: float = 0,
    commission_close_per_lot: float = 0,
    commission_close_today_per_lot: float = 0,
) -> FuturesDailyBar:
    resolved_settlement = close if settlement is None else settlement
    return FuturesDailyBar(
        trading_day=day,
        instrument_id=instrument_id.upper(),
        open=open_,
        high=high,
        low=low,
        close=close,
        settlement=resolved_settlement,
        pre_settlement=resolved_settlement,
        volume=volume,
        open_interest=50_000,
        upper_limit=upper_limit if upper_limit is not None else high + 100,
        lower_limit=lower_limit if lower_limit is not None else max(low - 100, 0.01),
        margin_rate=margin_rate,
        commission_open_per_lot=commission_open_per_lot,
        commission_open_rate=0,
        commission_close_per_lot=commission_close_per_lot,
        commission_close_rate=0,
        commission_close_today_per_lot=commission_close_today_per_lot,
        commission_close_today_rate=0,
        max_position_lots=1_000_000,
        first_session="night",
        session_complete=True,
    )
