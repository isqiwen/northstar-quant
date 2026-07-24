"""国内期货逐日状态机回测的关键成交假设测试。"""

from datetime import date

import pytest

from northstar_quant.backtest.futures_daily import (
    FuturesDailyBar,
    FuturesInstrumentSpec,
    FuturesRollover,
    FuturesTarget,
    run_daily_futures_backtest,
)


DAY_1 = date(2024, 1, 2)
DAY_2 = date(2024, 1, 3)
DAY_3 = date(2024, 1, 4)


def test_signal_executes_next_session_at_open_and_applies_directional_slippage():
    result = run_daily_futures_backtest(
        bars=[_bar(DAY_1, "rb2405", 100, 101, 99, 100), _bar(DAY_2, "rb2405", 110, 115, 109, 112)],
        instrument_specs=[_spec("rb2405", slippage_ticks=1, commission_per_lot=2)],
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
        bars=[_bar(DAY_1, "rb2405", 100, 101, 99, 100), _bar(DAY_2, "rb2405", 100, 101, 99, 100)],
        instrument_specs=[_spec("rb2405", margin_rate=0.2, multiplier=10)],
        targets=[FuturesTarget(DAY_1, "rb2405", 1)],
        initial_cash=100,
    )

    assert result.trades == []
    assert result.rejected_targets == [f"{DAY_2}/RB2405: 保证金不足，目标手数 1 被拒绝"]


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
        "northstar_quant.backtest.futures_daily.load_product_cards",
        lambda: (_ for _ in ()).throw(ValueError("品种卡配置无效")),
    )

    with pytest.raises(ValueError, match="品种卡配置无效"):
        run_daily_futures_backtest(
            bars=[_bar(DAY_1, "rb2405", 100, 101, 99, 100)],
            instrument_specs=[_spec("rb2405")],
            targets=[],
        )


def _spec(
    instrument_id: str,
    *,
    multiplier: float = 10,
    margin_rate: float = 0.1,
    slippage_ticks: float = 0,
    commission_per_lot: float = 0,
) -> FuturesInstrumentSpec:
    return FuturesInstrumentSpec(
        instrument_id=instrument_id.upper(),
        product="RB",
        exchange_id="SHFE",
        multiplier=multiplier,
        tick_size=1,
        initial_margin_rate=margin_rate,
        slippage_ticks=slippage_ticks,
        commission_per_lot=commission_per_lot,
    )


def _bar(day: date, instrument_id: str, open_: float, high: float, low: float, close: float) -> FuturesDailyBar:
    return FuturesDailyBar(day, instrument_id.upper(), open_, high, low, close)
