"""按交易画像运行完整离线回测的编排入口。

本模块连接本地历史数据、策略管线与通用回测器，输出供 CLI、报告和测试使用的摘要。
它不包含具体策略公式，也不负责订单提交；因此它只适用于 ``offline`` 研究画像，不能
替代模拟账户或真实账户运行服务。
"""

from __future__ import annotations

from typing import Any

from northstar_quant.backtest.canonical import run_strategy_output_backtest
from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.data.storage import load_profile_signal_data
from northstar_quant.strategies.pipeline import (
    latest_pipeline_output,
    resolve_selected_profile_strategy_ids,
    run_profile_strategy_pipeline,
)


def run_profile_backtest(profile_id: str | None = None) -> dict[str, Any]:
    """对一个离线交易画像运行其全部启用策略的历史回测。

    策略选择、价格口径和风险缩放全部来自画像；函数使用完整历史生成信号，再将同一
    策略输出交给 canonical 回测器计算绩效。返回的 ``latest_holdings`` 只是历史数据
    最后一个时点的研究目标，既不是持仓查询，也不能直接转换为真实委托。
    """

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
