"""受控的动量 canonical feature。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from northstar_quant.data.market.pit import MarketDataSnapshot
from northstar_quant.research.features.canonical import (
    CN_FUTURES_FEATURE_BAR_V1,
    CanonicalFeatureComputer,
    CanonicalFeatureDefinition,
    integer_parameter,
    positive_number,
)
from northstar_quant.research.features.models import (
    FeatureLineage,
    FeatureValue,
    FeatureVersion,
)


FEATURE_BAR_INPUT = CN_FUTURES_FEATURE_BAR_V1

MOMENTUM_ROC = CanonicalFeatureDefinition(
    feature_id="momentum.roc",
    description="按同一 feature-ready 期货序列计算 N 个观测 bar 的简单动量。",
    input_contract=FEATURE_BAR_INPUT,
    required_columns=("close",),
    output_column="momentum_roc",
    lookback_semantics="N 为观测 bar 数；使用当前 close 与完整的前 N 个观测窗口，不按自然日补齐。",
    missing_value_semantics="预热为 lookback_not_ready；窗口内任一 close 缺失为 input_missing；非正或非有限 close 拒绝整次回填。",
    parameter_schema={"lookback_bars": {"type": "integer", "required": True, "minimum": 1}},
)


class MomentumRocComputer(CanonicalFeatureComputer):
    """``close_t / close_(t-N) - 1``，不跨 symbol，也不使用未来 bar。"""

    def __init__(self, version: FeatureVersion) -> None:
        super().__init__(version, MOMENTUM_ROC)

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
            rows, entity_key_columns=MOMENTUM_ROC.input_contract.entity_key_columns
        ):
            closes = [positive_number(row.values["close"], field_name="close") for row in group]
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
                window = closes[index - lookback : index + 1]
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
                values.append(
                    self._value(
                        lineage=lineage,
                        row=row,
                        value=current / baseline - 1.0,
                    )
                )
        return tuple(values)
