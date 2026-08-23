"""P4-WP14 canonical Registry definitions for evidence-backed intelligence features."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from northstar_quant.data.market.pit import MarketDataKind, MarketDataSnapshot
from northstar_quant.intelligence.feature_projection import (
    INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS,
    INTELLIGENCE_FEATURE_INPUT_PROVENANCE_COLUMNS,
    INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS,
    INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS,
    INTELLIGENCE_FEATURE_PROJECTION_SCHEMA_VERSION,
    INTELLIGENCE_METRIC_MISSING_REASONS,
)
from northstar_quant.research.features.canonical import (
    CanonicalFeatureComputer,
    CanonicalFeatureDefinition,
    FeatureInputContract,
    finite_number,
)
from northstar_quant.research.features.models import FeatureLineage, FeatureValue, FeatureVersion


INTELLIGENCE_EVENT_INPUT = FeatureInputContract(
    kind=MarketDataKind.SNAPSHOT,
    schema_version=INTELLIGENCE_FEATURE_PROJECTION_SCHEMA_VERSION,
    entity_key_columns=("commodity_id", "projection_observation_id"),
    event_time_column="event_time",
    available_at_column="available_at",
    value_columns=INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS,
)


def _definition(name: str, description: str) -> CanonicalFeatureDefinition:
    return CanonicalFeatureDefinition(
        feature_id=f"intelligence.{name}",
        description=description,
        input_contract=INTELLIGENCE_EVENT_INPUT,
        required_columns=INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS,
        output_column=name,
        lookback_semantics="每行是一个经过 Event、证据、本体、机制、影响和市场上下文谱系验证的 Feature Projection 观测；不读取未来事件或未来上下文。",
        missing_value_semantics=(
            "null metric input requires its paired explicit closed "
            "*_missing_reason code; populated metric input requires no reason."
        ),
        parameter_schema={},
    )


SUPPLY_RISK_1H = _definition("supply_risk_1h", "已知事件在一小时内的供应风险分数。")
SUPPLY_RISK_6H = _definition("supply_risk_6h", "已知事件在六小时内的供应风险分数。")
SUPPLY_RISK_24H = _definition("supply_risk_24h", "已知事件在二十四小时内的供应风险分数。")
DEMAND_SHOCK = _definition("demand_shock", "已知事件的需求冲击分数。")
GEOPOLITICAL_RISK = _definition("geopolitical_risk", "已知事件的地缘风险分数。")
INVENTORY_STRESS = _definition("inventory_stress", "已知事件与市场上下文共同形成的库存压力分数。")
EVENT_NOVELTY = _definition("event_novelty", "相对于可得历史类比事件的结构化新颖度分数。")
EVENT_CONFIDENCE = _definition("event_confidence", "Source、确认、抽取和实体解析共同决定的事件置信度。")
CONTEXTUAL_IMPACT = _definition("contextual_impact", "事件机制在已知市场上下文下的影响分数。")

INTELLIGENCE_FEATURE_DEFINITIONS: tuple[CanonicalFeatureDefinition, ...] = (
    SUPPLY_RISK_1H,
    SUPPLY_RISK_6H,
    SUPPLY_RISK_24H,
    DEMAND_SHOCK,
    GEOPOLITICAL_RISK,
    INVENTORY_STRESS,
    EVENT_NOVELTY,
    EVENT_CONFIDENCE,
    CONTEXTUAL_IMPACT,
)
_DEFINITIONS_BY_ID = {definition.feature_id: definition for definition in INTELLIGENCE_FEATURE_DEFINITIONS}


class IntelligenceMetricComputer(CanonicalFeatureComputer):
    """Materializes one explicit bounded score; it never emits a position or order."""

    def __init__(self, version: FeatureVersion) -> None:
        try:
            definition = _DEFINITIONS_BY_ID[version.feature_id]
        except KeyError as exc:
            raise ValueError("unsupported intelligence feature version") from exc
        super().__init__(version, definition)

    def compute(
        self,
        *,
        market_snapshot: MarketDataSnapshot,
        parameters: Mapping[str, object],
        lineage: FeatureLineage,
    ) -> Iterable[FeatureValue]:
        if parameters:
            raise ValueError("intelligence metric features do not accept parameters")
        values: list[FeatureValue] = []
        for row in self._rows(market_snapshot=market_snapshot, lineage=lineage):
            input_column = f"{self.definition.output_column}_input"
            missing_reason_column = f"{self.definition.output_column}_missing_reason"
            value = finite_number(row.values[input_column], field_name=input_column)
            if value is None:
                missing_reason = row.values[missing_reason_column]
                if (
                    not isinstance(missing_reason, str)
                    or missing_reason.strip() != missing_reason
                    or missing_reason not in INTELLIGENCE_METRIC_MISSING_REASONS
                ):
                    raise ValueError(
                        f"{missing_reason_column} must be an explicit closed missing-data code "
                        f"when {input_column} is null"
                    )
                values.append(
                    self._value(
                        lineage=lineage,
                        row=row,
                        value=None,
                        missing_reason=missing_reason,
                    )
                )
                continue
            if row.values[missing_reason_column] is not None:
                raise ValueError(
                    f"{missing_reason_column} must be null when {input_column} is populated"
                )
            if not 0 <= value <= 1:
                raise ValueError("intelligence feature scores must be in [0, 1]")
            values.append(self._value(lineage=lineage, row=row, value=value))
        return tuple(values)


__all__ = [
    "CONTEXTUAL_IMPACT",
    "DEMAND_SHOCK",
    "EVENT_CONFIDENCE",
    "EVENT_NOVELTY",
    "GEOPOLITICAL_RISK",
    "INTELLIGENCE_EVENT_INPUT",
    "INTELLIGENCE_FEATURE_DEFINITIONS",
    "INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS",
    "INTELLIGENCE_FEATURE_INPUT_PROVENANCE_COLUMNS",
    "INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS",
    "INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS",
    "INVENTORY_STRESS",
    "IntelligenceMetricComputer",
    "SUPPLY_RISK_1H",
    "SUPPLY_RISK_6H",
    "SUPPLY_RISK_24H",
]
