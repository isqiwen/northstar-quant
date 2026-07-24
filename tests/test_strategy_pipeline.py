from dataclasses import replace
from datetime import date, timedelta

import polars as pl
import pytest

from northstar_quant.common.enums import AssetType
from northstar_quant.backtest.registry import run_simulation_backtest
from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.research.momentum_scan import run_momentum_research
from northstar_quant.strategies.pipeline import (
    build_profile_risk_limits,
    build_selected_profile_strategies,
    latest_pipeline_output,
    run_profile_strategy_pipeline,
)


def _daily_market_df(days: int = 140) -> pl.DataFrame:
    start = date(2024, 1, 1)
    symbols = {
        "510300.SS": 0.010,
        "510500.SS": 0.008,
        "159915.SZ": 0.006,
        "510050.SS": 0.002,
    }
    rows: list[dict] = []
    for offset in range(days):
        current_date = start + timedelta(days=offset)
        for symbol, slope in symbols.items():
            close = 100.0 * (1.0 + slope * offset)
            rows.append(
                {
                    "date": current_date,
                    "symbol": symbol,
                    "open": close * 0.99,
                    "high": close * 1.01,
                    "low": close * 0.98,
                    "close": close,
                    "volume": 1_000_000 + offset,
                }
            )
    return pl.DataFrame(rows)


def test_profile_strategy_pipeline_full_history_matches_latest_slice():
    profile = load_trading_profile("cn_etf_daily_live")
    market_df = _daily_market_df()

    full_pipeline = run_profile_strategy_pipeline(
        market_df,
        profile,
        latest_only=False,
    )
    latest_pipeline = run_profile_strategy_pipeline(
        market_df,
        profile,
        latest_only=True,
    )

    full_latest = latest_pipeline_output(full_pipeline).sort("symbol")
    latest_frame = latest_pipeline.frame.sort("symbol")

    assert full_pipeline.output_type.value == "target_weight"
    assert full_latest["symbol"].to_list() == latest_frame["symbol"].to_list()
    assert full_latest["target_weight"].to_list() == pytest.approx(
        latest_frame["target_weight"].to_list()
    )
    assert float(full_latest["target_weight"].sum()) == pytest.approx(0.98)


def test_profile_strategy_pipeline_subset_re_normalizes_selected_capital():
    profile = load_trading_profile("cn_etf_daily_live")
    market_df = _daily_market_df()

    subset_pipeline = run_profile_strategy_pipeline(
        market_df,
        profile,
        strategy_ids=("momentum",),
        latest_only=False,
    )
    latest_holdings = latest_pipeline_output(subset_pipeline)

    assert latest_holdings.height == 3
    assert float(latest_holdings["target_weight"].sum()) == pytest.approx(0.98)


def test_canonical_pipeline_reuses_profile_strategy_compatibility_validation():
    profile = replace(
        load_trading_profile("cn_etf_daily_live"),
        asset_type=AssetType.EQUITY,
    )

    with pytest.raises(ValueError, match="etf_rotation 不支持资产类型 EQUITY"):
        build_selected_profile_strategies(profile)


def test_build_profile_risk_limits_rejects_unknown_field():
    profile = load_trading_profile("cn_etf_daily_live")
    invalid_profile = replace(
        profile,
        risk={**profile.risk, "max_order_notianal": 1000.0},
    )

    with pytest.raises(ValueError, match="不支持的风控字段.*max_order_notianal"):
        build_profile_risk_limits(invalid_profile)


def test_run_momentum_research_uses_canonical_profile_pipeline(monkeypatch):
    market_df = _daily_market_df()
    monkeypatch.setattr(
        "northstar_quant.research.momentum_scan.load_profile_signal_data",
        lambda profile: market_df,
    )

    result = run_momentum_research("cn_etf_daily_live")

    assert result["profile_id"] == "cn_etf_daily_live"
    assert result["output_type"] == "target_weight"
    assert result["selected_strategy_ids"] == ["etf_rotation", "momentum"]
    assert len(result["latest_holdings"]) == 3
    assert sum(row["target_weight"] for row in result["latest_holdings"]) == pytest.approx(0.98)


def test_run_simulation_backtest_supports_portfolio_level_canonical_pipeline(monkeypatch):
    profile = load_trading_profile("cn_etf_daily_live")
    market_df = _daily_market_df()
    monkeypatch.setattr(
        "northstar_quant.backtest.backtrader_runner.load_profile_signal_data",
        lambda profile_id: market_df,
    )

    result = run_simulation_backtest(profile, strategy_name="portfolio")

    assert result["backtester"] == "backtrader_bar_simulation"
    assert result["strategy"] == "portfolio"
    assert result["selected_strategy_ids"] == ["etf_rotation", "momentum"]
    assert result["output_type"] == "target_weight"
    assert result["bars"] > 0
