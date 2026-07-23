"""可复用的技术指标。

指标只负责从行情计算特征，不负责选标的、仓位、风控或绩效归因。
常规批处理指标保持为无状态函数；只有需要实时增量计算或配置化实例化时，
才应实现 :class:`~northstar_quant.indicators.base.Indicator` 协议。
"""

from northstar_quant.indicators.base import Indicator, IndicatorSpec
from northstar_quant.indicators.momentum import (
    rate_of_change,
    relative_strength_index,
    stochastic_oscillator,
    williams_r,
)
from northstar_quant.indicators.registry import get_indicator_spec, list_indicator_specs
from northstar_quant.indicators.trend import (
    exponential_moving_average,
    donchian_channel,
    moving_average_convergence_divergence,
    prior_rolling_max,
    simple_moving_average,
)
from northstar_quant.indicators.volatility import (
    average_true_range,
    bollinger_bands,
    historical_volatility,
)
from northstar_quant.indicators.volume import (
    chaikin_money_flow,
    on_balance_volume,
    volume_weighted_average_price,
)

__all__ = [
    "Indicator",
    "IndicatorSpec",
    "average_true_range",
    "bollinger_bands",
    "chaikin_money_flow",
    "donchian_channel",
    "exponential_moving_average",
    "get_indicator_spec",
    "historical_volatility",
    "list_indicator_specs",
    "moving_average_convergence_divergence",
    "on_balance_volume",
    "prior_rolling_max",
    "rate_of_change",
    "relative_strength_index",
    "simple_moving_average",
    "stochastic_oscillator",
    "volume_weighted_average_price",
    "williams_r",
]
