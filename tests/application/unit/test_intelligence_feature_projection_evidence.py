from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import polars as pl
import pytest

from northstar_quant.application.intelligence_feature_projection_evidence import (
    ImmutableIntelligenceFeatureProjectionEvidenceVerifier,
    IntelligenceFeatureProjectionEvidenceError,
)
from northstar_quant.data_platform.artifacts.fingerprints import content_sha256
from northstar_quant.data_platform.artifacts.immutable_store import ArtifactStore
from northstar_quant.data_platform.contracts.data_domain import (
    ArtifactMetadata,
    ArtifactProvenance,
    DataSource,
    QualityStatus,
    RawArtifact,
)
from northstar_quant.data_platform.sources.protocol import (
    CANONICAL_NORMALIZED_FORMAT,
    AdapterMetadata,
    PublicationAuthorization,
    PublicationPurpose,
    PublicationScope,
    build_publication_authorization,
)
from northstar_quant.intelligence.context import MarketContextSnapshot
from northstar_quant.intelligence.domain import Event, Evidence, Impact, Mechanism
from northstar_quant.intelligence.feature_projection import (
    AuthorizedMarketContext,
    EventEvidenceAvailability,
    INTELLIGENCE_MARKET_CONTEXT_SNAPSHOT_ROW_COLUMNS,
    IntelligenceFeatureProjectionRequest,
    IntelligenceFeatureProjector,
    IntelligenceMetricKind,
    IntelligenceMetricValue,
    VersionedIntelligenceFeatureProjection,
)
from northstar_quant.intelligence.ontology import Ontology, load_ontology
from tests.helpers.pit_publication import _source_config, publish_authorized_pit_dataset


_EVENT_TIME = datetime(2026, 8, 22, 10, tzinfo=UTC)
_AVAILABLE_AT = _EVENT_TIME + timedelta(minutes=15)
_CONTEXT_AVAILABLE_AT = _EVENT_TIME - timedelta(minutes=5)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _ontology() -> Ontology:
    return load_ontology(Path("ontology"))


def _scope(
    *,
    dataset_id: str,
    purpose: PublicationPurpose = PublicationPurpose.INTERNAL_RESEARCH,
) -> PublicationScope:
    return PublicationScope(
        dataset_id=dataset_id,
        market="CN",
        asset_type="FUTURES",
        frequency="event",
        purpose=purpose,
        environment="internal_server",
        exchanges=("SHFE",),
        products=("CU",),
        actual_contract_data=False,
        requires_authoritative_calendar=False,
        requires_authoritative_dynamic_rules=False,
    )


def _adapter_metadata(adapter_id: str) -> AdapterMetadata:
    return AdapterMetadata(
        adapter_id=adapter_id,
        implementation_version="intelligence-evidence-fixture.v1",
        raw_format="application/json",
        normalized_schema_version="intelligence-evidence-fixture-v1",
        transform_version="intelligence-evidence-fixture-transform-v1",
        normalized_format=CANONICAL_NORMALIZED_FORMAT,
    )


def _stored_receipt(
    store: ArtifactStore,
    *,
    receipt_id: str,
    purpose: PublicationPurpose = PublicationPurpose.INTERNAL_RESEARCH,
    authorized_at: datetime = _EVENT_TIME,
    dataset_id: str | None = None,
) -> str:
    adapter_id = f"{receipt_id}-adapter"
    dataset_id = dataset_id or f"{receipt_id}-dataset"
    source_config = _source_config(
        source_id=f"{receipt_id}-source",
        adapter_id=adapter_id,
        dataset_id=dataset_id,
        permitted_purposes=(PublicationPurpose.INTERNAL_RESEARCH.value,),
        authorized_exchanges=("SHFE",),
        authorized_products=("CU",),
        actual_contract_data=False,
        frequency="event",
        requires_authoritative_dynamic_rules=False,
    )
    scope = _scope(dataset_id=dataset_id, purpose=purpose)
    metadata = _adapter_metadata(adapter_id)
    if purpose is PublicationPurpose.INTERNAL_RESEARCH:
        receipt = build_publication_authorization(
            source_config,
            scope,
            metadata,
            authorized_at=authorized_at,
        )
    else:
        # A persisted historical record can be semantically valid but unsafe
        # for this hand-off.  The verifier must reject it by its frozen scope.
        receipt = PublicationAuthorization(
            source=DataSource.from_config(source_config),
            scope=scope,
            adapter_metadata=metadata,
            authorized_at=authorized_at,
        )
    return store.put_publication_authorization(receipt).authorization_hash


def _document_payload(document_id: str) -> bytes:
    return (
        f"Immutable source document for {document_id}. "
        "The reported supply disruption is retained as research-only evidence."
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class _StoredSourceEvidence:
    receipt_hash: str
    artifact_snapshot_hash: str
    content_hash: str
    payload: bytes


def _stored_source_evidence(
    store: ArtifactStore,
    *,
    receipt_id: str,
    document_id: str,
    authorized_at: datetime,
    payload: bytes | None = None,
    artifact_id: str | None = None,
) -> _StoredSourceEvidence:
    """Create an immutable raw P1 document artifact tied to one receipt."""

    adapter_id = f"{receipt_id}-adapter"
    dataset_id = f"{receipt_id}-dataset"
    source_id = f"{receipt_id}-source"
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
        _scope(dataset_id=dataset_id),
        AdapterMetadata(
            adapter_id=adapter_id,
            implementation_version="intelligence-document-evidence-fixture.v1",
            raw_format="text/plain; charset=utf-8",
            normalized_schema_version="intelligence-document-evidence-fixture-v1",
            transform_version="intelligence-document-evidence-fixture-transform-v1",
            normalized_format=CANONICAL_NORMALIZED_FORMAT,
        ),
        authorized_at=authorized_at,
    )
    payload = payload if payload is not None else _document_payload(document_id)
    content_hash = content_sha256(payload, field_name="source document fixture payload")
    stored = store.put_raw(
        source=receipt.source,
        artifact=RawArtifact(
            metadata=ArtifactMetadata(
                artifact_id=artifact_id or document_id,
                source_id=source_id,
                acquired_at=authorized_at - timedelta(seconds=1),
                available_at=authorized_at,
                schema_version="intelligence-document-evidence-v1",
                content_hash=content_hash,
                transform_version="capture.intelligence-document-evidence-v1",
                quality_status=QualityStatus.PASS,
                provenance=ArtifactProvenance(
                    source_id=source_id,
                    source_reference=f"fixture://source-evidence/{receipt_id}",
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
        content_hash=content_hash,
        payload=payload,
    )


@dataclass(frozen=True, slots=True)
class _ProjectionEvidenceFixture:
    store: ArtifactStore
    projection: VersionedIntelligenceFeatureProjection
    context_receipt_hash: str
    context_artifact_snapshot_hash: str
    source_receipt_hashes: tuple[str, str]
    source_artifact_snapshot_hashes: tuple[str, str]


def _fixture(
    tmp_path: Path,
    *,
    store: ArtifactStore | None = None,
    source_receipt_hashes: tuple[str, str] | None = None,
    source_evidence: tuple[EventEvidenceAvailability, EventEvidenceAvailability] | None = None,
    context_receipt_hash: str | None = None,
    context_artifact_snapshot_hash: str | None = None,
    context_inventory: float = 120.0,
    context_frame_inventory: float | None = None,
) -> _ProjectionEvidenceFixture:
    store = store or ArtifactStore(tmp_path / "immutable-evidence")
    context_as_of = _EVENT_TIME - timedelta(hours=1)
    frame_inventory = (
        context_inventory
        if context_frame_inventory is None
        else context_frame_inventory
    )
    context_frame = pl.DataFrame(
        {
            "snapshot_id": ["context-copper-evidence"],
            "commodity_id": ["copper"],
            "market_id": ["shfe"],
            "as_of": [context_as_of],
            "available_at": [_CONTEXT_AVAILABLE_AT],
            "inventory": [frame_inventory],
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
        tmp_path,
        context_frame,
        store=store,
        dataset_id="intelligence-context-fixture",
        source_id="intelligence-context-source",
        adapter_id="intelligence-context-adapter",
        schema_version="intelligence-market-context-v1",
        artifact_id="intelligence-context-artifact",
        key_columns=("commodity_id", "snapshot_id", "as_of"),
        event_time_column="as_of",
        available_at_column="available_at",
        value_columns=tuple(
            column
            for column in INTELLIGENCE_MARKET_CONTEXT_SNAPSHOT_ROW_COLUMNS
            if column not in {"commodity_id", "snapshot_id", "as_of", "available_at"}
        ),
        normalized_available_at=_CONTEXT_AVAILABLE_AT,
        purpose=PublicationPurpose.INTERNAL_RESEARCH,
        scope_exchanges=("SHFE",),
        scope_products=("CU",),
        actual_contract_data=False,
        frequency="event",
    )
    context_replay = store.replay_dataset_version(context_dataset.version_hash)
    assert len(context_replay.artifacts) == 1
    stored_context_receipt = context_replay.artifacts[0].stored.publication_authorization_hash
    assert isinstance(stored_context_receipt, str)
    stored_context_artifact_hash = (
        context_replay.artifacts[0].stored.snapshot.snapshot_hash
    )

    first_source = _stored_source_evidence(
        store,
        receipt_id="source-document-one",
        document_id="document-one",
        authorized_at=_EVENT_TIME - timedelta(minutes=2),
    )
    second_source = _stored_source_evidence(
        store,
        receipt_id="source-document-two",
        document_id="document-two",
        authorized_at=_EVENT_TIME - timedelta(minutes=1),
    )
    source_receipt_hashes = source_receipt_hashes or (
        first_source.receipt_hash,
        second_source.receipt_hash,
    )
    source_evidence = source_evidence or (
        EventEvidenceAvailability(
            "document-one",
            first_source.content_hash,
            4,
            23,
            _EVENT_TIME - timedelta(minutes=2),
            source_receipt_hashes[0],
            first_source.artifact_snapshot_hash,
        ),
        EventEvidenceAvailability(
            "document-two",
            second_source.content_hash,
            9,
            31,
            _EVENT_TIME - timedelta(minutes=1),
            source_receipt_hashes[1],
            second_source.artifact_snapshot_hash,
        ),
    )
    context_receipt_hash = context_receipt_hash or stored_context_receipt
    context_artifact_snapshot_hash = (
        context_artifact_snapshot_hash or stored_context_artifact_hash
    )
    ontology = _ontology()
    event = Event(
        event_id="event-copper-outage-evidence",
        ontology_version=ontology.version,
        evidence=tuple(
            Evidence(
                item.document_id,
                item.content_hash,
                item.span_start,
                item.span_end,
            )
            for item in source_evidence
        ),
        mechanism=Mechanism("SUPPLY_REDUCTION", ontology.version),
        impacts=(Impact("impact-copper-evidence", "copper", "UP"),),
    )
    context = MarketContextSnapshot(
        snapshot_id="context-copper-evidence",
        commodity_id="copper",
        market_id="shfe",
        dataset_version=context_dataset.version_hash,
        as_of=context_as_of,
        available_at=_CONTEXT_AVAILABLE_AT,
        inventory=context_inventory,
        term_structure=-0.1,
        basis=5.0,
        positioning=0.25,
        volatility=0.18,
        usd=100.0,
        cny=7.2,
        macro_regime="slowdown",
        seasonality="Q3",
    )
    authorized_context = AuthorizedMarketContext(
        context,
        context_dataset.version_hash,
        context_receipt_hash,
        context_artifact_snapshot_hash,
    )
    projection = IntelligenceFeatureProjector().project(
        IntelligenceFeatureProjectionRequest(
            projection_version="p8-intelligence-evidence-v3",
            ontology=ontology,
            event=event,
            mechanism=event.mechanism,
            selected_impact=event.impacts[0],
            event_evidence=source_evidence,
            authorized_market_context=authorized_context,
            event_time=_EVENT_TIME,
            available_at=_AVAILABLE_AT,
            metric_values=tuple(
                IntelligenceMetricValue(kind=kind, score=(index + 1) / 10)
                for index, kind in enumerate(IntelligenceMetricKind)
            ),
            publication_receipt_hashes=tuple(
                sorted(
                    {
                        *(
                            item.source_publication_receipt_hash
                            for item in source_evidence
                        ),
                        context_receipt_hash,
                    }
                )
            ),
        )
    )
    return _ProjectionEvidenceFixture(
        store=store,
        projection=projection,
        context_receipt_hash=stored_context_receipt,
        context_artifact_snapshot_hash=context_artifact_snapshot_hash,
        source_receipt_hashes=tuple(
            item.source_publication_receipt_hash for item in source_evidence
        ),
        source_artifact_snapshot_hashes=tuple(
            item.source_artifact_snapshot_hash for item in source_evidence
        ),
    )


def test_verifier_requires_real_immutable_source_and_context_evidence(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    verified = ImmutableIntelligenceFeatureProjectionEvidenceVerifier(
        fixture.store
    ).verify(fixture.projection)

    assert verified is not fixture.projection
    assert verified.canonical_payload == fixture.projection.canonical_payload
    assert verified.observations[0].source_publication_receipt_hashes == tuple(
        sorted(fixture.source_receipt_hashes)
    )
    assert {
        item.source_artifact_snapshot_hash
        for item in verified.observations[0].event_evidence
    } == set(fixture.source_artifact_snapshot_hashes)
    feature_input_row = verified.as_feature_input_rows()[0]
    assert "source_artifact_snapshot_hash" not in feature_input_row
    assert "context_artifact_snapshot_hash" not in feature_input_row
    assert "context_content_commitment_hash" not in feature_input_row


def test_verifier_rejects_an_unknown_source_receipt_before_context_consumption(
    tmp_path: Path,
) -> None:
    unknown_source_receipt = _hash("unknown-source-receipt")
    initial = _fixture(tmp_path)
    fixture = _fixture(
        tmp_path,
        store=initial.store,
        source_receipt_hashes=(unknown_source_receipt, initial.source_receipt_hashes[1]),
    )

    with pytest.raises(
        IntelligenceFeatureProjectionEvidenceError,
        match="source evidence immutable receipt cannot be resolved",
    ):
        ImmutableIntelligenceFeatureProjectionEvidenceVerifier(fixture.store).verify(
            fixture.projection
        )


def test_verifier_rejects_an_unknown_source_artifact_snapshot(
    tmp_path: Path,
) -> None:
    initial = _fixture(tmp_path)
    first, second = initial.projection.observations[0].event_evidence
    fixture = _fixture(
        tmp_path,
        store=initial.store,
        source_evidence=(
            replace(
                first,
                source_artifact_snapshot_hash=_hash("unknown-source-artifact-snapshot"),
            ),
            second,
        ),
    )

    with pytest.raises(
        IntelligenceFeatureProjectionEvidenceError,
        match="source evidence immutable artifact cannot be resolved",
    ):
        ImmutableIntelligenceFeatureProjectionEvidenceVerifier(fixture.store).verify(
            fixture.projection
        )


def test_verifier_rejects_source_artifact_bound_to_a_different_document_id(
    tmp_path: Path,
) -> None:
    initial = _fixture(tmp_path)
    first, second = initial.projection.observations[0].event_evidence
    foreign = _stored_source_evidence(
        initial.store,
        receipt_id="source-document-id-mismatch",
        document_id="document-one",
        artifact_id="document-other",
        payload=_document_payload("document-one"),
        authorized_at=first.available_at,
    )
    fixture = _fixture(
        tmp_path,
        store=initial.store,
        source_evidence=(
            replace(
                first,
                source_publication_receipt_hash=foreign.receipt_hash,
                source_artifact_snapshot_hash=foreign.artifact_snapshot_hash,
            ),
            second,
        ),
    )

    with pytest.raises(
        IntelligenceFeatureProjectionEvidenceError,
        match="artifact document_id does not match",
    ):
        ImmutableIntelligenceFeatureProjectionEvidenceVerifier(fixture.store).verify(
            fixture.projection
        )


def test_verifier_rejects_source_artifact_with_a_different_document_content_hash(
    tmp_path: Path,
) -> None:
    initial = _fixture(tmp_path)
    first, second = initial.projection.observations[0].event_evidence
    foreign = _stored_source_evidence(
        initial.store,
        receipt_id="source-document-content-mismatch",
        document_id=first.document_id,
        payload=b"A different immutable source document with sufficient length for every span.",
        authorized_at=first.available_at,
    )
    fixture = _fixture(
        tmp_path,
        store=initial.store,
        source_evidence=(
            replace(
                first,
                source_publication_receipt_hash=foreign.receipt_hash,
                source_artifact_snapshot_hash=foreign.artifact_snapshot_hash,
            ),
            second,
        ),
    )

    with pytest.raises(
        IntelligenceFeatureProjectionEvidenceError,
        match="artifact content_hash does not match",
    ):
        ImmutableIntelligenceFeatureProjectionEvidenceVerifier(fixture.store).verify(
            fixture.projection
        )


def test_verifier_rejects_a_span_outside_the_immutable_document_payload(
    tmp_path: Path,
) -> None:
    initial = _fixture(tmp_path)
    first, second = initial.projection.observations[0].event_evidence
    fixture = _fixture(
        tmp_path,
        store=initial.store,
        source_evidence=(
            replace(
                first,
                span_end=len(_document_payload(first.document_id).decode("utf-8")) + 1,
            ),
            second,
        ),
    )

    with pytest.raises(
        IntelligenceFeatureProjectionEvidenceError,
        match="span is outside the immutable document payload",
    ):
        ImmutableIntelligenceFeatureProjectionEvidenceVerifier(fixture.store).verify(
            fixture.projection
        )


def test_verifier_rejects_a_source_artifact_bound_to_a_different_receipt(
    tmp_path: Path,
) -> None:
    initial = _fixture(tmp_path)
    first, second = initial.projection.observations[0].event_evidence
    foreign = _stored_source_evidence(
        initial.store,
        receipt_id="source-document-receipt-mismatch",
        document_id=first.document_id,
        payload=_document_payload(first.document_id),
        authorized_at=first.available_at,
    )
    fixture = _fixture(
        tmp_path,
        store=initial.store,
        source_evidence=(
            replace(first, source_publication_receipt_hash=foreign.receipt_hash),
            second,
        ),
    )

    with pytest.raises(
        IntelligenceFeatureProjectionEvidenceError,
        match="artifact must exactly bind its publication receipt",
    ):
        ImmutableIntelligenceFeatureProjectionEvidenceVerifier(fixture.store).verify(
            fixture.projection
        )


def test_verifier_rejects_a_source_receipt_with_unsafe_scope(tmp_path: Path) -> None:
    initial = _fixture(tmp_path)
    unsafe_receipt = _stored_receipt(
        initial.store,
        receipt_id="unsafe-live-source",
        purpose=PublicationPurpose.LIVE_SIGNAL,
    )
    fixture = _fixture(
        tmp_path,
        store=initial.store,
        source_receipt_hashes=(unsafe_receipt, initial.source_receipt_hashes[1]),
    )

    with pytest.raises(
        IntelligenceFeatureProjectionEvidenceError,
        match="not limited to research or historical backtest",
    ):
        ImmutableIntelligenceFeatureProjectionEvidenceVerifier(fixture.store).verify(
            fixture.projection
        )


def test_verifier_rejects_a_source_receipt_authorized_after_projection_availability(
    tmp_path: Path,
) -> None:
    initial = _fixture(tmp_path)
    future_receipt = _stored_receipt(
        initial.store,
        receipt_id="future-source",
        authorized_at=_AVAILABLE_AT + timedelta(minutes=1),
    )
    fixture = _fixture(
        tmp_path,
        store=initial.store,
        source_receipt_hashes=(future_receipt, initial.source_receipt_hashes[1]),
    )

    with pytest.raises(
        IntelligenceFeatureProjectionEvidenceError,
        match="authorized after projection availability",
    ):
        ImmutableIntelligenceFeatureProjectionEvidenceVerifier(fixture.store).verify(
            fixture.projection
        )


def test_verifier_rejects_context_dataset_with_a_different_persisted_receipt(
    tmp_path: Path,
) -> None:
    initial = _fixture(tmp_path)
    alternate_context_receipt = _stored_receipt(
        initial.store,
        receipt_id="alternate-context",
        dataset_id="intelligence-context-fixture",
    )
    fixture = _fixture(
        tmp_path,
        store=initial.store,
        context_receipt_hash=alternate_context_receipt,
    )

    with pytest.raises(
        IntelligenceFeatureProjectionEvidenceError,
        match="artifacts must uniformly bind the context receipt",
    ):
        ImmutableIntelligenceFeatureProjectionEvidenceVerifier(fixture.store).verify(
            fixture.projection
        )


def test_verifier_rejects_context_receipt_for_a_different_dataset(
    tmp_path: Path,
) -> None:
    initial = _fixture(tmp_path)
    alternate_context_receipt = _stored_receipt(
        initial.store,
        receipt_id="other-context-dataset",
    )
    fixture = _fixture(
        tmp_path,
        store=initial.store,
        context_receipt_hash=alternate_context_receipt,
    )

    with pytest.raises(
        IntelligenceFeatureProjectionEvidenceError,
        match="receipt must exactly bind the replayed DatasetVersion dataset_id",
    ):
        ImmutableIntelligenceFeatureProjectionEvidenceVerifier(fixture.store).verify(
            fixture.projection
        )


def test_verifier_rejects_context_artifact_outside_the_replayed_dataset(
    tmp_path: Path,
) -> None:
    initial = _fixture(tmp_path)
    fixture = _fixture(
        tmp_path,
        store=initial.store,
        context_artifact_snapshot_hash=_hash("unknown-context-artifact-snapshot"),
    )

    with pytest.raises(
        IntelligenceFeatureProjectionEvidenceError,
        match="artifact snapshot is not a member of the replayed DatasetVersion",
    ):
        ImmutableIntelligenceFeatureProjectionEvidenceVerifier(fixture.store).verify(
            fixture.projection
        )


def test_verifier_rejects_context_values_not_persisted_in_the_normalized_artifact(
    tmp_path: Path,
) -> None:
    fixture = _fixture(
        tmp_path,
        context_inventory=120.0,
        context_frame_inventory=121.0,
    )

    with pytest.raises(
        IntelligenceFeatureProjectionEvidenceError,
        match="does not contain the exact P4 context content row",
    ):
        ImmutableIntelligenceFeatureProjectionEvidenceVerifier(fixture.store).verify(
            fixture.projection
        )
