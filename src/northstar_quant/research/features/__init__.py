"""可复用的技术指标。

指标只负责从行情计算特征，不负责选标的、仓位、风控或绩效归因。
常规批处理指标保持为无状态函数；只有需要实时增量计算或配置化实例化时，
才应实现 :class:`~northstar_quant.research.features.technical.base.Indicator` 协议。
"""

from northstar_quant.research.features.technical.base import Indicator, IndicatorSpec
from northstar_quant.research.features.momentum.momentum import (
    rate_of_change,
    relative_strength_index,
    stochastic_oscillator,
    williams_r,
)
from northstar_quant.research.features.technical.registry import (
    get_indicator_spec,
    list_indicator_specs,
)
from northstar_quant.research.features.technical.trend import (
    exponential_moving_average,
    donchian_channel,
    moving_average_convergence_divergence,
    prior_rolling_max,
    simple_moving_average,
)
from northstar_quant.research.features.technical.volatility import (
    average_true_range,
    bollinger_bands,
    historical_volatility,
)
from northstar_quant.research.features.technical.volume import (
    chaikin_money_flow,
    on_balance_volume,
    volume_weighted_average_price,
)
from northstar_quant.research.features.models import (
    FeatureBackfill,
    FeatureDependency,
    FeatureDependencyKind,
    FeatureDatasetEvidence,
    FeatureDeterminismError,
    FeatureLineage,
    FeatureRegistryError,
    FeatureSpec,
    FeatureValue,
    FeatureVersion,
)
from northstar_quant.research.features.registry import FeatureComputer, FeatureRegistry
from northstar_quant.research.features.catalog import (
    CanonicalFeatureRegistration,
    get_canonical_feature_registration,
    list_canonical_feature_registrations,
    register_all_canonical_features,
    register_canonical_feature,
)

__all__ = [
    "Indicator",
    "IndicatorSpec",
    "FeatureBackfill",
    "FeatureComputer",
    "FeatureDependency",
    "FeatureDependencyKind",
    "FeatureDatasetEvidence",
    "FeatureDeterminismError",
    "FeatureLineage",
    "FeatureRegistry",
    "FeatureRegistryError",
    "FeatureSpec",
    "FeatureValue",
    "FeatureVersion",
    "CanonicalFeatureRegistration",
    "average_true_range",
    "bollinger_bands",
    "chaikin_money_flow",
    "donchian_channel",
    "exponential_moving_average",
    "get_indicator_spec",
    "historical_volatility",
    "get_canonical_feature_registration",
    "list_canonical_feature_registrations",
    "list_indicator_specs",
    "moving_average_convergence_divergence",
    "on_balance_volume",
    "prior_rolling_max",
    "rate_of_change",
    "relative_strength_index",
    "register_all_canonical_features",
    "register_canonical_feature",
    "simple_moving_average",
    "stochastic_oscillator",
    "volume_weighted_average_price",
    "williams_r",
]
