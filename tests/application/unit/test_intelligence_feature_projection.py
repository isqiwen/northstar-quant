from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import cast

import polars as pl
import pytest

from northstar_quant.application.intelligence_feature_projection import (
    IntelligenceFeatureProjectionAdapter,
    IntelligenceFeatureProjectionAdapterError,
    IntelligenceFeatureProjectionPublisher,
)
from northstar_quant.application.intelligence_feature_projection_evidence import (
    ImmutableIntelligenceFeatureProjectionEvidenceVerifier,
)
from northstar_quant.data.artifacts.immutable_store import ArtifactStore
from northstar_quant.data.sources.protocol import (
    DataSourceAdapter,
    PublicationPurpose,
    PublicationScope,
    SourceFetchRequest,
)
from northstar_quant.data.sources.publisher import (
    DataSourcePublisher,
    PublishedSourceDataset,
    SourcePublicationSpec,
)
from northstar_quant.intelligence.context import MarketContextSnapshot
from northstar_quant.intelligence.domain import Event, Evidence, Impact, Mechanism
from northstar_quant.intelligence.feature_projection import (
    AuthorizedMarketContext,
    EventEvidenceAvailability,
    INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS,
    INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS,
    IntelligenceFeatureProjectionRequest,
    IntelligenceFeatureProjector,
    IntelligenceMetricKind,
    IntelligenceMetricValue,
    VersionedIntelligenceFeatureProjection,
)
from northstar_quant.intelligence.ontology import load_ontology
from northstar_quant.research.features.intelligence import INTELLIGENCE_EVENT_INPUT
from tests.helpers.intelligence_feature_projection import (
    build_authorized_intelligence_feature_projection_fixture,
)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _projection() -> VersionedIntelligenceFeatureProjection:
    ontology = load_ontology(Path("ontology"))
    event = Event(
        event_id="event-copper-outage-1",
        ontology_version=ontology.version,
        evidence=(
            Evidence("document-one", _hash("document-one"), 4, 23),
            Evidence("document-two", _hash("document-two"), 9, 31),
        ),
        mechanism=Mechanism("SUPPLY_REDUCTION", ontology.version),
        impacts=(Impact("impact-copper-1", "copper", "UP"),),
    )
    context = MarketContextSnapshot(
        snapshot_id="context-copper-1",
        commodity_id="copper",
        market_id="shfe",
        dataset_version=_hash("market-context-dataset-v1"),
        as_of=datetime(2026, 8, 22, 9, tzinfo=UTC),
        available_at=datetime(2026, 8, 22, 9, 5, tzinfo=UTC),
        inventory=120.0,
        term_structure=-0.1,
        basis=5.0,
        positioning=0.25,
        volatility=0.18,
        usd=100.0,
        cny=7.2,
        macro_regime="slowdown",
        seasonality="Q3",
    )
    event_evidence = (
        EventEvidenceAvailability(
            "document-one",
            _hash("document-one"),
            4,
            23,
            datetime(2026, 8, 22, 10, 5, tzinfo=UTC),
            _hash("document-one-publication-receipt"),
            _hash("document-one-source-artifact-snapshot"),
        ),
        EventEvidenceAvailability(
            "document-two",
            _hash("document-two"),
            9,
            31,
            datetime(2026, 8, 22, 10, 6, tzinfo=UTC),
            _hash("document-two-publication-receipt"),
            _hash("document-two-source-artifact-snapshot"),
        ),
    )
    authorized_context = AuthorizedMarketContext(
        context,
        _hash("market-context-dataset-v1"),
        _hash("market-context-publication-receipt"),
        _hash("market-context-artifact-snapshot"),
    )
    request = IntelligenceFeatureProjectionRequest(
        projection_version="p4-intelligence-feature-v3",
        ontology=ontology,
        event=event,
        mechanism=event.mechanism,
        selected_impact=event.impacts[0],
        event_evidence=event_evidence,
        authorized_market_context=authorized_context,
        event_time=datetime(2026, 8, 22, 10, tzinfo=UTC),
        available_at=datetime(2026, 8, 22, 10, 15, tzinfo=UTC),
        metric_values=tuple(
            IntelligenceMetricValue(kind=kind, score=(index + 1) / 10)
            for index, kind in enumerate(IntelligenceMetricKind)
        ),
        publication_receipt_hashes=tuple(
            sorted(
                {
                    *(item.source_publication_receipt_hash for item in event_evidence),
                    authorized_context.context_publication_receipt_hash,
                }
            )
        ),
    )
    return IntelligenceFeatureProjector().project(request)


def _scope(
    *,
    purpose: PublicationPurpose = PublicationPurpose.INTERNAL_RESEARCH,
) -> PublicationScope:
    return PublicationScope(
        dataset_id="intelligence-feature-projection-v3",
        market="GLOBAL",
        asset_type="INTELLIGENCE",
        frequency="event",
        purpose=purpose,
        environment="test",
    )


def _publication_spec(
    projection: VersionedIntelligenceFeatureProjection,
    *,
    normalized_available_at: datetime | None = None,
    purpose: PublicationPurpose = PublicationPurpose.INTERNAL_RESEARCH,
) -> SourcePublicationSpec:
    normalized_available_at = normalized_available_at or projection.available_at
    acquired_at = normalized_available_at - timedelta(minutes=1)
    return SourcePublicationSpec(
        request=SourceFetchRequest(
            source_id="intelligence-feature-projection-source",
            scope=_scope(purpose=purpose),
            request_reference="intelligence-feature-projection-test",
            requested_at=acquired_at - timedelta(minutes=1),
        ),
        acquired_at=acquired_at,
        normalized_available_at=normalized_available_at,
        checked_at=normalized_available_at,
        decision_at=normalized_available_at,
        raw_artifact_id="intelligence-feature-projection-raw-v3",
        normalized_artifact_id="intelligence-feature-projection-normalized-v3",
        quality_request_builder=_UnusedQualityBuilder(),
        dataset_transform_version="intelligence-feature-projection-dataset-v3",
    )


class _UnusedQualityBuilder:
    def build(self, **_: object) -> object:
        raise AssertionError("the composition boundary must not invoke the quality builder")


class _RecordingPublisher(DataSourcePublisher):
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store
        self.calls: list[tuple[object, object, datetime]] = []
        self.result = object()

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
        return cast(PublishedSourceDataset, self.result)


def test_adapter_replays_only_the_frozen_receipt_as_the_exact_p1_input_table() -> None:
    projection = _projection()
    adapter = IntelligenceFeatureProjectionAdapter(projection=projection)
    request = _publication_spec(projection).request

    assert isinstance(adapter, DataSourceAdapter)
    assert adapter.eligible_for_trading is False
    assert adapter.available_at == projection.available_at

    metadata = adapter.metadata(request.scope)
    capture = adapter.fetch(request)
    normalized = adapter.normalize(capture.payload, metadata=metadata)

    expected_columns = (
        *INTELLIGENCE_EVENT_INPUT.entity_key_columns,
        INTELLIGENCE_EVENT_INPUT.event_time_column,
        INTELLIGENCE_EVENT_INPUT.available_at_column,
        *INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS,
    )
    frame = normalized.frame
    assert tuple(frame.columns) == expected_columns
    assert frame.schema[INTELLIGENCE_EVENT_INPUT.event_time_column] == pl.Datetime("us", "UTC")
    assert frame.schema[INTELLIGENCE_EVENT_INPUT.available_at_column] == pl.Datetime("us", "UTC")
    assert frame.to_dicts() == [
        {
            "commodity_id": "copper",
            "projection_observation_id": projection.observations[0].projection_observation_id,
            "event_time": datetime(2026, 8, 22, 10, tzinfo=UTC),
            "available_at": projection.available_at,
            "event_hash": projection.observations[0].event_hash,
            "evidence_bundle_hash": projection.observations[0].evidence_bundle_hash,
            "ontology_version": "v1",
            "mechanism_identity_hash": projection.observations[0].mechanism_identity_hash,
            "impact_identity_hash": projection.observations[0].impact_identity_hash,
            "context_identity_hash": projection.observations[0].context_identity_hash,
            "context_dataset_version_hash": projection.observations[0].context_dataset_version_hash,
            "context_publication_receipt_hash": (
                projection.observations[0].context_publication_receipt_hash
            ),
            "projection_hash": projection.projection_hash,
            "supply_risk_1h_input": 0.1,
            "supply_risk_6h_input": 0.2,
            "supply_risk_24h_input": 0.3,
            "demand_shock_input": 0.4,
            "geopolitical_risk_input": 0.5,
            "inventory_stress_input": 0.6,
            "event_novelty_input": 0.7,
            "event_confidence_input": 0.8,
            "contextual_impact_input": 0.9,
            **{
                column: None
                for column in INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS
            },
        }
    ]


def test_adapter_rejects_payload_that_is_not_the_exact_frozen_receipt() -> None:
    projection = _projection()
    adapter = IntelligenceFeatureProjectionAdapter(projection=projection)
    request = _publication_spec(projection).request
    metadata = adapter.metadata(request.scope)

    with pytest.raises(IntelligenceFeatureProjectionAdapterError, match="raw payload"):
        adapter.normalize(b"different-receipt", metadata=metadata)


def test_composition_delegates_once_to_the_injected_p1_publisher(tmp_path) -> None:
    fixture = build_authorized_intelligence_feature_projection_fixture(tmp_path)
    projection = fixture.projection
    publication_spec = fixture.publication_spec
    data_source_publisher = _RecordingPublisher(fixture.store)

    result = IntelligenceFeatureProjectionPublisher(
        data_source_publisher=data_source_publisher,
        evidence_verifier=ImmutableIntelligenceFeatureProjectionEvidenceVerifier(
            fixture.store
        ),
    ).publish(
        projection=projection,
        publication_spec=publication_spec,
    )

    assert result is data_source_publisher.result
    assert len(data_source_publisher.calls) == 1
    adapter, received_spec, released_at = data_source_publisher.calls[0]
    assert isinstance(adapter, IntelligenceFeatureProjectionAdapter)
    assert isinstance(adapter, DataSourceAdapter)
    assert received_spec is publication_spec
    assert released_at == projection.available_at


def _tamper_metric(projection: VersionedIntelligenceFeatureProjection) -> None:
    observation = projection.observations[0]
    values = list(observation.metric_values)
    values[0] = IntelligenceMetricValue(kind=values[0].kind, score=0.99)
    object.__setattr__(observation, "metric_values", tuple(values))


def _tamper_provenance(projection: VersionedIntelligenceFeatureProjection) -> None:
    object.__setattr__(projection.observations[0], "event_hash", _hash("forged-event"))


def _tamper_projection_hash(projection: VersionedIntelligenceFeatureProjection) -> None:
    object.__setattr__(projection, "projection_hash", _hash("forged-projection"))


def _tamper_observation_hash(projection: VersionedIntelligenceFeatureProjection) -> None:
    object.__setattr__(projection.observations[0], "observation_hash", _hash("forged-observation"))


@pytest.mark.parametrize(
    "tamper",
    (
        pytest.param(_tamper_metric, id="metric"),
        pytest.param(_tamper_provenance, id="provenance"),
        pytest.param(_tamper_projection_hash, id="projection-hash"),
        pytest.param(_tamper_observation_hash, id="observation-hash"),
    ),
)
def test_adapter_fails_closed_when_a_projected_dto_changes_after_construction(tamper) -> None:
    projection = _projection()
    adapter = IntelligenceFeatureProjectionAdapter(projection=projection)

    tamper(projection)

    with pytest.raises(IntelligenceFeatureProjectionAdapterError, match="P4 receipt identity"):
        _ = adapter.available_at


def test_composition_rejects_a_forged_replacement_before_calling_p1(tmp_path) -> None:
    projection = _projection()
    observation = projection.observations[0]
    values = list(observation.metric_values)
    values[0] = IntelligenceMetricValue(kind=values[0].kind, score=0.99)
    forged_observation = replace(observation, metric_values=tuple(values))
    object.__setattr__(projection, "observations", (forged_observation,))
    store = ArtifactStore(tmp_path / "forged-projection-evidence")
    data_source_publisher = _RecordingPublisher(store)

    with pytest.raises(IntelligenceFeatureProjectionAdapterError, match="P4 receipt identity"):
        IntelligenceFeatureProjectionPublisher(
            data_source_publisher=data_source_publisher,
            evidence_verifier=ImmutableIntelligenceFeatureProjectionEvidenceVerifier(store),
        ).publish(
            projection=projection,
            publication_spec=_publication_spec(projection),
        )

    assert data_source_publisher.calls == []


def test_composition_rejects_unresolved_immutable_evidence_before_calling_p1(
    tmp_path,
) -> None:
    projection = _projection()
    store = ArtifactStore(tmp_path / "unresolved-projection-evidence")
    data_source_publisher = _RecordingPublisher(store)

    with pytest.raises(
        IntelligenceFeatureProjectionAdapterError,
        match="immutable source/context evidence is invalid",
    ):
        IntelligenceFeatureProjectionPublisher(
            data_source_publisher=data_source_publisher,
            evidence_verifier=ImmutableIntelligenceFeatureProjectionEvidenceVerifier(store),
        ).publish(
            projection=projection,
            publication_spec=_publication_spec(projection),
        )

    assert data_source_publisher.calls == []


def test_composition_requires_evidence_verification_in_the_p1_publisher_store(
    tmp_path,
) -> None:
    publication_store = ArtifactStore(tmp_path / "projection-publication-store")
    verifier_store = ArtifactStore(tmp_path / "projection-verifier-store")

    with pytest.raises(
        IntelligenceFeatureProjectionAdapterError,
        match="same immutable artifact store",
    ):
        IntelligenceFeatureProjectionPublisher(
            data_source_publisher=_RecordingPublisher(publication_store),
            evidence_verifier=ImmutableIntelligenceFeatureProjectionEvidenceVerifier(
                verifier_store
            ),
        )


@pytest.mark.parametrize(
    ("publication_spec", "message"),
    (
        pytest.param(
            lambda projection: _publication_spec(
                projection,
                normalized_available_at=projection.available_at - timedelta(seconds=1),
            ),
            "normalized availability",
            id="mismatched-normalized-availability",
        ),
        pytest.param(
            lambda projection: _publication_spec(
                projection,
                purpose=PublicationPurpose.LIVE_SIGNAL,
            ),
            "restricted",
            id="unsafe-purpose",
        ),
    ),
)
def test_composition_fails_closed_before_calling_p1_for_incompatible_publication_spec(
    tmp_path,
    publication_spec,
    message: str,
) -> None:
    projection = _projection()
    store = ArtifactStore(tmp_path / "incompatible-publication-evidence")
    data_source_publisher = _RecordingPublisher(store)

    with pytest.raises(IntelligenceFeatureProjectionAdapterError, match=message):
        IntelligenceFeatureProjectionPublisher(
            data_source_publisher=data_source_publisher,
            evidence_verifier=ImmutableIntelligenceFeatureProjectionEvidenceVerifier(store),
        ).publish(
            projection=projection,
            publication_spec=publication_spec(projection),
        )

    assert data_source_publisher.calls == []
