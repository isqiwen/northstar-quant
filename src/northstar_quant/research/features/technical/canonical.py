"""受控的波动率、成交量和持仓量 canonical feature。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import math

from northstar_quant.data.market.pit import MarketDataSnapshot
from northstar_quant.research.features.canonical import (
    CN_FUTURES_ACTUAL_CONTRACT_FEATURE_BAR_V1,
    CN_FUTURES_FEATURE_BAR_V1,
    CanonicalFeatureComputer,
    CanonicalFeatureDefinition,
    actual_contract_id_in_scope,
    integer_parameter,
    non_negative_number,
    positive_number,
)
from northstar_quant.research.features.models import (
    FeatureLineage,
    FeatureValue,
    FeatureVersion,
)


REALIZED_VOLATILITY = CanonicalFeatureDefinition(
    feature_id="technical.realized_volatility",
    description="按同一 feature-ready 期货序列计算简单收益率的未年化样本标准差。",
    input_contract=CN_FUTURES_FEATURE_BAR_V1,
    required_columns=("close",),
    output_column="realized_volatility",
    lookback_semantics="window_bars 是连续观测 bar 的收益率个数；每个值使用当前及此前 window_bars 个收益率，不年化。",
    missing_value_semantics="不足 window_bars+1 个观测为 lookback_not_ready；窗口内任一 close 缺失为 input_missing；非正或非有限 close 拒绝整次回填。",
    parameter_schema={"window_bars": {"type": "integer", "required": True, "minimum": 2}},
)

VOLUME_RATIO = CanonicalFeatureDefinition(
    feature_id="technical.volume_ratio",
    description="当前成交量相对于此前 N 个观测 bar 平均成交量的比率。",
    input_contract=CN_FUTURES_FEATURE_BAR_V1,
    required_columns=("volume",),
    output_column="volume_ratio",
    lookback_semantics="window_bars 只计算 t-N 至 t-1 的历史均量，明确排除当前成交量。",
    missing_value_semantics="历史不足为 lookback_not_ready；当前或历史 volume 缺失为 input_missing；历史均量为零为 zero_baseline_volume；负数或非有限 volume 拒绝整次回填。",
    parameter_schema={"window_bars": {"type": "integer", "required": True, "minimum": 1}},
)

OPEN_INTEREST_CHANGE = CanonicalFeatureDefinition(
    feature_id="technical.open_interest_change",
    description="同一受授权实际合约序列的 N 个观测 bar 持仓量变化率。",
    input_contract=CN_FUTURES_ACTUAL_CONTRACT_FEATURE_BAR_V1,
    required_columns=("open_interest",),
    output_column="open_interest_change",
    lookback_semantics="lookback_bars 为观测 bar 数，计算 OI_t / OI_(t-N) - 1；只接受 contract_id 键、行级 available_at 和 actual_contract_data=true 的 feature-ready 投影。",
    missing_value_semantics="预热为 lookback_not_ready；窗口内 OI 缺失为 input_missing；基准 OI 为零为 zero_baseline_open_interest；负数或非有限 OI 拒绝整次回填。",
    parameter_schema={"lookback_bars": {"type": "integer", "required": True, "minimum": 1}},
)


class RealizedVolatilityComputer(CanonicalFeatureComputer):
    """使用 N 个简单收益率、``ddof=1`` 的未年化 realized volatility。"""

    def __init__(self, version: FeatureVersion) -> None:
        super().__init__(version, REALIZED_VOLATILITY)

    def compute(
        self,
        *,
        market_snapshot: MarketDataSnapshot,
        parameters: Mapping[str, object],
        lineage: FeatureLineage,
    ) -> Iterable[FeatureValue]:
        window = integer_parameter(parameters, "window_bars", minimum=2)
        rows = self._rows(market_snapshot=market_snapshot, lineage=lineage)
        values: list[FeatureValue] = []
        for group in self._groups(
            rows, entity_key_columns=REALIZED_VOLATILITY.input_contract.entity_key_columns
        ):
            closes = [positive_number(row.values["close"], field_name="close") for row in group]
            for index, row in enumerate(group):
                if index < window:
                    values.append(
                        self._value(
                            lineage=lineage,
                            row=row,
                            value=None,
                            missing_reason="lookback_not_ready",
                        )
                    )
                    continue
                prices = closes[index - window : index + 1]
                if any(value is None for value in prices):
                    values.append(
                        self._value(
                            lineage=lineage,
                            row=row,
                            value=None,
                            missing_reason="input_missing",
                        )
                    )
                    continue
                valid_prices = [float(value) for value in prices if value is not None]
                returns = [
                    valid_prices[position] / valid_prices[position - 1] - 1.0
                    for position in range(1, len(valid_prices))
                ]
                mean_return = sum(returns) / len(returns)
                variance = sum((item - mean_return) ** 2 for item in returns) / (len(returns) - 1)
                values.append(self._value(lineage=lineage, row=row, value=math.sqrt(variance)))
        return tuple(values)


class VolumeRatioComputer(CanonicalFeatureComputer):
    """当前 volume / 前 N 个 bar 的均量，避免把当前量放进基准。"""

    def __init__(self, version: FeatureVersion) -> None:
        super().__init__(version, VOLUME_RATIO)

    def compute(
        self,
        *,
        market_snapshot: MarketDataSnapshot,
        parameters: Mapping[str, object],
        lineage: FeatureLineage,
    ) -> Iterable[FeatureValue]:
        window = integer_parameter(parameters, "window_bars", minimum=1)
        rows = self._rows(market_snapshot=market_snapshot, lineage=lineage)
        values: list[FeatureValue] = []
        for group in self._groups(
            rows, entity_key_columns=VOLUME_RATIO.input_contract.entity_key_columns
        ):
            volumes = [
                non_negative_number(row.values["volume"], field_name="volume") for row in group
            ]
            for index, row in enumerate(group):
                if index < window:
                    values.append(
                        self._value(
                            lineage=lineage,
                            row=row,
                            value=None,
                            missing_reason="lookback_not_ready",
                        )
                    )
                    continue
                history = volumes[index - window : index]
                current = volumes[index]
                if current is None or any(value is None for value in history):
                    values.append(
                        self._value(
                            lineage=lineage,
                            row=row,
                            value=None,
                            missing_reason="input_missing",
                        )
                    )
                    continue
                baseline = sum(float(value) for value in history if value is not None) / window
                if baseline == 0:
                    values.append(
                        self._value(
                            lineage=lineage,
                            row=row,
                            value=None,
                            missing_reason="zero_baseline_volume",
                        )
                    )
                    continue
                values.append(self._value(lineage=lineage, row=row, value=current / baseline))
        return tuple(values)


class OpenInterestChangeComputer(CanonicalFeatureComputer):
    """``OI_t / OI_(t-N) - 1``，仅消费带行级可用时间的 feature-ready 投影。"""

    def __init__(self, version: FeatureVersion) -> None:
        super().__init__(version, OPEN_INTEREST_CHANGE)

    def compute(
        self,
        *,
        market_snapshot: MarketDataSnapshot,
        parameters: Mapping[str, object],
        lineage: FeatureLineage,
    ) -> Iterable[FeatureValue]:
        lookback = integer_parameter(parameters, "lookback_bars", minimum=1)
        rows = self._rows(market_snapshot=market_snapshot, lineage=lineage)
        values: list[FeatureValue] = []
        for group in self._groups(
            rows, entity_key_columns=OPEN_INTEREST_CHANGE.input_contract.entity_key_columns
        ):
            for row in group:
                actual_contract_id_in_scope(
                    row.key["contract_id"],
                    scope=market_snapshot.publication_scope,
                )
            interests = [
                non_negative_number(row.values["open_interest"], field_name="open_interest")
                for row in group
            ]
            for index, row in enumerate(group):
                if index < lookback:
                    values.append(
                        self._value(
                            lineage=lineage,
                            row=row,
                            value=None,
                            missing_reason="lookback_not_ready",
                        )
                    )
                    continue
                window = interests[index - lookback : index + 1]
                if any(value is None for value in window):
                    values.append(
                        self._value(
                            lineage=lineage,
                            row=row,
                            value=None,
                            missing_reason="input_missing",
                        )
                    )
                    continue
                baseline = window[0]
                current = window[-1]
                assert baseline is not None and current is not None
                if baseline == 0:
                    values.append(
                        self._value(
                            lineage=lineage,
                            row=row,
                            value=None,
                            missing_reason="zero_baseline_open_interest",
                        )
                    )
                    continue
                values.append(self._value(lineage=lineage, row=row, value=current / baseline - 1.0))
        return tuple(values)
