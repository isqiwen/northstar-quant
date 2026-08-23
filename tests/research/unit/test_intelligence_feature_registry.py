from datetime import UTC, datetime
from hashlib import sha256

import polars as pl
import pytest

from northstar_quant.data_platform.market.pit import MarketDataPITSpec, MarketDataSnapshot
from northstar_quant.data_platform.sources.protocol import PublicationPurpose, PublicationScope
from northstar_quant.research.features import FeatureRegistry, register_canonical_feature
from northstar_quant.research.features.intelligence import (
    EVENT_CONFIDENCE,
    INTELLIGENCE_EVENT_INPUT,
    INTELLIGENCE_FEATURE_DEFINITIONS,
    INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS,
    INTELLIGENCE_FEATURE_INPUT_PROVENANCE_COLUMNS,
    INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS,
    INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS,
    IntelligenceMetricComputer,
)
from northstar_quant.research.features.models import FeatureDependency, FeatureLineage
from northstar_quant.intelligence.feature_projection import (
    INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS as P4_INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS,
    INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS as P4_INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS,
    INTELLIGENCE_FEATURE_PROJECTION_SCHEMA_VERSION,
)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _event_confidence_snapshot(
    *,
    score: object,
    missing_reason: object,
) -> MarketDataSnapshot:
    event_time = datetime(2026, 8, 22, 10, tzinfo=UTC)
    available_at = datetime(2026, 8, 22, 10, 15, tzinfo=UTC)
    row: dict[str, object] = {
        "commodity_id": "copper",
        "projection_observation_id": "ifpobs-unit-1",
        "event_time": event_time,
        "available_at": available_at,
    }
    for column in INTELLIGENCE_FEATURE_INPUT_PROVENANCE_COLUMNS:
        row[column] = (
            "ontology-v1" if column == "ontology_version" else _hash(column)
        )
    row.update({column: 0.5 for column in INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS})
    row.update(
        {column: None for column in INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS}
    )
    row["event_confidence_input"] = score
    row["event_confidence_missing_reason"] = missing_reason
    casts = [
        pl.col("event_time").cast(pl.Datetime("us", "UTC")),
        pl.col("available_at").cast(pl.Datetime("us", "UTC")),
        *(
            pl.col(column).cast(pl.String)
            for column in INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS
        ),
    ]
    if score is None or (
        isinstance(score, (int, float)) and not isinstance(score, bool)
    ):
        casts.append(pl.col("event_confidence_input").cast(pl.Float64))
    frame = pl.DataFrame([row]).with_columns(*casts)
    spec = MarketDataPITSpec(
        kind=INTELLIGENCE_EVENT_INPUT.kind,
        key_columns=(
            *INTELLIGENCE_EVENT_INPUT.entity_key_columns,
            INTELLIGENCE_EVENT_INPUT.event_time_column,
        ),
        event_time_column=INTELLIGENCE_EVENT_INPUT.event_time_column,
        available_at_column=INTELLIGENCE_EVENT_INPUT.available_at_column,
        value_columns=INTELLIGENCE_EVENT_INPUT.value_columns or (),
        schema_version=INTELLIGENCE_EVENT_INPUT.schema_version,
    )
    return MarketDataSnapshot.from_selected_frame(
        dataset_id="intelligence_unit_fixture",
        dataset_version_hash=_hash("intelligence-unit-dataset"),
        source_artifact_snapshot_hash=_hash("intelligence-unit-artifact"),
        source_id="intelligence_unit_source",
        source_config_sha256=_hash("intelligence-unit-source-config"),
        publication_authorization_hash=_hash("intelligence-unit-publication"),
        publication_scope=PublicationScope(
            dataset_id="intelligence_unit_fixture",
            market="CN",
            asset_type="FUTURES",
            frequency="snapshot",
            purpose=PublicationPurpose.INTERNAL_RESEARCH,
            environment="test",
            actual_contract_data=False,
        ),
        spec=spec,
        source_artifact_available_at=available_at,
        as_of=available_at,
        frame=frame,
    )


def _compute_event_confidence(*, score: object, missing_reason: object):
    version = EVENT_CONFIDENCE.feature_version(
        version="1.0.0",
        code_revision="p8-wp02-missing-reason-unit",
    )
    snapshot = _event_confidence_snapshot(score=score, missing_reason=missing_reason)
    lineage = FeatureLineage.create(
        feature_version=version,
        dependencies=(
            FeatureDependency.from_market_data_snapshot(
                role="market_data",
                snapshot=snapshot,
            ),
        ),
        parameters={},
        decision_at=snapshot.as_of,
        available_at=snapshot.as_of,
    )
    return tuple(
        IntelligenceMetricComputer(version).compute(
            market_snapshot=snapshot,
            parameters={},
            lineage=lineage,
        )
    )


def test_intelligence_features_require_the_exact_v2_projection_input_contract() -> None:
    assert INTELLIGENCE_EVENT_INPUT.schema_version == INTELLIGENCE_FEATURE_PROJECTION_SCHEMA_VERSION
    assert INTELLIGENCE_EVENT_INPUT.entity_key_columns == (
        "commodity_id",
        "projection_observation_id",
    )
    assert INTELLIGENCE_EVENT_INPUT.event_time_column == "event_time"
    assert INTELLIGENCE_EVENT_INPUT.available_at_column == "available_at"
    assert INTELLIGENCE_FEATURE_INPUT_PROVENANCE_COLUMNS == (
        "event_hash",
        "evidence_bundle_hash",
        "ontology_version",
        "mechanism_identity_hash",
        "impact_identity_hash",
        "context_identity_hash",
        "context_dataset_version_hash",
        "context_publication_receipt_hash",
        "projection_hash",
    )
    assert INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS == (
        "supply_risk_1h_input",
        "supply_risk_6h_input",
        "supply_risk_24h_input",
        "demand_shock_input",
        "geopolitical_risk_input",
        "inventory_stress_input",
        "event_novelty_input",
        "event_confidence_input",
        "contextual_impact_input",
    )
    assert INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS == (
        "supply_risk_1h_missing_reason",
        "supply_risk_6h_missing_reason",
        "supply_risk_24h_missing_reason",
        "demand_shock_missing_reason",
        "geopolitical_risk_missing_reason",
        "inventory_stress_missing_reason",
        "event_novelty_missing_reason",
        "event_confidence_missing_reason",
        "contextual_impact_missing_reason",
    )
    assert (
        INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS
        == P4_INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS
    )
    assert INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS == (
        *INTELLIGENCE_FEATURE_INPUT_PROVENANCE_COLUMNS,
        *INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS,
        *INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS,
    )
    assert INTELLIGENCE_EVENT_INPUT.value_columns == INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS
    assert INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS == P4_INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS

    expected_input_columns = {
        "commodity_id",
        "projection_observation_id",
        "event_time",
        "available_at",
        *INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS,
    }
    assert all(
        definition.required_columns == INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS
        and set(definition.feature_spec().input_columns) == expected_input_columns
        for definition in INTELLIGENCE_FEATURE_DEFINITIONS
    )


def test_all_intelligence_feature_definitions_are_explicitly_registered_in_the_feature_registry():
    registry = FeatureRegistry()
    versions = tuple(register_canonical_feature(registry, feature_id=definition.feature_id, version="1.0.0", code_revision="p4-wp14") for definition in INTELLIGENCE_FEATURE_DEFINITIONS)
    assert {version.feature_id for version in versions} == {
        "intelligence.supply_risk_1h",
        "intelligence.supply_risk_6h",
        "intelligence.supply_risk_24h",
        "intelligence.demand_shock",
        "intelligence.geopolitical_risk",
        "intelligence.inventory_stress",
        "intelligence.event_novelty",
        "intelligence.event_confidence",
        "intelligence.contextual_impact",
    }
    assert {spec.feature_id for spec in registry.list_specs()} == {definition.feature_id for definition in INTELLIGENCE_FEATURE_DEFINITIONS}
    assert all(version.parameter_schema == {} for version in versions)


def test_intelligence_metric_computer_preserves_the_explicit_paired_missing_reason():
    values = _compute_event_confidence(
        score=None,
        missing_reason="not_implemented",
    )

    assert len(values) == 1
    assert values[0].value is None
    assert values[0].missing_reason == "not_implemented"


def test_intelligence_metric_computer_requires_no_reason_for_a_populated_score():
    values = _compute_event_confidence(score=0.8, missing_reason=None)

    assert len(values) == 1
    assert values[0].value == pytest.approx(0.8)
    assert values[0].missing_reason is None


@pytest.mark.parametrize(
    ("score", "missing_reason"),
    (
        pytest.param(None, None, id="null-score-missing-reason"),
        pytest.param(None, "", id="null-score-empty-reason"),
        pytest.param(None, "unrecognized", id="null-score-unknown-reason"),
        pytest.param(None, " not_available", id="null-score-untrimmed-reason"),
        pytest.param(0.8, "input_missing", id="score-with-reason"),
        pytest.param(0.8, "", id="score-with-empty-reason"),
        pytest.param("0.8", None, id="non-numeric-score"),
        pytest.param(float("nan"), None, id="nan-score"),
        pytest.param(1.01, None, id="out-of-range-score"),
    ),
)
def test_intelligence_metric_computer_fails_closed_for_malformed_score_reason_pairs(
    score: object,
    missing_reason: object,
):
    with pytest.raises(ValueError):
        _compute_event_confidence(score=score, missing_reason=missing_reason)
