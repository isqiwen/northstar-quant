from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from northstar_quant.intelligence.context import MarketContextSnapshot
from northstar_quant.intelligence.domain import Event, Evidence, Impact, Mechanism
from northstar_quant.intelligence.feature_projection import (
    AuthorizedMarketContext,
    EventEvidenceAvailability,
    INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS,
    INTELLIGENCE_FEATURE_INPUT_PROVENANCE_COLUMNS,
    INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS,
    INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS,
    INTELLIGENCE_FEATURE_PROJECTION_PROVENANCE_COLUMNS,
    INTELLIGENCE_FEATURE_PROJECTION_SCHEMA_VERSION,
    INTELLIGENCE_FEATURE_PROJECTION_SCORE_COLUMNS,
    INTELLIGENCE_FEATURE_PROJECTION_VALUE_COLUMNS,
    IntelligenceFeatureProjectionError,
    IntelligenceFeatureProjectionObservation,
    IntelligenceFeatureProjectionRequest,
    IntelligenceFeatureProjector,
    IntelligenceMetricKind,
    IntelligenceMetricValue,
    VersionedIntelligenceFeatureProjection,
)
from northstar_quant.intelligence.ontology import Ontology, load_ontology


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _event() -> Event:
    return Event(
        event_id="event-copper-outage-1",
        ontology_version="v1",
        evidence=(
            Evidence("document-one", _hash("document-one"), 4, 23),
            Evidence("document-two", _hash("document-two"), 9, 31),
        ),
        mechanism=Mechanism("SUPPLY_REDUCTION", "v1"),
        impacts=(Impact("impact-copper-1", "copper", "UP"),),
    )


def _ontology() -> Ontology:
    return load_ontology(Path("ontology"))


def _context(*, available_at: datetime | None = None) -> MarketContextSnapshot:
    as_of = datetime(2026, 8, 22, 9, tzinfo=UTC)
    return MarketContextSnapshot(
        snapshot_id="context-copper-1",
        commodity_id="copper",
        market_id="shfe",
        dataset_version=_hash("market-context-dataset-v1"),
        as_of=as_of,
        available_at=available_at or as_of + timedelta(minutes=5),
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


def _metric_values() -> tuple[IntelligenceMetricValue, ...]:
    values = tuple(
        IntelligenceMetricValue(kind=kind, score=(index + 1) / 10)
        for index, kind in enumerate(IntelligenceMetricKind)
    )
    return tuple(reversed(values))


def _request(
    *,
    event: Event | None = None,
    ontology: Ontology | None = None,
    context: MarketContextSnapshot | None = None,
    event_time: datetime | None = None,
    available_at: datetime | None = None,
    evidence: tuple[EventEvidenceAvailability, ...] | None = None,
    metric_values: tuple[IntelligenceMetricValue, ...] | None = None,
    publication_receipt_hashes: tuple[str, ...] | None = None,
    impact: Impact | None = None,
) -> IntelligenceFeatureProjectionRequest:
    event = event or _event()
    event_time = event_time or datetime(2026, 8, 22, 10, tzinfo=UTC)
    available_at = available_at or datetime(2026, 8, 22, 10, 15, tzinfo=UTC)
    evidence = evidence or (
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
        context or _context(),
        _hash("market-context-dataset-v1"),
        _hash("market-context-publication-receipt"),
        _hash("market-context-artifact-snapshot"),
    )
    publication_receipt_hashes = publication_receipt_hashes or tuple(
        sorted(
            {
                *(item.source_publication_receipt_hash for item in evidence),
                authorized_context.context_publication_receipt_hash,
            }
        )
    )
    return IntelligenceFeatureProjectionRequest(
        projection_version="p4-intelligence-feature-v3",
        ontology=ontology or _ontology(),
        event=event,
        mechanism=event.mechanism,
        selected_impact=impact or event.impacts[0],
        event_evidence=evidence,
        authorized_market_context=authorized_context,
        event_time=event_time,
        available_at=available_at,
        metric_values=metric_values or _metric_values(),
        publication_receipt_hashes=publication_receipt_hashes,
    )


def test_projector_emits_canonical_hash_only_p1_rows_with_all_nine_metrics():
    request = _request()

    projection = IntelligenceFeatureProjector().project(request)

    assert projection.collection_schema == INTELLIGENCE_FEATURE_PROJECTION_SCHEMA_VERSION
    assert projection.available_at == request.available_at
    assert projection.eligible_for_trading is False
    assert len(projection.observations) == 1
    observation = projection.observations[0]
    assert observation.metric_values == tuple(
        sorted(request.metric_values, key=lambda item: list(IntelligenceMetricKind).index(item.kind))
    )
    assert observation.event_hash == request.event.event_hash
    assert observation.source_publication_receipt_hashes == tuple(
        sorted(item.source_publication_receipt_hash for item in request.event_evidence)
    )
    assert observation.event_evidence == request.event_evidence
    assert {
        item.source_artifact_snapshot_hash for item in observation.event_evidence
    } == {
        item.source_artifact_snapshot_hash for item in request.event_evidence
    }
    assert observation.context_dataset_version_hash == (
        request.authorized_market_context.context_dataset_version_hash
    )
    assert observation.context_publication_receipt_hash == (
        request.authorized_market_context.context_publication_receipt_hash
    )
    assert observation.context_artifact_snapshot_hash == (
        request.authorized_market_context.context_artifact_snapshot_hash
    )
    assert observation.context_content_commitment_hash == (
        request.authorized_market_context.context_content_commitment_hash
    )
    assert observation.projection_observation_id.startswith("ifpobs-")
    assert observation.eligible_for_trading is False

    rows = projection.as_rows()
    assert len(rows) == 1
    row = rows[0]
    assert tuple(row) == (*INTELLIGENCE_FEATURE_PROJECTION_PROVENANCE_COLUMNS, *INTELLIGENCE_FEATURE_PROJECTION_VALUE_COLUMNS)
    assert row["collection_schema"] == INTELLIGENCE_FEATURE_PROJECTION_SCHEMA_VERSION
    assert row["event_id"] == request.event.event_id
    assert row["commodity_id"] == "copper"
    assert row["event_time"] == request.event_time
    assert row["available_at"] == request.available_at
    assert {column for column in row if column.endswith("_input")} == set(
        INTELLIGENCE_FEATURE_PROJECTION_SCORE_COLUMNS
    )
    assert row["supply_risk_1h_input"] == 0.1
    assert row["contextual_impact_missing_reason"] is None
    with pytest.raises(TypeError):
        row["event_id"] = "different-event"  # type: ignore[index]

    feature_rows = projection.as_feature_input_rows()
    assert len(feature_rows) == 1
    feature_row = feature_rows[0]
    assert tuple(feature_row) == (
        "commodity_id",
        "projection_observation_id",
        "event_time",
        "available_at",
        *INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS,
    )
    assert tuple(
        key for key in feature_row if key.endswith("_input")
    ) == INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS
    assert tuple(
        key for key in feature_row if key.endswith("_missing_reason")
    ) == INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS
    assert all(column in feature_row for column in INTELLIGENCE_FEATURE_INPUT_PROVENANCE_COLUMNS)
    assert "event_id" not in feature_row
    assert "source_artifact_snapshot_hash" not in feature_row
    assert "context_artifact_snapshot_hash" not in feature_row
    assert "context_content_commitment_hash" not in feature_row

    assert projection.canonical_payload == IntelligenceFeatureProjector().project(request).canonical_payload
    assert _hash("canonical-payload-prefix") != projection.projection_hash
    assert b"https://" not in projection.canonical_payload
    assert b"rationale" not in projection.canonical_payload


def test_event_evidence_requires_an_exact_immutable_source_snapshot_hash():
    with pytest.raises(
        IntelligenceFeatureProjectionError,
        match="source_artifact_snapshot_hash",
    ):
        EventEvidenceAvailability(
            "document-one",
            _hash("document-one"),
            0,
            1,
            datetime(2026, 8, 22, 10, tzinfo=UTC),
            _hash("document-one-publication-receipt"),
            "not-a-sha256-snapshot-hash",
        )


def test_projection_is_deterministic_and_canonicalizes_evidence_metric_and_receipt_order():
    request = _request()
    reordered = IntelligenceFeatureProjectionRequest(
        projection_version=request.projection_version,
        ontology=request.ontology,
        event=request.event,
        mechanism=request.mechanism,
        selected_impact=request.selected_impact,
        event_evidence=tuple(reversed(request.event_evidence)),
        authorized_market_context=request.authorized_market_context,
        event_time=request.event_time,
        available_at=request.available_at,
        metric_values=tuple(reversed(request.metric_values)),
        publication_receipt_hashes=tuple(reversed(request.publication_receipt_hashes)),
    )

    projector = IntelligenceFeatureProjector()
    first = projector.project(request)
    second = projector.project(reordered)

    assert first == second
    assert first.projection_hash == second.projection_hash
    assert first.as_rows() == second.as_rows()
    assert first.canonical_payload == second.canonical_payload
    with pytest.raises(FrozenInstanceError):
        first.observations[0].event_id = "changed-event"  # type: ignore[misc]


def test_missing_metric_uses_explicit_closed_code_and_is_preserved_as_null_input():
    values = list(_metric_values())
    values[0] = IntelligenceMetricValue(
        kind=values[0].kind,
        score=None,
        missing_reason="input_missing",
    )

    projection = IntelligenceFeatureProjector().project(_request(metric_values=tuple(values)))
    row = projection.as_rows()[0]
    kind = values[0].kind.value

    assert row[f"{kind}_input"] is None
    assert row[f"{kind}_missing_reason"] == "input_missing"
    feature_row = projection.as_feature_input_rows()[0]
    assert feature_row[f"{kind}_input"] is None
    assert feature_row[f"{kind}_missing_reason"] == "input_missing"
    with pytest.raises(IntelligenceFeatureProjectionError, match="missing_reason"):
        IntelligenceMetricValue(IntelligenceMetricKind.DEMAND_SHOCK, None, "source unavailable")
    with pytest.raises(IntelligenceFeatureProjectionError, match="closed"):
        IntelligenceMetricValue(IntelligenceMetricKind.DEMAND_SHOCK, None, "unknown_missing")
    with pytest.raises(IntelligenceFeatureProjectionError, match="only allowed"):
        IntelligenceMetricValue(IntelligenceMetricKind.DEMAND_SHOCK, 0.5, "input_missing")
    with pytest.raises(IntelligenceFeatureProjectionError, match=r"\[0, 1\]"):
        IntelligenceMetricValue(IntelligenceMetricKind.DEMAND_SHOCK, 1.01)


@pytest.mark.parametrize(
    ("event_time", "available_at", "message"),
    (
        (datetime(2026, 8, 22, 10), datetime(2026, 8, 22, 10, 15, tzinfo=UTC), "timezone"),
        (
            datetime(2026, 8, 22, 10, 16, tzinfo=UTC),
            datetime(2026, 8, 22, 10, 15, tzinfo=UTC),
            "event_time cannot",
        ),
    ),
)
def test_request_rejects_naive_or_reverse_explicit_times(
    event_time: datetime,
    available_at: datetime,
    message: str,
):
    with pytest.raises(IntelligenceFeatureProjectionError, match=message):
        _request(event_time=event_time, available_at=available_at)


def test_request_rejects_future_evidence_or_market_context_at_projection_availability():
    request = _request()
    future_evidence = replace(
        request.event_evidence[0],
        available_at=request.available_at + timedelta(seconds=1),
    )
    with pytest.raises(IntelligenceFeatureProjectionError, match="evidence cannot be later"):
        _request(evidence=(future_evidence, request.event_evidence[1]))

    future_context = _context(available_at=request.available_at + timedelta(seconds=1))
    with pytest.raises(IntelligenceFeatureProjectionError, match="market context cannot be later"):
        _request(context=future_context)


def test_request_requires_exact_event_evidence_selected_impact_and_publication_receipts():
    request = _request()
    duplicate_evidence = (request.event_evidence[0], request.event_evidence[0])
    with pytest.raises(IntelligenceFeatureProjectionError, match="duplicate evidence"):
        _request(evidence=duplicate_evidence)

    mismatch = EventEvidenceAvailability(
        "document-other",
        _hash("document-other"),
        0,
        2,
        request.event_evidence[0].available_at,
        request.event_evidence[0].source_publication_receipt_hash,
        request.event_evidence[0].source_artifact_snapshot_hash,
    )
    with pytest.raises(IntelligenceFeatureProjectionError, match="exactly cover"):
        _request(evidence=(mismatch, request.event_evidence[1]))

    rubber_impact = Impact("impact-rubber-1", "rubber", "UP")
    rubber_event = Event(
        event_id="event-rubber-outage-1",
        ontology_version=request.event.ontology_version,
        evidence=request.event.evidence,
        mechanism=request.event.mechanism,
        impacts=(rubber_impact,),
    )
    with pytest.raises(IntelligenceFeatureProjectionError, match="commodity"):
        _request(event=rubber_event, impact=rubber_impact)

    with pytest.raises(IntelligenceFeatureProjectionError, match="publication_receipt_hashes"):
        _request(publication_receipt_hashes=(_hash("unrelated-publication-receipt"),))


def test_authorized_market_context_requires_the_exact_dataset_version_identity():
    with pytest.raises(
        IntelligenceFeatureProjectionError,
        match="dataset_version must exactly match",
    ):
        AuthorizedMarketContext(
            _context(),
            _hash("different-context-dataset"),
            _hash("market-context-publication-receipt"),
            _hash("market-context-artifact-snapshot"),
        )


def test_request_requires_a_typed_ontology_and_closed_semantic_bindings():
    request = _request()

    with pytest.raises(IntelligenceFeatureProjectionError, match="ontology must be an Ontology"):
        _request(ontology=cast(Ontology, object()))

    malformed_ontology = _ontology()
    object.__setattr__(malformed_ontology, "mechanisms", frozenset())
    with pytest.raises(IntelligenceFeatureProjectionError, match="ontology.mechanisms"):
        _request(ontology=malformed_ontology)

    with pytest.raises(IntelligenceFeatureProjectionError, match="event ontology_version"):
        _request(ontology=replace(request.ontology, version="v2"))

    unknown_mechanism_event = Event(
        event_id="event-copper-unknown-mechanism-1",
        ontology_version=request.event.ontology_version,
        evidence=request.event.evidence,
        mechanism=Mechanism("UNLISTED_MECHANISM", request.event.ontology_version),
        impacts=request.event.impacts,
    )
    with pytest.raises(IntelligenceFeatureProjectionError, match="mechanism_id must exist"):
        _request(event=unknown_mechanism_event)

    unknown_commodity_impact = Impact("impact-rubber-1", "rubber", "UP")
    unknown_commodity_event = Event(
        event_id="event-rubber-outage-1",
        ontology_version=request.event.ontology_version,
        evidence=request.event.evidence,
        mechanism=request.event.mechanism,
        impacts=(unknown_commodity_impact,),
    )
    with pytest.raises(IntelligenceFeatureProjectionError, match="commodity_id must exist"):
        _request(event=unknown_commodity_event, impact=unknown_commodity_impact)

    invalid_direction_impact = Impact("impact-copper-sideways-1", "copper", "SIDEWAYS")
    invalid_direction_event = Event(
        event_id="event-copper-sideways-1",
        ontology_version=request.event.ontology_version,
        evidence=request.event.evidence,
        mechanism=request.event.mechanism,
        impacts=(invalid_direction_impact,),
    )
    with pytest.raises(IntelligenceFeatureProjectionError, match="closed P4 impact directions"):
        _request(event=invalid_direction_event, impact=invalid_direction_impact)

    with pytest.raises(IntelligenceFeatureProjectionError, match="market context commodity_id"):
        _request(context=replace(_context(), commodity_id="rubber"))


def test_projection_identity_binds_the_full_canonical_ontology_contents():
    request = _request()
    expanded_ontology = replace(
        request.ontology,
        event_types=request.ontology.event_types | frozenset({"EXPERIMENTAL"}),
    )

    original = IntelligenceFeatureProjector().project(request)
    expanded = IntelligenceFeatureProjector().project(_request(ontology=expanded_ontology))

    assert original.observations[0].ontology_identity_hash != (
        expanded.observations[0].ontology_identity_hash
    )
    assert original.projection_hash != expanded.projection_hash


def test_request_requires_each_of_the_nine_metrics_once_and_rechecks_adversarial_mutation():
    request = _request()
    with pytest.raises(IntelligenceFeatureProjectionError, match="each IntelligenceMetricKind"):
        _request(metric_values=request.metric_values[:-1])
    duplicate = (*request.metric_values[:-1], request.metric_values[0])
    with pytest.raises(IntelligenceFeatureProjectionError, match="each IntelligenceMetricKind"):
        _request(metric_values=duplicate)

    object.__setattr__(
        request,
        "available_at",
        request.available_at - timedelta(minutes=11),
    )
    with pytest.raises(IntelligenceFeatureProjectionError, match="evidence cannot be later"):
        IntelligenceFeatureProjector().project(request)


def test_versioned_projection_rejects_a_replaced_observation_with_a_fresh_hash():
    projection = IntelligenceFeatureProjector().project(_request())
    observation = projection.observations[0]
    forged_metrics = list(observation.metric_values)
    forged_metrics[0] = IntelligenceMetricValue(
        kind=forged_metrics[0].kind,
        score=0.99,
    )
    forged_observation = replace(
        observation,
        metric_values=tuple(forged_metrics),
    )

    assert forged_observation.observation_hash != observation.observation_hash
    with pytest.raises(IntelligenceFeatureProjectionError, match="projection_hash.*identity"):
        VersionedIntelligenceFeatureProjection(
            projection_version=projection.projection_version,
            projection_hash=projection.projection_hash,
            observations=(forged_observation,),
        )


def test_versioned_projection_rejects_a_stale_observation_hash_after_mutation():
    projection = IntelligenceFeatureProjector().project(_request())
    observation = projection.observations[0]
    object.__setattr__(observation, "event_hash", _hash("forged-event"))

    with pytest.raises(IntelligenceFeatureProjectionError, match="observation_hash"):
        VersionedIntelligenceFeatureProjection(
            projection_version=projection.projection_version,
            projection_hash=projection.projection_hash,
            observations=(observation,),
        )


def test_versioned_projection_rejects_mixed_observation_availability():
    projection = IntelligenceFeatureProjector().project(_request())
    observation = projection.observations[0]
    later_observation = replace(
        observation,
        projection_observation_id="ifpobs-other",
        available_at=observation.available_at + timedelta(seconds=1),
    )

    with pytest.raises(IntelligenceFeatureProjectionError, match="canonical available_at"):
        VersionedIntelligenceFeatureProjection(
            projection_version=projection.projection_version,
            projection_hash=projection.projection_hash,
            observations=(observation, later_observation),
        )


def test_public_projection_dtos_are_slotted_immutable_and_exclude_control_or_raw_payload_fields():
    forbidden = {
        "document",
        "url",
        "payload",
        "rationale",
        "target",
        "order",
        "trade",
        "approval",
        "decision",
    }

    for dto in (IntelligenceFeatureProjectionObservation, VersionedIntelligenceFeatureProjection):
        assert hasattr(dto, "__slots__")
        assert not {
            field.name
            for field in fields(dto)
            if any(token in field.name.lower() for token in forbidden)
        }
