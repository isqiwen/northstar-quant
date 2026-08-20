"""受控的库存 canonical feature。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from northstar_quant.data_platform.market.pit import MarketDataKind, MarketDataSnapshot
from northstar_quant.research.features.canonical import (
    CanonicalFeatureComputer,
    CanonicalFeatureDefinition,
    FeatureInputContract,
    integer_parameter,
    non_negative_number,
)
from northstar_quant.research.features.models import (
    FeatureLineage,
    FeatureValue,
    FeatureVersion,
)


INVENTORY_INPUT = FeatureInputContract(
    kind=MarketDataKind.SNAPSHOT,
    schema_version="cn_futures_inventory_v1",
    entity_key_columns=("product", "inventory_scope", "inventory_unit"),
    event_time_column="observation_date",
    available_at_column="available_at",
)

INVENTORY_LEVEL_CHANGE = CanonicalFeatureDefinition(
    feature_id="inventory.level_change",
    description="同一产品、库存范围和单位下的库存水平变化率。",
    input_contract=INVENTORY_INPUT,
    required_columns=("inventory_level",),
    output_column="inventory_level_change",
    lookback_semantics="lookback_observations 是报告观测次数，不是自然日或交易日；只比较当前报告和前 N 个同范围同单位报告。",
    missing_value_semantics="预热为 lookback_not_ready；窗口内库存缺失为 input_missing；基准库存为零为 zero_baseline_inventory；负数或非有限库存拒绝整次回填。",
    parameter_schema={"lookback_observations": {"type": "integer", "required": True, "minimum": 1}},
)


class InventoryLevelChangeComputer(CanonicalFeatureComputer):
    """``inventory_t / inventory_(t-N) - 1``，不对缺失报告做前填。"""

    def __init__(self, version: FeatureVersion) -> None:
        super().__init__(version, INVENTORY_LEVEL_CHANGE)

    def compute(
        self,
        *,
        market_snapshot: MarketDataSnapshot,
        parameters: Mapping[str, object],
        lineage: FeatureLineage,
    ) -> Iterable[FeatureValue]:
        lookback = integer_parameter(parameters, "lookback_observations", minimum=1)
        rows = self._rows(market_snapshot=market_snapshot, lineage=lineage)
        values: list[FeatureValue] = []
        for group in self._groups(
            rows, entity_key_columns=INVENTORY_LEVEL_CHANGE.input_contract.entity_key_columns
        ):
            levels = [
                non_negative_number(row.values["inventory_level"], field_name="inventory_level")
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
                window = levels[index - lookback : index + 1]
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
                            missing_reason="zero_baseline_inventory",
                        )
                    )
                    continue
                values.append(self._value(lineage=lineage, row=row, value=current / baseline - 1.0))
        return tuple(values)
