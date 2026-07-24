from datetime import date

import polars as pl
import pytest

from northstar_quant.backtest.canonical import run_strategy_output_backtest
from northstar_quant.common.enums import DataFrequency, StrategyOutputType
from northstar_quant.common.types import StrategyOutputBundle
from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.strategies.base import DailyTradePlanStrategyBase
from northstar_quant.strategies.models import TradePlan
from northstar_quant.strategies.pipeline import run_profile_strategy_pipeline


class _ExampleDailyTradePlanStrategy(DailyTradePlanStrategyBase):
    strategy_id = "example_daily_trade_plan"

    def generate_trade_plans(self, market_df: pl.DataFrame) -> pl.DataFrame:
        self.validate_market_data(market_df)
        latest = market_df.sort("date").tail(1).row(0, named=True)
        plan = TradePlan(
            symbol=str(latest["symbol"]),
            signal_value=1.0,
            side="BUY",
            planned_entry_price=float(latest["close"]),
            initial_stop_price=float(latest["close"]) - 2.0,
            target_r=2.0,
            entry_condition="下一交易日开盘价不高于计划入场价",
            decision_time=latest["date"],
            cancel_condition="开盘价跳空高于计划入场价 3%",
            reason="日线趋势回调后突破",
            trend="上涨",
            support_price=float(latest["low"]),
            resistance_price=float(latest["high"]),
        )
        return self.to_trade_plans_frame([plan.to_row()])


def _daily_market_df() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "date": date(2024, 1, 2),
                "symbol": "AAA",
                "open": 100.0,
                "high": 103.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1_000_000,
            }
        ]
    )


def test_daily_trade_plan_base_generates_normalized_plan_and_derives_target_price():
    strategy = _ExampleDailyTradePlanStrategy()

    output = strategy.generate_trade_plans(_daily_market_df())
    latest = strategy.latest_trade_plans(output)

    assert strategy.output_type == StrategyOutputType.TRADE_PLAN
    assert strategy.supported_data_frequencies == (DataFrequency.D1,)
    assert latest["symbol"].to_list() == ["AAA"]
    assert latest["risk_per_unit"].to_list() == pytest.approx([2.0])
    assert latest["target_price"].to_list() == pytest.approx([105.0])
    assert latest["reward_per_unit"].to_list() == pytest.approx([4.0])
    assert latest["risk_reward_ratio"].to_list() == pytest.approx([2.0])


def test_trade_plan_rejects_invalid_price_relationships():
    with pytest.raises(ValueError, match="初始止损价必须低于"):
        TradePlan(
            symbol="AAA",
            signal_value=1.0,
            side="BUY",
            planned_entry_price=100.0,
            initial_stop_price=100.0,
            entry_condition="收盘突破前高",
            decision_time=date(2024, 1, 2),
        )

    with pytest.raises(ValueError, match="目标价与 target_r 不一致"):
        TradePlan(
            symbol="AAA",
            signal_value=1.0,
            side="BUY",
            planned_entry_price=100.0,
            initial_stop_price=95.0,
            target_price=110.0,
            target_r=3.0,
            entry_condition="收盘突破前高",
            decision_time=date(2024, 1, 2),
        )


def test_trade_plan_base_rejects_inconsistent_dataframe_values():
    strategy = _ExampleDailyTradePlanStrategy()
    invalid = pl.DataFrame(
        [
            {
                "date": date(2024, 1, 2),
                "symbol": "AAA",
                "signal_value": 1.0,
                "side": "BUY",
                "planned_entry_price": 100.0,
                "initial_stop_price": 95.0,
                "target_price": 110.0,
                "target_r": 3.0,
                "entry_condition": "收盘突破前高",
            }
        ]
    )

    with pytest.raises(ValueError, match="目标价与 target_r 不一致"):
        strategy.normalize_output(invalid)


def test_pipeline_returns_single_trade_plan_strategy_without_portfolio_weighting(monkeypatch):
    strategy = _ExampleDailyTradePlanStrategy()
    profile = load_trading_profile("cn_etf_daily_live")
    monkeypatch.setattr(
        "northstar_quant.strategies.pipeline.build_selected_profile_strategies",
        lambda *_args, **_kwargs: ([(strategy, 1.0)], (strategy.strategy_id,)),
    )

    bundle = run_profile_strategy_pipeline(_daily_market_df(), profile, latest_only=True)

    assert bundle.output_type == StrategyOutputType.TRADE_PLAN
    assert bundle.frame["strategy_id"].to_list() == [strategy.strategy_id]


def test_trade_plan_is_fail_closed_until_a_dedicated_backtest_engine_exists():
    strategy = _ExampleDailyTradePlanStrategy()
    profile = load_trading_profile("cn_etf_daily_live")
    bundle = StrategyOutputBundle(
        strategy_id=strategy.strategy_id,
        output_type=StrategyOutputType.TRADE_PLAN,
        time_column="date",
        frame=strategy.generate_trade_plans(_daily_market_df()),
    )

    with pytest.raises(ValueError, match="尚未接入专用回测状态机"):
        run_strategy_output_backtest(profile, _daily_market_df(), bundle)
