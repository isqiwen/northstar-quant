"""受控的持仓结构 canonical feature。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from northstar_quant.data.market.pit import MarketDataKind, MarketDataSnapshot
from northstar_quant.research.features.canonical import (
    CanonicalFeatureComputer,
    CanonicalFeatureDefinition,
    FeatureInputContract,
    non_negative_number,
)
from northstar_quant.research.features.models import (
    FeatureLineage,
    FeatureValue,
    FeatureVersion,
)


POSITIONING_INPUT = FeatureInputContract(
    kind=MarketDataKind.SNAPSHOT,
    schema_version="cn_futures_positioning_v1",
    entity_key_columns=("product", "participant_group"),
    event_time_column="report_date",
    available_at_column="available_at",
)

NET_POSITION_RATIO = CanonicalFeatureDefinition(
    feature_id="positioning.net_position_ratio",
    description="指定产品和参与者组的净多头占总持仓量比率。",
    input_contract=POSITIONING_INPUT,
    required_columns=("long_open_interest", "short_open_interest", "total_open_interest"),
    output_column="net_position_ratio",
    lookback_semantics="不使用历史窗口；每份已发布的参与者持仓报告独立计算 (long-short)/total。",
    missing_value_semantics="任一输入缺失为 input_missing；total=0 为 invalid_position_denominator；long/short/total 为负数或非有限值时拒绝整次回填；已知组成超过 total 为 inconsistent_position_components；不使用 aggregate open_interest 冒充参与者仓位。",
    parameter_schema={},
)


class NetPositionRatioComputer(CanonicalFeatureComputer):
    """``(long_open_interest - short_open_interest) / total_open_interest``。"""

    def __init__(self, version: FeatureVersion) -> None:
        super().__init__(version, NET_POSITION_RATIO)

    def compute(
        self,
        *,
        market_snapshot: MarketDataSnapshot,
        parameters: Mapping[str, object],
        lineage: FeatureLineage,
    ) -> Iterable[FeatureValue]:
        if parameters:
            raise ValueError("positioning.net_position_ratio 不接受参数")
        rows = self._rows(market_snapshot=market_snapshot, lineage=lineage)
        values: list[FeatureValue] = []
        for row in rows:
            fields = row.values
            long_interest = non_negative_number(
                fields["long_open_interest"], field_name="long_open_interest"
            )
            short_interest = non_negative_number(
                fields["short_open_interest"], field_name="short_open_interest"
            )
            total_interest = non_negative_number(
                fields["total_open_interest"], field_name="total_open_interest"
            )
            if None in (long_interest, short_interest, total_interest):
                values.append(
                    self._value(
                        lineage=lineage,
                        row=row,
                        value=None,
                        missing_reason="input_missing",
                    )
                )
                continue
            assert isinstance(long_interest, float)
            assert isinstance(short_interest, float)
            assert isinstance(total_interest, float)
            if total_interest == 0:
                values.append(
                    self._value(
                        lineage=lineage,
                        row=row,
                        value=None,
                        missing_reason="invalid_position_denominator",
                    )
                )
                continue
            if long_interest > total_interest or short_interest > total_interest:
                values.append(
                    self._value(
                        lineage=lineage,
                        row=row,
                        value=None,
                        missing_reason="inconsistent_position_components",
                    )
                )
                continue
            values.append(
                self._value(
                    lineage=lineage,
                    row=row,
                    value=(long_interest - short_interest) / total_interest,
                )
            )
        return tuple(values)
