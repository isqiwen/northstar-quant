"""把统一策略输出安全分派到对应回测器。

策略可以输出目标权重、交易意图或交易计划；三者的成交语义不同，不能为了“能运行”
而互相转换。本模块只允许目标权重输出进入画像明确选择的回测器。
"""

from __future__ import annotations

from northstar_quant.research.backtest.models import BacktestResult
from northstar_quant.research.backtest.registry import run_target_backtest
from northstar_quant.foundation.common.enums import StrategyOutputType
from northstar_quant.foundation.common.types import StrategyOutputBundle
from northstar_quant.foundation.config.trading_profile import TradingProfile


def run_strategy_output_backtest(
    profile: TradingProfile,
    market_df,
    output_bundle: StrategyOutputBundle,
) -> BacktestResult:
    """根据输出类型选择已匹配的回测器，并拒绝语义不完整的转换。

    ``TARGET_WEIGHT`` 可由连续收益研究、实际合约逐日或分钟订单回放引擎处理；
    ``TRADE_PLAN`` 尚未定义组合资金分配和成交语义，因此仍明确失败关闭。
    """

    if output_bundle.output_type == StrategyOutputType.TARGET_WEIGHT:
        return run_target_backtest(profile, market_df, output_bundle.frame)

    if output_bundle.output_type == StrategyOutputType.TRADE_PLAN:
        raise ValueError(
            "TradePlan 策略尚未接入期货专用回测状态机，不能错误地按目标权重回测。"
        )

    raise ValueError(
        f"暂不支持的策略输出类型：{output_bundle.output_type.value}"
    )
