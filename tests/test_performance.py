from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from northstar_quant.backtest import (
    DailyBacktestConfig,
    analyze_daily_stateful_result,
    run_daily_stateful_backtest,
)
from northstar_quant.performance import (
    EquityPoint,
    ExecutionFill,
    analyze_long_only_fills,
    calculate_max_drawdown,
)


def test_fifo_trade_analysis_allocates_partial_fill_commissions_and_risk():
    analysis = analyze_long_only_fills(
        [
            ExecutionFill(
                fill_id="buy-1",
                timestamp=date(2024, 1, 2),
                symbol="SPY",
                side="BUY",
                qty=10,
                price=100.0,
                commission=1.0,
                initial_stop_price=95.0,
                target_r=2.0,
                strategy_id="ema_pullback",
                reason="entry_signal",
            ),
            ExecutionFill(
                fill_id="sell-1",
                timestamp=date(2024, 1, 4),
                symbol="SPY",
                side="SELL",
                qty=4,
                price=110.0,
                commission=0.4,
                reason="take_profit",
            ),
            ExecutionFill(
                fill_id="sell-2",
                timestamp=date(2024, 1, 5),
                symbol="SPY",
                side="SELL",
                qty=6,
                price=90.0,
                commission=0.6,
                reason="stop_loss",
            ),
        ]
    )

    assert len(analysis.trades) == 2
    first, second = analysis.trades
    assert first.qty == 4
    assert first.entry_commission == pytest.approx(0.4)
    assert first.exit_commission == pytest.approx(0.4)
    assert first.net_pnl == pytest.approx(39.2)
    assert first.initial_risk == 20.0
    assert first.pnl_r == pytest.approx(1.96)
    assert first.exit_reason == "take_profit"
    assert second.qty == 6
    assert second.net_pnl == pytest.approx(-61.2)
    assert second.pnl_r == pytest.approx(-2.04)
    assert analysis.open_qty_by_symbol == {}
    assert analysis.metrics.closed_trade_count == 2
    assert analysis.metrics.win_rate == 0.5
    assert analysis.metrics.expectancy_r == pytest.approx(-0.04)
    assert analysis.metrics.profit_factor == pytest.approx(39.2 / 61.2)


def test_unrated_trades_keep_currency_pnl_without_fabricating_r_multiple():
    analysis = analyze_long_only_fills(
        [
            ExecutionFill("buy", date(2024, 1, 2), "SPY", "BUY", 1, 100.0),
            ExecutionFill("sell", date(2024, 1, 3), "SPY", "SELL", 1, 105.0),
        ]
    )

    assert analysis.trades[0].net_pnl == 5.0
    assert analysis.trades[0].pnl_r is None
    assert analysis.metrics.rated_trade_count == 0
    assert analysis.metrics.expectancy_r is None


def test_trade_analysis_rejects_oversold_inventory_and_invalid_risk():
    with pytest.raises(ValueError, match="超过可用多头持仓"):
        analyze_long_only_fills(
            [ExecutionFill("sell", date(2024, 1, 2), "SPY", "SELL", 1, 100.0)]
        )

    with pytest.raises(ValueError, match="必须低于入场价格"):
        analyze_long_only_fills(
            [
                ExecutionFill(
                    "buy",
                    date(2024, 1, 2),
                    "SPY",
                    "BUY",
                    1,
                    100.0,
                    initial_stop_price=100.0,
                )
            ]
        )


def test_daily_stateful_adapter_reuses_shared_trade_analysis_without_risk_guessing():
    market = [
        {"date": date(2024, 1, 2), "symbol": "AAA", "open": 10.0, "close": 10.0},
        {"date": date(2024, 1, 3), "symbol": "AAA", "open": 10.0, "close": 10.0},
        {"date": date(2024, 1, 4), "symbol": "AAA", "open": 12.0, "close": 12.0},
    ]
    targets = [
        {"date": date(2024, 1, 2), "symbol": "AAA", "target_weight": 1.0},
        {"date": date(2024, 1, 3), "symbol": "AAA", "target_weight": 0.0},
    ]
    result = run_daily_stateful_backtest(
        pl.DataFrame(market),
        pl.DataFrame(targets),
        config=DailyBacktestConfig(initial_cash=1_000.0),
    )

    analysis = analyze_daily_stateful_result(result)

    assert analysis.trades[0].net_pnl == 200.0
    assert analysis.trades[0].pnl_r is None
    assert analysis.metrics.net_pnl == 200.0


def test_max_drawdown_uses_shared_equity_points():
    assert calculate_max_drawdown(
        [
            EquityPoint(date(2024, 1, 2), 100.0),
            EquityPoint(date(2024, 1, 3), 110.0),
            EquityPoint(date(2024, 1, 4), 99.0),
        ]
    ) == pytest.approx(-0.1)
