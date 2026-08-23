"""Reusable test-only setup for the P4 intelligence-to-P1 publication seam.

The helpers deliberately build a real immutable P4 receipt and send it through
the application publisher and P1 ``DataSourcePublisher``.  The fixture source
configuration and pass-only quality engine remain in ``tests.helpers`` so this
module cannot become a production source configuration or a publication path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from northstar_quant.application.intelligence_feature_projection import (
    IntelligenceFeatureProjectionAdapter,
    IntelligenceFeatureProjectionPublisher,
)
from northstar_quant.application.intelligence_feature_projection_evidence import (
    ImmutableIntelligenceFeatureProjectionEvidenceVerifier,
)
from northstar_quant.data_platform.artifacts.fingerprints import content_sha256
from northstar_quant.data_platform.artifacts.immutable_store import ArtifactStore
from northstar_quant.data_platform.contracts.data_domain import (
    ArtifactMetadata,
    ArtifactProvenance,
    NormalizedArtifact,
    QualityStatus,
    RawArtifact,
)
from northstar_quant.data_platform.quality import (
    CompletenessRule,
    GapRule,
    OrderingRule,
    QualityEvaluation,
    QualityRequest,
    QualityRule,
    RevisionRule,
    SchemaField,
    StalenessRule,
    UniquenessRule,
)
from northstar_quant.data_platform.sources.protocol import (
    CANONICAL_NORMALIZED_FORMAT,
    AdapterMetadata,
    NormalizedTable,
    PublicationPurpose,
    PublicationScope,
    SourceFetchRequest,
    build_publication_authorization,
)
from northstar_quant.data_platform.sources.publisher import (
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
    INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS,
    INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS,
    INTELLIGENCE_MARKET_CONTEXT_SNAPSHOT_ROW_COLUMNS,
    IntelligenceFeatureProjectionRequest,
    IntelligenceFeatureProjector,
    IntelligenceMetricKind,
    IntelligenceMetricValue,
    VersionedIntelligenceFeatureProjection,
)
from northstar_quant.intelligence.ontology import load_ontology
from northstar_quant.research.features.intelligence import INTELLIGENCE_EVENT_INPUT
from tests.helpers.pit_publication import (
    _PassQualityEngine,
    _source_config,
    publish_authorized_pit_dataset,
)


_SOURCE_ID = "intelligence_projection_fixture_source"
_DATASET_ID = "intelligence_feature_projection_fixture"
_ARTIFACT_ID = "intelligence-feature-projection-normalized"
_DEFAULT_EVENT_TIME = datetime(2026, 8, 22, 10, tzinfo=UTC)
_DEFAULT_AVAILABLE_AT = datetime(2026, 8, 22, 10, 15, tzinfo=UTC)


def _document_payload(document_id: str) -> bytes:
    return (
        f"Immutable source document for {document_id}. "
        "The reported supply disruption is independently retained for research evidence."
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class _StoredSourceEvidence:
    receipt_hash: str
    artifact_snapshot_hash: str
    content_hash: str


def _stored_source_evidence(
    store: ArtifactStore,
    *,
    document_id: str,
    authorized_at: datetime,
) -> _StoredSourceEvidence:
    """Persist one raw P1 document artifact and its exact research receipt."""

    source_id = f"intelligence-{document_id}-source"
    adapter_id = f"intelligence-{document_id}-adapter"
    dataset_id = f"intelligence-{document_id}-evidence"
    source_config = _source_config(
        source_id=source_id,
        adapter_id=adapter_id,
        dataset_id=dataset_id,
        permitted_purposes=(PublicationPurpose.INTERNAL_RESEARCH.value,),
        authorized_exchanges=("SHFE",),
        authorized_products=("CU",),
        actual_contract_data=False,
        frequency="event",
        requires_authoritative_dynamic_rules=False,
    )
    receipt = build_publication_authorization(
        source_config,
        PublicationScope(
            dataset_id=dataset_id,
            market="CN",
            asset_type="FUTURES",
            frequency="event",
            purpose=PublicationPurpose.INTERNAL_RESEARCH,
            environment="internal_server",
            exchanges=("SHFE",),
            products=("CU",),
            actual_contract_data=False,
            requires_authoritative_calendar=False,
            requires_authoritative_dynamic_rules=False,
        ),
        AdapterMetadata(
            adapter_id=adapter_id,
            implementation_version="intelligence-evidence-fixture-v1",
            raw_format="application/json",
            normalized_schema_version="intelligence-evidence-fixture-v1",
            transform_version="intelligence-evidence-fixture-transform-v1",
            normalized_format=CANONICAL_NORMALIZED_FORMAT,
        ),
        authorized_at=authorized_at,
    )
    payload = _document_payload(document_id)
    stored = store.put_raw(
        source=receipt.source,
        artifact=RawArtifact(
            metadata=ArtifactMetadata(
                artifact_id=document_id,
                source_id=source_id,
                acquired_at=authorized_at - timedelta(seconds=1),
                available_at=authorized_at,
                schema_version="intelligence-document-evidence-v1",
                content_hash=content_sha256(
                    payload,
                    field_name="intelligence fixture source document",
                ),
                transform_version="capture.intelligence-document-evidence-v1",
                quality_status=QualityStatus.PASS,
                provenance=ArtifactProvenance(
                    source_id=source_id,
                    source_reference=f"fixture://intelligence-evidence/{document_id}",
                    collection_method="fixture-document-import",
                ),
            ),
            raw_format="text/plain; charset=utf-8",
        ),
        payload=payload,
        authorization=receipt,
    )
    return _StoredSourceEvidence(
        receipt_hash=receipt.authorization_hash,
        artifact_snapshot_hash=stored.snapshot.snapshot_hash,
        content_hash=content_sha256(
            payload,
            field_name="intelligence fixture source document",
        ),
    )


def _authorized_context(
    store: ArtifactStore,
    *,
    event_time: datetime,
) -> AuthorizedMarketContext:
    """Publish and replay the exact immutable P1 DatasetVersion used by P4 context."""

    context_as_of = event_time - timedelta(hours=1)
    context_available_at = context_as_of + timedelta(minutes=5)
    context_frame = pl.DataFrame(
        {
            "snapshot_id": ["context-copper-1"],
            "commodity_id": ["copper"],
            "market_id": ["shfe"],
            "as_of": [context_as_of],
            "available_at": [context_available_at],
            "inventory": [120.0],
            "term_structure": [-0.1],
            "basis": [5.0],
            "positioning": [0.25],
            "volatility": [0.18],
            "usd": [100.0],
            "cny": [7.2],
            "macro_regime": ["slowdown"],
            "seasonality": ["Q3"],
        }
    )
    assert tuple(context_frame.columns) == INTELLIGENCE_MARKET_CONTEXT_SNAPSHOT_ROW_COLUMNS
    _, context_dataset = publish_authorized_pit_dataset(
        store.root,
        context_frame,
        store=store,
        dataset_id="intelligence-context-fixture",
        source_id="intelligence-context-source",
        adapter_id="intelligence-context-adapter",
        schema_version="intelligence-context-fixture-v1",
        artifact_id="intelligence-context-artifact",
        key_columns=("commodity_id", "snapshot_id", "as_of"),
        event_time_column="as_of",
        available_at_column="available_at",
        value_columns=tuple(
            column
            for column in INTELLIGENCE_MARKET_CONTEXT_SNAPSHOT_ROW_COLUMNS
            if column not in {"commodity_id", "snapshot_id", "as_of", "available_at"}
        ),
        normalized_available_at=context_available_at,
        purpose=PublicationPurpose.INTERNAL_RESEARCH,
        scope_exchanges=("SHFE",),
        scope_products=("CU",),
        actual_contract_data=False,
        frequency="event",
    )
    replay = store.replay_dataset_version(context_dataset.version_hash)
    receipt_hashes = {
        artifact.stored.publication_authorization_hash for artifact in replay.artifacts
    }
    assert len(receipt_hashes) == 1
    context_receipt_hash = receipt_hashes.pop()
    assert isinstance(context_receipt_hash, str)
    assert len(replay.artifacts) == 1
    context_artifact_snapshot_hash = replay.artifacts[0].stored.snapshot.snapshot_hash
    return AuthorizedMarketContext(
        MarketContextSnapshot(
            snapshot_id="context-copper-1",
            commodity_id="copper",
            market_id="shfe",
            dataset_version=context_dataset.version_hash,
            as_of=context_as_of,
            available_at=context_available_at,
            inventory=120.0,
            term_structure=-0.1,
            basis=5.0,
            positioning=0.25,
            volatility=0.18,
            usd=100.0,
            cny=7.2,
            macro_regime="slowdown",
            seasonality="Q3",
        ),
        context_dataset_version_hash=context_dataset.version_hash,
        context_publication_receipt_hash=context_receipt_hash,
        context_artifact_snapshot_hash=context_artifact_snapshot_hash,
    )


def _projection(
    *,
    store: ArtifactStore,
    event_time: datetime,
    available_at: datetime,
    metric_values: tuple[IntelligenceMetricValue, ...] | None = None,
) -> VersionedIntelligenceFeatureProjection:
    ontology = load_ontology(Path("ontology"))
    first_source_evidence = _stored_source_evidence(
        store,
        document_id="document-one",
        authorized_at=event_time - timedelta(minutes=10),
    )
    second_source_evidence = _stored_source_evidence(
        store,
        document_id="document-two",
        authorized_at=event_time - timedelta(minutes=9),
    )
    event = Event(
        event_id="event-copper-outage-1",
        ontology_version=ontology.version,
        evidence=(
            Evidence("document-one", first_source_evidence.content_hash, 4, 23),
            Evidence("document-two", second_source_evidence.content_hash, 9, 31),
        ),
        mechanism=Mechanism("SUPPLY_REDUCTION", ontology.version),
        impacts=(Impact("impact-copper-1", "copper", "UP"),),
    )
    event_evidence = (
        EventEvidenceAvailability(
            "document-one",
            first_source_evidence.content_hash,
            4,
            23,
            event_time - timedelta(minutes=10),
            first_source_evidence.receipt_hash,
            first_source_evidence.artifact_snapshot_hash,
        ),
        EventEvidenceAvailability(
            "document-two",
            second_source_evidence.content_hash,
            9,
            31,
            event_time - timedelta(minutes=9),
            second_source_evidence.receipt_hash,
            second_source_evidence.artifact_snapshot_hash,
        ),
    )
    authorized_context = _authorized_context(store, event_time=event_time)
    request = IntelligenceFeatureProjectionRequest(
        projection_version="p4-intelligence-feature-v3",
        ontology=ontology,
        event=event,
        mechanism=event.mechanism,
        selected_impact=event.impacts[0],
        event_evidence=event_evidence,
        authorized_market_context=authorized_context,
        event_time=event_time,
        available_at=available_at,
        metric_values=(
            metric_values
            if metric_values is not None
            else tuple(
                IntelligenceMetricValue(kind=kind, score=(index + 1) / 10)
                for index, kind in enumerate(IntelligenceMetricKind)
            )
        ),
        publication_receipt_hashes=tuple(
            sorted(
                {
                    *(
                        item.source_publication_receipt_hash
                        for item in event_evidence
                    ),
                    authorized_context.context_publication_receipt_hash,
                }
            )
        ),
    )
    return IntelligenceFeatureProjector().project(request)


class _ProjectionFixtureQualityRequestBuilder:
    """Closed, full-schema P1 policy for the hash-only P4 input table."""

    def build(
        self,
        *,
        candidate: NormalizedArtifact,
        normalized: NormalizedTable,
        checked_at: datetime,
        decision_at: datetime,
    ) -> QualityRequest:
        frame = normalized.frame
        key_columns = (
            *INTELLIGENCE_EVENT_INPUT.entity_key_columns,
            INTELLIGENCE_EVENT_INPUT.event_time_column,
        )
        expected_columns = (
            *key_columns,
            INTELLIGENCE_EVENT_INPUT.available_at_column,
            *(INTELLIGENCE_EVENT_INPUT.value_columns or ()),
        )
        if tuple(frame.columns) != expected_columns:
            raise ValueError(
                "intelligence projection fixture quality policy requires the exact P2 input schema"
            )
        nullable_columns = frozenset(
            {
                *INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS,
                *INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS,
            }
        )
        required_columns = tuple(
            column for column in expected_columns if column not in nullable_columns
        )
        return QualityRequest(
            artifact=candidate,
            frame=frame,
            checked_at=checked_at,
            decision_at=decision_at,
            completeness=CompletenessRule(required_columns, 1, 0.0),
            uniqueness=UniquenessRule(
                (*key_columns, INTELLIGENCE_EVENT_INPUT.available_at_column)
            ),
            ordering=OrderingRule(
                (
                    INTELLIGENCE_EVENT_INPUT.event_time_column,
                    INTELLIGENCE_EVENT_INPUT.available_at_column,
                ),
                INTELLIGENCE_EVENT_INPUT.entity_key_columns,
            ),
            schema=tuple(
                SchemaField(
                    column,
                    str(frame.schema[column]),
                    column in nullable_columns,
                )
                for column in expected_columns
            ),
            expected_artifact_schema_version=candidate.schema_version,
            allow_additional_columns=False,
            ranges=(),
            staleness=StalenessRule(None, timedelta(hours=1)),
            gap=GapRule(
                INTELLIGENCE_EVENT_INPUT.event_time_column,
                timedelta(days=7),
                INTELLIGENCE_EVENT_INPUT.entity_key_columns,
                coverage_start=checked_at - timedelta(minutes=1),
                coverage_end=checked_at,
            ),
            revision=RevisionRule(
                (*key_columns, INTELLIGENCE_EVENT_INPUT.available_at_column),
                INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS,
                QualityStatus.WARN,
                None,
            ),
            policy_id="intelligence-projection-fixture-quality-policy",
            policy_version="v1",
            evaluated_payload=normalized.payload,
            critical_rules=frozenset(QualityRule),
        )


class _ProjectionFixtureQualityEngine(_PassQualityEngine):
    """Records the real P1 request so the fixture can assert its full schema policy."""

    def __init__(self) -> None:
        self.requests: list[QualityRequest] = []

    def evaluate(self, request: QualityRequest) -> QualityEvaluation:
        self.requests.append(request)
        return super().evaluate(request)


@dataclass(frozen=True, slots=True)
class IntelligenceFeatureProjectionFixture:
    """One unpublished, fully authorized fixture at the P4/P1 hand-off."""

    store: ArtifactStore
    projection: VersionedIntelligenceFeatureProjection
    data_source_publisher: DataSourcePublisher
    publication_spec: SourcePublicationSpec
    quality_engine: _ProjectionFixtureQualityEngine

    def publish(self) -> PublishedSourceDataset:
        """Publish this exact frozen projection through the real application seam."""

        return IntelligenceFeatureProjectionPublisher(
            data_source_publisher=self.data_source_publisher,
            evidence_verifier=ImmutableIntelligenceFeatureProjectionEvidenceVerifier(
                self.store
            ),
        ).publish(
            projection=self.projection,
            publication_spec=self.publication_spec,
        )


@dataclass(frozen=True, slots=True)
class PublishedIntelligenceFeatureProjectionFixture:
    """The immutable P4 receipt and its actual authorized P1 publication."""

    store: ArtifactStore
    projection: VersionedIntelligenceFeatureProjection
    publication_spec: SourcePublicationSpec
    publication: PublishedSourceDataset


def build_authorized_intelligence_feature_projection_fixture(
    root: Path,
    *,
    event_time: datetime = _DEFAULT_EVENT_TIME,
    available_at: datetime = _DEFAULT_AVAILABLE_AT,
    metric_values: tuple[IntelligenceMetricValue, ...] | None = None,
) -> IntelligenceFeatureProjectionFixture:
    """Build a safe, offline P4 receipt and injected P1 publication boundary.

    The returned fixture is intentionally unpublished so failure tests can prove
    that a rejected bridge never reaches P1 artifact release.
    """

    store = ArtifactStore(root / "intelligence-feature-projection-artifacts")
    projection = _projection(
        store=store,
        event_time=event_time,
        available_at=available_at,
        metric_values=metric_values,
    )
    adapter_id = IntelligenceFeatureProjectionAdapter(projection=projection).adapter_id
    source_config = _source_config(
        source_id=_SOURCE_ID,
        adapter_id=adapter_id,
        dataset_id=_DATASET_ID,
        permitted_purposes=(PublicationPurpose.INTERNAL_RESEARCH.value,),
        authorized_exchanges=("SHFE",),
        authorized_products=("CU",),
        actual_contract_data=False,
        frequency="snapshot",
        requires_authoritative_dynamic_rules=False,
    )

    def source_config_loader(requested_source_id: str):
        if requested_source_id != source_config.source_id:
            raise ValueError("unknown intelligence projection fixture source")
        return source_config

    quality_engine = _ProjectionFixtureQualityEngine()
    data_source_publisher = DataSourcePublisher(
        store=store,
        source_config_loader=source_config_loader,
        quality_engine=quality_engine,
    )
    acquired_at = projection.available_at - timedelta(minutes=1)
    publication_spec = SourcePublicationSpec(
        request=SourceFetchRequest(
            source_id=_SOURCE_ID,
            scope=PublicationScope(
                dataset_id=_DATASET_ID,
                market="CN",
                asset_type="FUTURES",
                frequency="snapshot",
                purpose=PublicationPurpose.INTERNAL_RESEARCH,
                environment="internal_server",
                exchanges=("SHFE",),
                products=("CU",),
                actual_contract_data=False,
                requires_authoritative_calendar=False,
                requires_authoritative_dynamic_rules=False,
            ),
            request_reference="fixture://intelligence-feature-projection",
            requested_at=acquired_at - timedelta(minutes=1),
        ),
        acquired_at=acquired_at,
        normalized_available_at=projection.available_at,
        checked_at=projection.available_at,
        decision_at=projection.available_at,
        raw_artifact_id=f"{_ARTIFACT_ID}.raw",
        normalized_artifact_id=_ARTIFACT_ID,
        quality_request_builder=_ProjectionFixtureQualityRequestBuilder(),
        dataset_transform_version="intelligence-feature-projection-dataset-v3",
    )
    return IntelligenceFeatureProjectionFixture(
        store=store,
        projection=projection,
        data_source_publisher=data_source_publisher,
        publication_spec=publication_spec,
        quality_engine=quality_engine,
    )


def publish_authorized_intelligence_feature_projection_fixture(
    root: Path,
    *,
    event_time: datetime = _DEFAULT_EVENT_TIME,
    available_at: datetime = _DEFAULT_AVAILABLE_AT,
    metric_values: tuple[IntelligenceMetricValue, ...] | None = None,
) -> PublishedIntelligenceFeatureProjectionFixture:
    """Return a real P4-to-P1 publication for safe downstream PIT test use."""

    fixture = build_authorized_intelligence_feature_projection_fixture(
        root,
        event_time=event_time,
        available_at=available_at,
        metric_values=metric_values,
    )
    return PublishedIntelligenceFeatureProjectionFixture(
        store=fixture.store,
        projection=fixture.projection,
        publication_spec=fixture.publication_spec,
        publication=fixture.publish(),
    )


__all__ = [
    "IntelligenceFeatureProjectionFixture",
    "PublishedIntelligenceFeatureProjectionFixture",
    "build_authorized_intelligence_feature_projection_fixture",
    "publish_authorized_intelligence_feature_projection_fixture",
]
