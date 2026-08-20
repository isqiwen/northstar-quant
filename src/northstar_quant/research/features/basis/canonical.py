"""受控的现货—实际期货基差 canonical feature。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from northstar_quant.data_platform.market.pit import MarketDataKind, MarketDataSnapshot
from northstar_quant.research.features.canonical import (
    CanonicalFeatureComputer,
    CanonicalFeatureDefinition,
    FeatureInputContract,
    actual_contract_id_in_scope,
    positive_number,
    required_text_value,
)
from northstar_quant.research.features.models import (
    FeatureLineage,
    FeatureRegistryError,
    FeatureValue,
    FeatureVersion,
)


BASIS_DAILY_INPUT = FeatureInputContract(
    kind=MarketDataKind.SNAPSHOT,
    schema_version="cn_futures_basis_daily_v1",
    entity_key_columns=("product",),
    event_time_column="date",
    available_at_column="available_at",
    requires_actual_contract_data=True,
)

RELATIVE_BASIS = CanonicalFeatureDefinition(
    feature_id="basis.relative_basis",
    description="同币种、同单位的受授权实际期货结算价相对已对齐现货价格的基差。",
    input_contract=BASIS_DAILY_INPUT,
    required_columns=(
        "spot_price",
        "futures_settlement",
        "futures_contract_id",
        "spot_unit",
        "futures_unit",
        "spot_currency",
        "futures_currency",
    ),
    output_column="relative_basis",
    lookback_semantics="不使用历史窗口；只计算同一明确报告时点、actual_contract_data=true 的已对齐现货与实际期货快照。",
    missing_value_semantics="输入缺失为 input_missing；单位或币种不一致为 unit_or_currency_mismatch；连续合约代码或非法价格为 invalid_basis_input；不会以连续 close 替代 spot。",
    parameter_schema={},
)


class RelativeBasisComputer(CanonicalFeatureComputer):
    """``futures_settlement / spot_price - 1``，不隐式做单位或币种换算。"""

    def __init__(self, version: FeatureVersion) -> None:
        super().__init__(version, RELATIVE_BASIS)

    def compute(
        self,
        *,
        market_snapshot: MarketDataSnapshot,
        parameters: Mapping[str, object],
        lineage: FeatureLineage,
    ) -> Iterable[FeatureValue]:
        if parameters:
            raise ValueError("basis.relative_basis 不接受参数")
        rows = self._rows(market_snapshot=market_snapshot, lineage=lineage)
        values: list[FeatureValue] = []
        for row in rows:
            fields = row.values
            try:
                spot = positive_number(fields["spot_price"], field_name="spot_price")
                futures = positive_number(
                    fields["futures_settlement"], field_name="futures_settlement"
                )
                contract = actual_contract_id_in_scope(
                    fields["futures_contract_id"],
                    scope=market_snapshot.publication_scope,
                    expected_product=row.key["product"],
                    field_name="futures_contract_id",
                )
                spot_unit = required_text_value(fields["spot_unit"], field_name="spot_unit")
                futures_unit = required_text_value(
                    fields["futures_unit"], field_name="futures_unit"
                )
                spot_currency = required_text_value(
                    fields["spot_currency"], field_name="spot_currency"
                )
                futures_currency = required_text_value(
                    fields["futures_currency"], field_name="futures_currency"
                )
            except FeatureRegistryError:
                values.append(
                    self._value(
                        lineage=lineage,
                        row=row,
                        value=None,
                        missing_reason="invalid_basis_input",
                    )
                )
                continue
            if None in (
                spot,
                futures,
                contract,
                spot_unit,
                futures_unit,
                spot_currency,
                futures_currency,
            ):
                values.append(
                    self._value(
                        lineage=lineage,
                        row=row,
                        value=None,
                        missing_reason="input_missing",
                    )
                )
                continue
            assert isinstance(spot, float) and isinstance(futures, float)
            assert isinstance(contract, str)
            assert isinstance(spot_unit, str) and isinstance(futures_unit, str)
            assert isinstance(spot_currency, str) and isinstance(futures_currency, str)
            if spot_unit != futures_unit or spot_currency != futures_currency:
                values.append(
                    self._value(
                        lineage=lineage,
                        row=row,
                        value=None,
                        missing_reason="unit_or_currency_mismatch",
                    )
                )
                continue
            values.append(self._value(lineage=lineage, row=row, value=futures / spot - 1.0))
        return tuple(values)
