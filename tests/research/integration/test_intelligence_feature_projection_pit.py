"""P4 intelligence projection must remain an evidence-only P1/P2 input.

This integration boundary intentionally exercises the real P4 projector, the
application publisher, immutable P1 publication/PIT selection, and the
registered P2 feature/experiment paths.  It never builds a target, order, or
trading capability.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256

import polars as pl
import pytest

from northstar_quant.application.intelligence_feature_projection import (
    IntelligenceFeatureProjectionAdapterError,
    IntelligenceFeatureProjectionPublisher,
)
from northstar_quant.application.intelligence_feature_projection_evidence import (
    ImmutableIntelligenceFeatureProjectionEvidenceVerifier,
)
from northstar_quant.data_platform.artifacts.immutable_store import ArtifactStore
from northstar_quant.data_platform.market.pit import (
    MarketDataKind,
    MarketDataPITError,
    MarketDataPITSelector,
    MarketDataPITSpec,
    MarketDataSnapshot,
)
from northstar_quant.data_platform.sources.protocol import DataSourceAdapter
from northstar_quant.data_platform.sources.publisher import (
    DataSourcePublisher,
    PublishedSourceDataset,
    SourcePublicationSpec,
)
from northstar_quant.intelligence.feature_projection import (
    INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS,
    INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS,
    IntelligenceMetricKind,
    IntelligenceMetricValue,
)
from northstar_quant.research.experiments import ExperimentRegistry
from northstar_quant.research.features import FeatureRegistry, FeatureRegistryError
from northstar_quant.research.features.catalog import register_canonical_feature
from northstar_quant.research.features.intelligence import (
    EVENT_CONFIDENCE,
    INTELLIGENCE_EVENT_INPUT,
)
from tests.helpers.intelligence_feature_projection import (
    build_authorized_intelligence_feature_projection_fixture,
    publish_authorized_intelligence_feature_projection_fixture,
)


def _projection_spec() -> MarketDataPITSpec:
    return MarketDataPITSpec(
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


def _registered_event_confidence(store) -> tuple[FeatureRegistry, object]:
    registry = FeatureRegistry(artifact_store=store)
    version = register_canonical_feature(
        registry,
        feature_id=EVENT_CONFIDENCE.feature_id,
        version="1.0.0",
        code_revision="p8-wp02-projection-pit",
    )
    return registry, version


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class _NoReleasePublisher(DataSourcePublisher):
    """Records whether the app publisher reaches P1 after a preflight rejection."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store
        self.calls: list[tuple[DataSourceAdapter, SourcePublicationSpec, datetime]] = []

    def publish(
        self,
        *,
        adapter: DataSourceAdapter,
        spec: SourcePublicationSpec,
        released_at: datetime,
        allow_warn: bool = False,
        allow_unknown_for_noncritical: bool = False,
    ) -> PublishedSourceDataset:
        assert allow_warn is False
        assert allow_unknown_for_noncritical is False
        self.calls.append((adapter, spec, released_at))
        raise AssertionError("a rejected publication must not reach P1 artifact release")


def test_real_projection_publication_binds_p4_provenance_to_p1_pit_p2_and_experiment(
    tmp_path,
) -> None:
    fixture = publish_authorized_intelligence_feature_projection_fixture(tmp_path)
    projection = fixture.projection
    published = fixture.publication
    dataset = published.dataset.dataset_version
    as_of = projection.available_at + timedelta(minutes=1)

    snapshot = MarketDataPITSelector(fixture.store).select(
        dataset_version_hash=dataset.version_hash,
        spec=_projection_spec(),
        as_of=as_of,
    )
    expected_rows = [dict(row) for row in projection.as_feature_input_rows()]
    selected = snapshot.selected_frame()

    assert projection.eligible_for_trading is False
    assert tuple(selected.columns) == (
        *INTELLIGENCE_EVENT_INPUT.entity_key_columns,
        INTELLIGENCE_EVENT_INPUT.event_time_column,
        INTELLIGENCE_EVENT_INPUT.available_at_column,
        *(INTELLIGENCE_EVENT_INPUT.value_columns or ()),
    )
    assert selected.to_dicts() == expected_rows
    assert selected.to_dicts()[0]["projection_hash"] == projection.projection_hash
    assert snapshot.dataset_version_hash == dataset.version_hash
    assert snapshot.as_of == as_of
    assert snapshot.source_artifact_available_at == projection.available_at
    assert snapshot.publication_authorization_hash == published.assessed.authorization.authorization_hash
    assert snapshot.publication_scope == fixture.publication_spec.request.scope

    registry, version = _registered_event_confidence(fixture.store)
    lineage = registry.create_market_data_lineage(
        feature_version_hash=version.version_hash,
        market_snapshot=snapshot,
        parameters={},
    )
    backfill = registry.materialize_deterministic_backfill(lineage)
    frozen_input = ExperimentRegistry(feature_registry=registry).freeze_feature_input(
        lineage_hash=lineage.lineage_hash
    )

    evidence = lineage.dependencies[0].dataset_evidence
    assert evidence is not None
    assert version.feature_id == EVENT_CONFIDENCE.feature_id
    assert version.spec_hash == EVENT_CONFIDENCE.feature_spec().spec_hash
    assert lineage.feature_version_hash == version.version_hash
    assert evidence.dataset_version_hash == dataset.version_hash
    assert evidence.snapshot_id == snapshot.snapshot_id
    assert evidence.selected_frame_hash == snapshot.selected_frame_hash
    assert evidence.publication_authorization_hash == published.assessed.authorization.authorization_hash
    assert evidence.publication_scope == snapshot.as_manifest_mapping()["publication_scope"]
    assert backfill.values[0].value == pytest.approx(0.8)
    assert registry.materialize_deterministic_backfill(lineage) == backfill
    assert backfill.feature_version_hash == version.version_hash
    assert lineage.selection_mode == "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY"
    assert lineage.decision_time_safe is False
    assert backfill.selection_mode == lineage.selection_mode
    assert backfill.decision_time_safe is False
    assert frozen_input.dataset_version_hashes == (dataset.version_hash,)
    assert frozen_input.dataset_inputs[0].evidence.evidence_hash == evidence.evidence_hash
    assert frozen_input.source_selection_mode == lineage.selection_mode
    assert frozen_input.source_decision_time_safe is False


def test_future_projection_publication_timing_is_rejected_before_p1_artifact_release(
    tmp_path,
) -> None:
    fixture = build_authorized_intelligence_feature_projection_fixture(tmp_path)
    rejected_spec = replace(
        fixture.publication_spec,
        normalized_available_at=fixture.projection.available_at + timedelta(minutes=1),
    )
    before_files = tuple(sorted(path for path in fixture.store.root.rglob("*") if path.is_file()))
    no_release = _NoReleasePublisher(fixture.store)

    with pytest.raises(
        IntelligenceFeatureProjectionAdapterError,
        match="normalized availability",
    ):
        IntelligenceFeatureProjectionPublisher(
            data_source_publisher=no_release,
            evidence_verifier=ImmutableIntelligenceFeatureProjectionEvidenceVerifier(
                fixture.store
            ),
        ).publish(
            projection=fixture.projection,
            publication_spec=rejected_spec,
        )

    assert no_release.calls == []
    assert tuple(sorted(path for path in fixture.store.root.rglob("*") if path.is_file())) == before_files


def test_projection_fixture_p1_quality_policy_requires_the_full_closed_input_schema(
    tmp_path,
) -> None:
    fixture = build_authorized_intelligence_feature_projection_fixture(tmp_path)

    fixture.publish()

    assert len(fixture.quality_engine.requests) == 1
    request = fixture.quality_engine.requests[0]
    expected_columns = (
        *INTELLIGENCE_EVENT_INPUT.entity_key_columns,
        INTELLIGENCE_EVENT_INPUT.event_time_column,
        INTELLIGENCE_EVENT_INPUT.available_at_column,
        *(INTELLIGENCE_EVENT_INPUT.value_columns or ()),
    )
    nullable_columns = frozenset(
        {
            *INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS,
            *INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS,
        }
    )

    assert tuple(field.name for field in request.schema) == tuple(sorted(expected_columns))
    assert request.allow_additional_columns is False
    assert request.completeness.required_columns == tuple(
        column for column in expected_columns if column not in nullable_columns
    )
    assert {
        field.name for field in request.schema if field.nullable
    } == nullable_columns


def test_real_p4_to_p1_to_p2_path_preserves_the_explicit_metric_missing_reason(
    tmp_path,
) -> None:
    metric_values = tuple(
        IntelligenceMetricValue(
            kind=kind,
            score=(None if kind is IntelligenceMetricKind.EVENT_CONFIDENCE else 0.5),
            missing_reason=(
                "not_implemented"
                if kind is IntelligenceMetricKind.EVENT_CONFIDENCE
                else None
            ),
        )
        for kind in IntelligenceMetricKind
    )
    fixture = publish_authorized_intelligence_feature_projection_fixture(
        tmp_path,
        metric_values=metric_values,
    )
    snapshot = MarketDataPITSelector(fixture.store).select(
        dataset_version_hash=fixture.publication.dataset.dataset_version.version_hash,
        spec=_projection_spec(),
        as_of=fixture.projection.available_at + timedelta(minutes=1),
    )
    registry, version = _registered_event_confidence(fixture.store)
    lineage = registry.create_market_data_lineage(
        feature_version_hash=version.version_hash,
        market_snapshot=snapshot,
        parameters={},
    )
    backfill = registry.materialize_deterministic_backfill(lineage)

    assert backfill.values[0].value is None
    assert backfill.values[0].missing_reason == "not_implemented"


def test_wrong_p1_spec_is_rejected_before_feature_lineage_is_created(tmp_path) -> None:
    fixture = publish_authorized_intelligence_feature_projection_fixture(tmp_path)
    dataset = fixture.publication.dataset.dataset_version
    wrong_spec = MarketDataPITSpec(
        kind=MarketDataKind.SNAPSHOT,
        key_columns=(
            *INTELLIGENCE_EVENT_INPUT.entity_key_columns,
            INTELLIGENCE_EVENT_INPUT.event_time_column,
        ),
        event_time_column=INTELLIGENCE_EVENT_INPUT.event_time_column,
        available_at_column=INTELLIGENCE_EVENT_INPUT.available_at_column,
        value_columns=INTELLIGENCE_EVENT_INPUT.value_columns or (),
        schema_version="forged_intelligence_projection_v0",
    )

    with pytest.raises(MarketDataPITError):
        MarketDataPITSelector(fixture.store).select(
            dataset_version_hash=dataset.version_hash,
            spec=wrong_spec,
            as_of=fixture.projection.available_at + timedelta(minutes=1),
        )


def test_registry_rejects_forged_or_replaced_pit_snapshot_before_lineage(tmp_path) -> None:
    fixture = publish_authorized_intelligence_feature_projection_fixture(tmp_path)
    dataset = fixture.publication.dataset.dataset_version
    snapshot = MarketDataPITSelector(fixture.store).select(
        dataset_version_hash=dataset.version_hash,
        spec=_projection_spec(),
        as_of=fixture.projection.available_at + timedelta(minutes=1),
    )
    registry, version = _registered_event_confidence(fixture.store)
    common = {
        "dataset_id": snapshot.dataset_id,
        "source_artifact_snapshot_hash": snapshot.source_artifact_snapshot_hash,
        "source_id": snapshot.source_id,
        "source_config_sha256": snapshot.source_config_sha256,
        "publication_authorization_hash": snapshot.publication_authorization_hash,
        "publication_scope": snapshot.publication_scope,
        "spec": snapshot.spec,
        "source_artifact_available_at": snapshot.source_artifact_available_at,
        "as_of": snapshot.as_of,
    }
    forged = MarketDataSnapshot.from_selected_frame(
        dataset_version_hash=_hash("forged-dataset-version"),
        frame=snapshot.selected_frame(),
        **common,
    )
    replaced = MarketDataSnapshot.from_selected_frame(
        dataset_version_hash=snapshot.dataset_version_hash,
        frame=snapshot.selected_frame().with_columns(
            pl.lit(0.99).alias("event_confidence_input")
        ),
        **common,
    )

    for unsafe_snapshot in (forged, replaced):
        with pytest.raises(FeatureRegistryError):
            registry.create_market_data_lineage(
                feature_version_hash=version.version_hash,
                market_snapshot=unsafe_snapshot,
                parameters={},
            )
