"""Canonical profile research entrypoint."""

from __future__ import annotations

from northstar_quant.backtest.canonical import run_strategy_output_backtest
from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.data.storage import load_profile_signal_data
from northstar_quant.strategies.pipeline import (
    latest_pipeline_output,
    resolve_selected_profile_strategy_ids,
    run_profile_strategy_pipeline,
)


def run_momentum_research(profile_id: str | None = None) -> dict:
    """Run research on the same canonical strategy pipeline used by backtest/live."""

    profile = load_trading_profile(profile_id)
    market_df = load_profile_signal_data(profile)
    selected_strategy_ids = resolve_selected_profile_strategy_ids(profile)
    pipeline = run_profile_strategy_pipeline(
        market_df,
        profile,
        latest_only=False,
    )
    latest_holdings = latest_pipeline_output(pipeline)
    result = run_strategy_output_backtest(profile, market_df, pipeline)

    return {
        "profile_id": profile.profile_id,
        "price_field": profile.data.price_field,
        "output_type": pipeline.output_type.value,
        "selected_strategy_ids": list(selected_strategy_ids),
        "total_return": result.total_return,
        "annualized_return": result.annualized_return,
        "max_drawdown": result.max_drawdown,
        "turnover_estimate": result.turnover_estimate,
        "symbols": sorted(set(latest_holdings["symbol"].to_list()))
        if "symbol" in latest_holdings.columns
        else [],
        "latest_holdings": latest_holdings.to_dicts(),
    }
