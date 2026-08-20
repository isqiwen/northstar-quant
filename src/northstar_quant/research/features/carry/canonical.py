"""受控的期限结构与 carry canonical feature。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
import math
from typing import cast

from northstar_quant.data_platform.market.pit import MarketDataKind, MarketDataSnapshot
from northstar_quant.data_platform.sources.protocol import PublicationScope
from northstar_quant.research.features.canonical import (
    CanonicalFeatureComputer,
    CanonicalFeatureDefinition,
    FeatureInputContract,
    actual_contract_id_in_scope,
    expiry_date,
    positive_number,
)
from northstar_quant.research.features.models import (
    FeatureLineage,
    FeatureRegistryError,
    FeatureValue,
    FeatureVersion,
)


CURVE_TRIPLET_INPUT = FeatureInputContract(
    kind=MarketDataKind.SNAPSHOT,
    schema_version="cn_futures_curve_triplet_v1",
    entity_key_columns=("product",),
    event_time_column="date",
    available_at_column="available_at",
    requires_actual_contract_data=True,
)

ANNUALIZED_ROLL_YIELD = CanonicalFeatureDefinition(
    feature_id="carry.annualized_roll_yield",
    description="以受授权实际近月和次近月结算价及到期日计算正值代表 backwardation 的年化滚动收益。",
    input_contract=CURVE_TRIPLET_INPUT,
    required_columns=(
        "near_contract_id",
        "next_contract_id",
        "near_settlement",
        "next_settlement",
        "near_expiry",
        "next_expiry",
    ),
    output_column="annualized_roll_yield",
    lookback_semantics="不使用历史窗口；每个 actual_contract_data=true 的已对齐近月/次近月曲线快照独立计算 ln(near/next) / 年化期限差。",
    missing_value_semantics="任一输入缺失为 input_missing；合约不不同、到期日不严格递增、已到期或价格非法为 invalid_curve_pair；不会猜测换月。",
    parameter_schema={},
)

TERM_STRUCTURE_SLOPE = CanonicalFeatureDefinition(
    feature_id="carry.term_structure_slope",
    description="对受授权实际近月、次近月和远月 log settlement 相对于到期年限做 OLS 的期限结构斜率。",
    input_contract=CURVE_TRIPLET_INPUT,
    required_columns=(
        "near_contract_id",
        "next_contract_id",
        "far_contract_id",
        "near_settlement",
        "next_settlement",
        "far_settlement",
        "near_expiry",
        "next_expiry",
        "far_expiry",
    ),
    output_column="term_structure_slope",
    lookback_semantics="不使用历史窗口；每个 actual_contract_data=true 的同日三点曲线快照独立回归，正斜率表示 contango。",
    missing_value_semantics="任一输入缺失为 input_missing；少于三个不同、未到期且严格递增的期限点或价格非法为 insufficient_or_invalid_curve。",
    parameter_schema={},
)


def _event_day(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _curve_pair(
    row: Mapping[str, object],
    *,
    scope: PublicationScope,
) -> tuple[float, float, date, date] | None:
    near_contract = actual_contract_id_in_scope(
        row["near_contract_id"],
        scope=scope,
        expected_product=row["product"],
        field_name="near_contract_id",
    )
    next_contract = actual_contract_id_in_scope(
        row["next_contract_id"],
        scope=scope,
        expected_product=row["product"],
        field_name="next_contract_id",
    )
    near_price = positive_number(row["near_settlement"], field_name="near_settlement")
    next_price = positive_number(row["next_settlement"], field_name="next_settlement")
    near_expiry = expiry_date(row["near_expiry"], field_name="near_expiry")
    next_expiry = expiry_date(row["next_expiry"], field_name="next_expiry")
    if None in (near_contract, next_contract, near_price, next_price, near_expiry, next_expiry):
        return None
    assert isinstance(near_contract, str) and isinstance(next_contract, str)
    assert isinstance(near_price, float) and isinstance(next_price, float)
    assert isinstance(near_expiry, date) and isinstance(next_expiry, date)
    if (
        near_contract == next_contract
        or near_contract.split(".", 1)[0] != next_contract.split(".", 1)[0]
        or near_expiry >= next_expiry
    ):
        raise ValueError("invalid_curve_pair")
    return near_price, next_price, near_expiry, next_expiry


class AnnualizedRollYieldComputer(CanonicalFeatureComputer):
    """正值表示 near settlement 高于 next settlement 的 backwardation。"""

    def __init__(self, version: FeatureVersion) -> None:
        super().__init__(version, ANNUALIZED_ROLL_YIELD)

    def compute(
        self,
        *,
        market_snapshot: MarketDataSnapshot,
        parameters: Mapping[str, object],
        lineage: FeatureLineage,
    ) -> Iterable[FeatureValue]:
        if parameters:
            raise ValueError("carry.annualized_roll_yield 不接受参数")
        rows = self._rows(market_snapshot=market_snapshot, lineage=lineage)
        values: list[FeatureValue] = []
        for row in rows:
            try:
                pair = _curve_pair(row.values, scope=market_snapshot.publication_scope)
            except ValueError:
                values.append(
                    self._value(
                        lineage=lineage,
                        row=row,
                        value=None,
                        missing_reason="invalid_curve_pair",
                    )
                )
                continue
            if pair is None:
                values.append(
                    self._value(
                        lineage=lineage,
                        row=row,
                        value=None,
                        missing_reason="input_missing",
                    )
                )
                continue
            near_price, next_price, near_expiry, next_expiry = pair
            event_day = _event_day(row.event_time)
            maturity_days = (next_expiry - near_expiry).days
            if near_expiry <= event_day or maturity_days <= 0:
                values.append(
                    self._value(
                        lineage=lineage,
                        row=row,
                        value=None,
                        missing_reason="invalid_curve_pair",
                    )
                )
                continue
            values.append(
                self._value(
                    lineage=lineage,
                    row=row,
                    value=math.log(near_price / next_price) / (maturity_days / 365.25),
                )
            )
        return tuple(values)


class TermStructureSlopeComputer(CanonicalFeatureComputer):
    """三点 OLS slope；不把 carry 的相反数伪装成独立期限结构特征。"""

    def __init__(self, version: FeatureVersion) -> None:
        super().__init__(version, TERM_STRUCTURE_SLOPE)

    def compute(
        self,
        *,
        market_snapshot: MarketDataSnapshot,
        parameters: Mapping[str, object],
        lineage: FeatureLineage,
    ) -> Iterable[FeatureValue]:
        if parameters:
            raise ValueError("carry.term_structure_slope 不接受参数")
        rows = self._rows(market_snapshot=market_snapshot, lineage=lineage)
        values: list[FeatureValue] = []
        for row in rows:
            fields = row.values
            try:
                contracts = [
                    actual_contract_id_in_scope(
                        fields[column],
                        scope=market_snapshot.publication_scope,
                        expected_product=row.key["product"],
                        field_name=column,
                    )
                    for column in ("near_contract_id", "next_contract_id", "far_contract_id")
                ]
                prices = [
                    positive_number(fields[column], field_name=column)
                    for column in ("near_settlement", "next_settlement", "far_settlement")
                ]
                expiries = [
                    expiry_date(fields[column], field_name=column)
                    for column in ("near_expiry", "next_expiry", "far_expiry")
                ]
            except FeatureRegistryError:
                values.append(
                    self._value(
                        lineage=lineage,
                        row=row,
                        value=None,
                        missing_reason="insufficient_or_invalid_curve",
                    )
                )
                continue
            if any(value is None for value in (*contracts, *prices, *expiries)):
                values.append(
                    self._value(
                        lineage=lineage,
                        row=row,
                        value=None,
                        missing_reason="input_missing",
                    )
                )
                continue
            contract_values = tuple(cast(str, value) for value in contracts)
            price_values = tuple(cast(float, value) for value in prices)
            expiry_values = tuple(cast(date, value) for value in expiries)
            maturity_days = [(expiry - _event_day(row.event_time)).days for expiry in expiry_values]
            if (
                len(set(contract_values)) != 3
                or len({contract.split(".", 1)[0] for contract in contract_values}) != 1
                or not (expiry_values[0] < expiry_values[1] < expiry_values[2])
                or any(days <= 0 for days in maturity_days)
            ):
                values.append(
                    self._value(
                        lineage=lineage,
                        row=row,
                        value=None,
                        missing_reason="insufficient_or_invalid_curve",
                    )
                )
                continue
            x_values = [days / 365.25 for days in maturity_days]
            y_values = [math.log(price) for price in price_values]
            mean_x = sum(x_values) / len(x_values)
            denominator = sum((item - mean_x) ** 2 for item in x_values)
            if denominator == 0:  # defensive; strict expiry ordering should already preclude it.
                values.append(
                    self._value(
                        lineage=lineage,
                        row=row,
                        value=None,
                        missing_reason="insufficient_or_invalid_curve",
                    )
                )
                continue
            mean_y = sum(y_values) / len(y_values)
            slope = (
                sum(
                    (x_value - mean_x) * (y_value - mean_y)
                    for x_value, y_value in zip(x_values, y_values, strict=True)
                )
                / denominator
            )
            values.append(self._value(lineage=lineage, row=row, value=slope))
        return tuple(values)
