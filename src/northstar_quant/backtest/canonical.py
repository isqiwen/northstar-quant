"""把统一策略输出安全分派到对应回测器。

策略可以输出目标权重、交易意图或交易计划；三者的成交语义不同，不能为了“能运行”
而互相转换。本模块只允许当前已验证的目标权重输出进入连续合约收益回测。
"""

from __future__ import annotations

from northstar_quant.backtest.event_engine import BacktestResult
from northstar_quant.backtest.registry import run_target_backtest
from northstar_quant.common.enums import StrategyOutputType
from northstar_quant.common.types import StrategyOutputBundle
from northstar_quant.config.trading_profile import TradingProfile


def run_strategy_output_backtest(
    profile: TradingProfile,
    market_df,
    output_bundle: StrategyOutputBundle,
) -> BacktestResult:
    """根据输出类型选择已匹配的回测器，并拒绝语义不完整的转换。

    ``TARGET_WEIGHT`` 使用收益率型研究回测；``TRADE_PLAN`` 必须等待逐日持仓与撮合
    状态机，不得按权重收益偷换假设；其他输出类型同样明确失败关闭。
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
