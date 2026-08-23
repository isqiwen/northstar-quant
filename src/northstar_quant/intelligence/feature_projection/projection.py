"""Fail-closed projection of P4 evidence into P2-ready feature input rows.

This boundary is deliberately pure.  It neither scores an event nor writes a
feature: callers supply the nine already-derived metric values and receive an
immutable, point-in-time receipt that can be published by an application
adapter.  The receipt contains only stable identifiers, timestamps, and
fingerprints; documents, source URLs, rationale, and trading authority do not
cross this boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import json
import math
import re
from types import MappingProxyType
from typing import Final, Literal, cast

from northstar_quant.data.artifacts.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
    require_sha256,
)
from northstar_quant.intelligence.context import MarketContextSnapshot
from northstar_quant.intelligence.domain import Event, Impact, Mechanism
from northstar_quant.intelligence.ontology import Ontology


__all__ = [
    "AuthorizedMarketContext",
    "EventEvidenceAvailability",
    "INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS",
    "INTELLIGENCE_FEATURE_INPUT_PROVENANCE_COLUMNS",
    "INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS",
    "INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS",
    "INTELLIGENCE_MARKET_CONTEXT_SNAPSHOT_ROW_COLUMNS",
    "INTELLIGENCE_METRIC_MISSING_REASONS",
    "INTELLIGENCE_FEATURE_PROJECTION_PROVENANCE_COLUMNS",
    "INTELLIGENCE_FEATURE_PROJECTION_SCHEMA_VERSION",
    "INTELLIGENCE_FEATURE_PROJECTION_SCORE_COLUMNS",
    "INTELLIGENCE_FEATURE_PROJECTION_VALUE_COLUMNS",
    "IntelligenceFeatureProjectionError",
    "IntelligenceFeatureProjectionObservation",
    "IntelligenceFeatureProjectionRequest",
    "IntelligenceFeatureProjector",
    "IntelligenceMetricKind",
    "IntelligenceMetricValue",
    "VersionedIntelligenceFeatureProjection",
]


INTELLIGENCE_FEATURE_PROJECTION_SCHEMA_VERSION: Final[
    Literal["intelligence_feature_projection_v3"]
] = "intelligence_feature_projection_v3"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
INTELLIGENCE_METRIC_MISSING_REASONS: Final[frozenset[str]] = frozenset(
    {
        "input_missing",
        "not_applicable",
        "not_available",
        "not_implemented",
    }
)
_IMPACT_DIRECTIONS = frozenset({"UP", "DOWN", "NEUTRAL"})

# ``dataset_version`` is deliberately absent: it is supplied by the exact
# immutable DatasetVersion selected for replay, then bound into the typed
# snapshot and its content commitment.  Every other MarketContextSnapshot
# field must be present in one canonical normalized row; no inferred/default
# context values are accepted at the P4-to-P1 composition boundary.
INTELLIGENCE_MARKET_CONTEXT_SNAPSHOT_ROW_COLUMNS: Final[tuple[str, ...]] = (
    "snapshot_id",
    "commodity_id",
    "market_id",
    "as_of",
    "available_at",
    "inventory",
    "term_structure",
    "basis",
    "positioning",
    "volatility",
    "usd",
    "cny",
    "macro_regime",
    "seasonality",
)


class IntelligenceFeatureProjectionError(ValueError):
    """Raised when an evidence-to-feature projection is unsafe or ambiguous."""


class IntelligenceMetricKind(StrEnum):
    """The complete closed set of P4 intelligence metrics consumed by P2."""

    SUPPLY_RISK_1H = "supply_risk_1h"
    SUPPLY_RISK_6H = "supply_risk_6h"
    SUPPLY_RISK_24H = "supply_risk_24h"
    DEMAND_SHOCK = "demand_shock"
    GEOPOLITICAL_RISK = "geopolitical_risk"
    INVENTORY_STRESS = "inventory_stress"
    EVENT_NOVELTY = "event_novelty"
    EVENT_CONFIDENCE = "event_confidence"
    CONTEXTUAL_IMPACT = "contextual_impact"


_METRIC_INDEX = {kind: index for index, kind in enumerate(IntelligenceMetricKind)}
INTELLIGENCE_FEATURE_PROJECTION_SCORE_COLUMNS = tuple(
    f"{kind.value}_input" for kind in IntelligenceMetricKind
)
INTELLIGENCE_FEATURE_PROJECTION_MISSING_REASON_COLUMNS = tuple(
    f"{kind.value}_missing_reason" for kind in IntelligenceMetricKind
)
INTELLIGENCE_FEATURE_PROJECTION_VALUE_COLUMNS = (
    *INTELLIGENCE_FEATURE_PROJECTION_SCORE_COLUMNS,
    *INTELLIGENCE_FEATURE_PROJECTION_MISSING_REASON_COLUMNS,
)
INTELLIGENCE_FEATURE_PROJECTION_PROVENANCE_COLUMNS = (
    "projection_observation_id",
    "event_id",
    "commodity_id",
    "event_time",
    "available_at",
    "event_hash",
    "evidence_bundle_hash",
    "ontology_version",
    "ontology_identity_hash",
    "mechanism_identity_hash",
    "impact_identity_hash",
    "context_identity_hash",
    "context_dataset_version_hash",
    "context_publication_receipt_hash",
    "context_artifact_snapshot_hash",
    "context_content_commitment_hash",
    "publication_receipt_bundle_hash",
    "projection_hash",
    "observation_hash",
    "projection_version",
    "collection_schema",
    "eligible_for_trading",
)
INTELLIGENCE_FEATURE_INPUT_PROVENANCE_COLUMNS = (
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
INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS = INTELLIGENCE_FEATURE_PROJECTION_SCORE_COLUMNS
INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS = (
    INTELLIGENCE_FEATURE_PROJECTION_MISSING_REASON_COLUMNS
)
INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS = (
    *INTELLIGENCE_FEATURE_INPUT_PROVENANCE_COLUMNS,
    *INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS,
    *INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS,
)


def _identifier(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or value.strip() != value
        or _IDENTIFIER_PATTERN.fullmatch(value) is None
    ):
        raise IntelligenceFeatureProjectionError(
            f"{field_name} must be a non-empty stable identifier"
        )
    return value


def _sha256(value: object, field_name: str) -> str:
    try:
        return require_sha256(cast(str, value), field_name=field_name)
    except FingerprintError as exc:
        raise IntelligenceFeatureProjectionError(
            f"{field_name} must be a lowercase SHA-256 hash"
        ) from exc


def _time(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise IntelligenceFeatureProjectionError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise IntelligenceFeatureProjectionError(f"{field_name} must be an integer")
    return value


def _hash_payload(namespace: str, payload: Mapping[str, object]) -> str:
    try:
        return canonical_json_sha256({"namespace": namespace, "payload": dict(payload)})
    except FingerprintError as exc:
        raise IntelligenceFeatureProjectionError("projection payload must be canonical JSON") from exc


def _canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise IntelligenceFeatureProjectionError("projection payload must be canonical JSON") from exc


def _metric_values(value: object, field_name: str) -> tuple[IntelligenceMetricValue, ...]:
    if not isinstance(value, tuple) or not all(type(item) is IntelligenceMetricValue for item in value):
        raise IntelligenceFeatureProjectionError(
            f"{field_name} must be a tuple of IntelligenceMetricValue records"
        )
    try:
        metric_values = tuple(
            IntelligenceMetricValue(
                kind=cast(IntelligenceMetricValue, item).kind,
                score=cast(IntelligenceMetricValue, item).score,
                missing_reason=cast(IntelligenceMetricValue, item).missing_reason,
            )
            for item in value
        )
    except (TypeError, ValueError) as exc:
        raise IntelligenceFeatureProjectionError(
            f"{field_name} contains an invalid IntelligenceMetricValue"
        ) from exc
    if len(metric_values) != len(IntelligenceMetricKind) or {
        item.kind for item in metric_values
    } != set(IntelligenceMetricKind):
        raise IntelligenceFeatureProjectionError(
            f"{field_name} must contain each IntelligenceMetricKind exactly once"
        )
    return tuple(sorted(metric_values, key=lambda item: _METRIC_INDEX[item.kind]))


def _publication_receipt_hashes(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, tuple):
        raise IntelligenceFeatureProjectionError(f"{field_name} must be a tuple of SHA-256 hashes")
    hashes = tuple(_sha256(item, field_name) for item in value)
    if not hashes:
        raise IntelligenceFeatureProjectionError(f"{field_name} cannot be empty")
    if len(set(hashes)) != len(hashes):
        raise IntelligenceFeatureProjectionError(f"{field_name} cannot contain duplicates")
    return tuple(sorted(hashes))


def _ontology_members(value: object, field_name: str) -> frozenset[str]:
    """Return one exact, non-empty ontology vocabulary without trusting mutation."""

    if type(value) is not frozenset or not value:
        raise IntelligenceFeatureProjectionError(
            f"{field_name} must be a non-empty frozenset of stable identifiers"
        )
    try:
        return frozenset(_identifier(item, field_name) for item in value)
    except IntelligenceFeatureProjectionError:
        raise


def _canonical_ontology(value: object) -> Ontology:
    """Rebuild a typed ontology so request semantics cannot rely on opaque IDs."""

    if type(value) is not Ontology:
        raise IntelligenceFeatureProjectionError("ontology must be an Ontology")
    try:
        return Ontology(
            version=_identifier(value.version, "ontology.version"),
            event_types=_ontology_members(value.event_types, "ontology.event_types"),
            mechanisms=_ontology_members(value.mechanisms, "ontology.mechanisms"),
            entity_types=_ontology_members(value.entity_types, "ontology.entity_types"),
            commodities=_ontology_members(value.commodities, "ontology.commodities"),
            relations=_ontology_members(value.relations, "ontology.relations"),
        )
    except IntelligenceFeatureProjectionError:
        raise
    except (AttributeError, TypeError, ValueError) as exc:
        raise IntelligenceFeatureProjectionError(
            "ontology must retain valid versioned Ontology semantics"
        ) from exc


@dataclass(frozen=True, slots=True)
class IntelligenceMetricValue:
    """One explicit bounded score or a closed-code declaration that it is missing."""

    kind: IntelligenceMetricKind
    score: float | None
    missing_reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not IntelligenceMetricKind:
            raise IntelligenceFeatureProjectionError("kind must be an IntelligenceMetricKind")
        if self.score is None:
            if (
                not isinstance(self.missing_reason, str)
                or self.missing_reason.strip() != self.missing_reason
                or self.missing_reason not in INTELLIGENCE_METRIC_MISSING_REASONS
            ):
                raise IntelligenceFeatureProjectionError(
                    "missing_reason must be an explicit closed missing-data code"
                )
            return
        if (
            not isinstance(self.score, (int, float))
            or isinstance(self.score, bool)
            or not math.isfinite(self.score)
            or not 0 <= self.score <= 1
        ):
            raise IntelligenceFeatureProjectionError("score must be a finite number in [0, 1]")
        if self.missing_reason is not None:
            raise IntelligenceFeatureProjectionError(
                "missing_reason is only allowed when score is absent"
            )
        object.__setattr__(self, "score", float(self.score))


@dataclass(frozen=True, slots=True)
class EventEvidenceAvailability:
    """PIT availability and immutable P1 source binding for one Event evidence span.

    ``source_artifact_snapshot_hash`` is deliberately an exact immutable P1
    artifact reference, rather than a mutable document lookup or a receipt
    bundle hint.  The application composition boundary can therefore replay
    the one source payload that proves this document hash and span without
    carrying that payload into P2 feature rows.
    """

    document_id: str
    content_hash: str
    span_start: int
    span_end: int
    available_at: datetime
    source_publication_receipt_hash: str
    source_artifact_snapshot_hash: str
    evidence_identity_hash: str = field(init=False)

    def __post_init__(self) -> None:
        document_id = _identifier(self.document_id, "document_id")
        content_hash = _sha256(self.content_hash, "content_hash")
        span_start = _int(self.span_start, "span_start")
        span_end = _int(self.span_end, "span_end")
        if span_start < 0 or span_end <= span_start:
            raise IntelligenceFeatureProjectionError("evidence spans must be non-empty and ordered")
        available_at = _time(self.available_at, "available_at")
        source_publication_receipt_hash = _sha256(
            self.source_publication_receipt_hash,
            "source_publication_receipt_hash",
        )
        source_artifact_snapshot_hash = _sha256(
            self.source_artifact_snapshot_hash,
            "source_artifact_snapshot_hash",
        )
        object.__setattr__(self, "document_id", document_id)
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "span_start", span_start)
        object.__setattr__(self, "span_end", span_end)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(
            self,
            "source_publication_receipt_hash",
            source_publication_receipt_hash,
        )
        object.__setattr__(
            self,
            "source_artifact_snapshot_hash",
            source_artifact_snapshot_hash,
        )
        object.__setattr__(
            self,
            "evidence_identity_hash",
            _hash_payload(
                "northstar.intelligence-feature-projection.event-evidence.v3",
                {
                    "source_publication_receipt_hash": source_publication_receipt_hash,
                    "available_at": available_at.isoformat(),
                    "content_hash": content_hash,
                    "document_id": document_id,
                    "source_artifact_snapshot_hash": source_artifact_snapshot_hash,
                    "span_end": span_end,
                    "span_start": span_start,
                },
            ),
        )


@dataclass(frozen=True, slots=True)
class AuthorizedMarketContext:
    """One P4 context snapshot bound to one immutable P1 normalized artifact.

    The P4 receipt retains hashes only.  Its full context values are committed
    here so the application boundary can reconstruct exactly one normalized
    P1 row from the declared DatasetVersion and prove that every snapshot
    field originated there before any P2 feature input is published.
    """

    market_context: MarketContextSnapshot
    context_dataset_version_hash: str
    context_publication_receipt_hash: str
    context_artifact_snapshot_hash: str
    context_content_commitment_hash: str = field(init=False)
    context_identity_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.market_context) is not MarketContextSnapshot:
            raise IntelligenceFeatureProjectionError(
                "market_context must be a MarketContextSnapshot"
            )
        dataset_version_hash = _sha256(
            self.context_dataset_version_hash,
            "context_dataset_version_hash",
        )
        context_publication_receipt_hash = _sha256(
            self.context_publication_receipt_hash,
            "context_publication_receipt_hash",
        )
        context_artifact_snapshot_hash = _sha256(
            self.context_artifact_snapshot_hash,
            "context_artifact_snapshot_hash",
        )
        context = self.market_context
        if context.dataset_version != dataset_version_hash:
            raise IntelligenceFeatureProjectionError(
                "market_context.dataset_version must exactly match context_dataset_version_hash"
            )
        context_content_commitment_hash = _hash_payload(
            "northstar.intelligence-feature-projection.market-context-content.v3",
            {
                "as_of": context.as_of.isoformat(),
                "available_at": context.available_at.isoformat(),
                "basis": context.basis,
                "cny": context.cny,
                "commodity_id": context.commodity_id,
                "context_artifact_snapshot_hash": context_artifact_snapshot_hash,
                "context_dataset_version_hash": dataset_version_hash,
                "dataset_version": context.dataset_version,
                "inventory": context.inventory,
                "macro_regime": context.macro_regime,
                "market_id": context.market_id,
                "positioning": context.positioning,
                "seasonality": context.seasonality,
                "snapshot_id": context.snapshot_id,
                "term_structure": context.term_structure,
                "usd": context.usd,
                "volatility": context.volatility,
            },
        )
        object.__setattr__(self, "context_dataset_version_hash", dataset_version_hash)
        object.__setattr__(
            self,
            "context_publication_receipt_hash",
            context_publication_receipt_hash,
        )
        object.__setattr__(
            self,
            "context_artifact_snapshot_hash",
            context_artifact_snapshot_hash,
        )
        object.__setattr__(
            self,
            "context_content_commitment_hash",
            context_content_commitment_hash,
        )
        object.__setattr__(
            self,
            "context_identity_hash",
            _hash_payload(
                "northstar.intelligence-feature-projection.market-context.v3",
                {
                    "context_artifact_snapshot_hash": context_artifact_snapshot_hash,
                    "context_content_commitment_hash": context_content_commitment_hash,
                    "context_publication_receipt_hash": context_publication_receipt_hash,
                    "context_dataset_version_hash": dataset_version_hash,
                },
            ),
        )


def _event_evidence(value: object) -> tuple[EventEvidenceAvailability, ...]:
    if not isinstance(value, tuple) or not all(type(item) is EventEvidenceAvailability for item in value):
        raise IntelligenceFeatureProjectionError(
            "event_evidence must be a tuple of EventEvidenceAvailability records"
        )
    try:
        evidence = tuple(
            EventEvidenceAvailability(
                document_id=cast(EventEvidenceAvailability, item).document_id,
                content_hash=cast(EventEvidenceAvailability, item).content_hash,
                span_start=cast(EventEvidenceAvailability, item).span_start,
                span_end=cast(EventEvidenceAvailability, item).span_end,
                available_at=cast(EventEvidenceAvailability, item).available_at,
                source_publication_receipt_hash=cast(
                    EventEvidenceAvailability,
                    item,
                ).source_publication_receipt_hash,
                source_artifact_snapshot_hash=cast(
                    EventEvidenceAvailability,
                    item,
                ).source_artifact_snapshot_hash,
            )
            for item in value
        )
    except (TypeError, ValueError) as exc:
        raise IntelligenceFeatureProjectionError(
            "event_evidence contains an invalid EventEvidenceAvailability"
        ) from exc
    keys = tuple(
        (item.document_id, item.content_hash, item.span_start, item.span_end) for item in evidence
    )
    if not evidence or len(set(keys)) != len(keys):
        raise IntelligenceFeatureProjectionError("event_evidence cannot be empty or duplicate evidence")
    return tuple(sorted(evidence, key=lambda item: item.evidence_identity_hash))


def _event_evidence_keys(event: Event) -> tuple[tuple[str, str, int, int], ...]:
    return tuple(
        (item.document_id, item.content_hash, item.span_start, item.span_end) for item in event.evidence
    )


def _canonical_event(event: object) -> Event:
    if type(event) is not Event:
        raise IntelligenceFeatureProjectionError("event must be an Event")
    try:
        mechanism = Mechanism(
            mechanism_id=event.mechanism.mechanism_id,
            ontology_version=event.mechanism.ontology_version,
        )
        impacts = tuple(
            Impact(
                impact_id=impact.impact_id,
                commodity_id=impact.commodity_id,
                direction=impact.direction,
            )
            for impact in event.impacts
        )
        canonical_event = Event(
            event_id=event.event_id,
            ontology_version=event.ontology_version,
            evidence=event.evidence,
            mechanism=mechanism,
            impacts=impacts,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise IntelligenceFeatureProjectionError("event must retain valid P4 Event semantics") from exc
    if _sha256(event.event_hash, "event_hash") != canonical_event.event_hash:
        raise IntelligenceFeatureProjectionError("event_hash must match the exact Event contents")
    return canonical_event


def _canonical_authorized_market_context(value: object) -> AuthorizedMarketContext:
    if type(value) is not AuthorizedMarketContext:
        raise IntelligenceFeatureProjectionError(
            "authorized_market_context must be an AuthorizedMarketContext"
        )
    context = value.market_context
    if type(context) is not MarketContextSnapshot:
        raise IntelligenceFeatureProjectionError("market_context must be a MarketContextSnapshot")
    try:
        canonical_context = MarketContextSnapshot(
            snapshot_id=context.snapshot_id,
            commodity_id=context.commodity_id,
            market_id=context.market_id,
            dataset_version=context.dataset_version,
            as_of=context.as_of,
            available_at=context.available_at,
            inventory=context.inventory,
            term_structure=context.term_structure,
            basis=context.basis,
            positioning=context.positioning,
            volatility=context.volatility,
            usd=context.usd,
            cny=context.cny,
            macro_regime=context.macro_regime,
            seasonality=context.seasonality,
        )
        canonical_context_receipt = AuthorizedMarketContext(
            market_context=canonical_context,
            context_dataset_version_hash=value.context_dataset_version_hash,
            context_publication_receipt_hash=value.context_publication_receipt_hash,
            context_artifact_snapshot_hash=value.context_artifact_snapshot_hash,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise IntelligenceFeatureProjectionError(
            "authorized_market_context must retain valid P4 context semantics"
        ) from exc
    if value.context_identity_hash != canonical_context_receipt.context_identity_hash:
        raise IntelligenceFeatureProjectionError(
            "context_identity_hash must match the exact market context contents"
        )
    if (
        value.context_content_commitment_hash
        != canonical_context_receipt.context_content_commitment_hash
    ):
        raise IntelligenceFeatureProjectionError(
            "context_content_commitment_hash must match the exact market context contents"
        )
    return canonical_context_receipt


def _validate_event_bindings(
    *,
    ontology: Ontology,
    event: Event,
    mechanism: Mechanism,
    selected_impact: Impact,
    evidence: tuple[EventEvidenceAvailability, ...],
    authorized_market_context: AuthorizedMarketContext,
    event_time: datetime,
    available_at: datetime,
    publication_receipt_hashes: tuple[str, ...],
) -> None:
    if type(ontology) is not Ontology:
        raise IntelligenceFeatureProjectionError("ontology must be an Ontology")
    if type(event) is not Event:
        raise IntelligenceFeatureProjectionError("event must be an Event")
    if event.ontology_version != ontology.version:
        raise IntelligenceFeatureProjectionError(
            "event ontology_version must exactly match the supplied ontology"
        )
    if type(mechanism) is not Mechanism or mechanism != event.mechanism:
        raise IntelligenceFeatureProjectionError("mechanism must exactly match event.mechanism")
    if mechanism.ontology_version != ontology.version:
        raise IntelligenceFeatureProjectionError(
            "mechanism ontology_version must exactly match the supplied ontology"
        )
    if mechanism.mechanism_id not in ontology.mechanisms:
        raise IntelligenceFeatureProjectionError(
            "mechanism_id must exist in the supplied ontology"
        )
    if type(selected_impact) is not Impact:
        raise IntelligenceFeatureProjectionError("selected_impact must be an Impact")
    if sum(item == selected_impact for item in event.impacts) != 1:
        raise IntelligenceFeatureProjectionError("selected_impact must occur exactly once in event.impacts")
    for impact in event.impacts:
        if impact.commodity_id not in ontology.commodities:
            raise IntelligenceFeatureProjectionError(
                "event impact commodity_id must exist in the supplied ontology"
            )
        if impact.direction not in _IMPACT_DIRECTIONS:
            raise IntelligenceFeatureProjectionError(
                "event impact direction must be one of the closed P4 impact directions"
            )
    context = authorized_market_context.market_context
    if context.commodity_id not in ontology.commodities:
        raise IntelligenceFeatureProjectionError(
            "market context commodity_id must exist in the supplied ontology"
        )
    if selected_impact.commodity_id != context.commodity_id:
        raise IntelligenceFeatureProjectionError(
            "selected_impact commodity must exactly match market context commodity"
        )
    event_keys = _event_evidence_keys(event)
    if len(set(event_keys)) != len(event_keys):
        raise IntelligenceFeatureProjectionError("event cannot contain duplicate evidence")
    evidence_keys = tuple(
        (item.document_id, item.content_hash, item.span_start, item.span_end) for item in evidence
    )
    if set(evidence_keys) != set(event_keys):
        raise IntelligenceFeatureProjectionError(
            "event_evidence must exactly cover the Event evidence records"
        )
    if event_time > available_at:
        raise IntelligenceFeatureProjectionError("event_time cannot be later than available_at")
    if any(item.available_at > available_at for item in evidence):
        raise IntelligenceFeatureProjectionError("event evidence cannot be later than available_at")
    if context.available_at > available_at:
        raise IntelligenceFeatureProjectionError("market context cannot be later than available_at")
    if context.as_of > event_time:
        raise IntelligenceFeatureProjectionError("market context as_of cannot be later than event_time")
    expected_publication_receipts = tuple(
        sorted(
            {
                *(item.source_publication_receipt_hash for item in evidence),
                authorized_market_context.context_publication_receipt_hash,
            }
        )
    )
    if publication_receipt_hashes != expected_publication_receipts:
        raise IntelligenceFeatureProjectionError(
            "publication_receipt_hashes must exactly cover event and context receipts"
        )


@dataclass(frozen=True, slots=True)
class IntelligenceFeatureProjectionRequest:
    """Complete, authorized, point-in-time input for one Event/context projection."""

    projection_version: str
    ontology: Ontology
    event: Event
    mechanism: Mechanism
    selected_impact: Impact
    event_evidence: tuple[EventEvidenceAvailability, ...]
    authorized_market_context: AuthorizedMarketContext
    event_time: datetime
    available_at: datetime
    metric_values: tuple[IntelligenceMetricValue, ...]
    publication_receipt_hashes: tuple[str, ...]

    def __post_init__(self) -> None:
        projection_version = _identifier(self.projection_version, "projection_version")
        ontology = _canonical_ontology(self.ontology)
        event = _canonical_event(self.event)
        authorized_market_context = _canonical_authorized_market_context(
            self.authorized_market_context
        )
        event_time = _time(self.event_time, "event_time")
        available_at = _time(self.available_at, "available_at")
        evidence = _event_evidence(self.event_evidence)
        metric_values = _metric_values(self.metric_values, "metric_values")
        publication_receipt_hashes = _publication_receipt_hashes(
            self.publication_receipt_hashes,
            "publication_receipt_hashes",
        )
        _validate_event_bindings(
            ontology=ontology,
            event=event,
            mechanism=self.mechanism,
            selected_impact=self.selected_impact,
            evidence=evidence,
            authorized_market_context=authorized_market_context,
            event_time=event_time,
            available_at=available_at,
            publication_receipt_hashes=publication_receipt_hashes,
        )
        selected_impact = next(
            impact for impact in event.impacts if impact == self.selected_impact
        )
        object.__setattr__(self, "projection_version", projection_version)
        object.__setattr__(self, "ontology", ontology)
        object.__setattr__(self, "event", event)
        object.__setattr__(self, "mechanism", event.mechanism)
        object.__setattr__(self, "selected_impact", selected_impact)
        object.__setattr__(self, "authorized_market_context", authorized_market_context)
        object.__setattr__(self, "event_time", event_time)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "event_evidence", evidence)
        object.__setattr__(self, "metric_values", metric_values)
        object.__setattr__(
            self,
            "publication_receipt_hashes",
            publication_receipt_hashes,
        )


def _metric_payload(metric_values: tuple[IntelligenceMetricValue, ...]) -> list[dict[str, object]]:
    return [
        {
            "kind": item.kind.value,
            "missing_reason": item.missing_reason,
            "score": item.score,
        }
        for item in metric_values
    ]


def _event_evidence_payload(
    evidence: tuple[EventEvidenceAvailability, ...],
) -> list[dict[str, object]]:
    """Return the canonical, hash-only source proof retained in a P4 receipt."""

    return [
        {
            "available_at": item.available_at.isoformat(),
            "content_hash": item.content_hash,
            "document_id": item.document_id,
            "source_artifact_snapshot_hash": item.source_artifact_snapshot_hash,
            "source_publication_receipt_hash": item.source_publication_receipt_hash,
            "span_end": item.span_end,
            "span_start": item.span_start,
        }
        for item in evidence
    ]


def _evidence_bundle_hash(
    evidence: tuple[EventEvidenceAvailability, ...],
) -> str:
    """Bind every source document proof, not only an aggregate receipt list."""

    return _hash_payload(
        "northstar.intelligence-feature-projection.evidence-bundle.v3",
        {
            "evidence_identity_hashes": [
                item.evidence_identity_hash for item in evidence
            ]
        },
    )


def _projection_identity_hash(
    *,
    projection_version: str,
    publication_receipt_bundle_hash: str,
    available_at: datetime,
    context_artifact_snapshot_hash: str,
    context_content_commitment_hash: str,
    context_publication_receipt_hash: str,
    context_dataset_version_hash: str,
    context_identity_hash: str,
    event_hash: str,
    event_time: datetime,
    evidence_bundle_hash: str,
    impact_identity_hash: str,
    metric_values: tuple[IntelligenceMetricValue, ...],
    mechanism_identity_hash: str,
    ontology_identity_hash: str,
    ontology_version: str,
) -> str:
    """Hash the one stable P4 identity shared by projector and collection validation."""

    return _hash_payload(
        "northstar.intelligence-feature-projection.v3",
        {
            "publication_receipt_bundle_hash": publication_receipt_bundle_hash,
            "available_at": available_at.isoformat(),
            "context_artifact_snapshot_hash": context_artifact_snapshot_hash,
            "context_content_commitment_hash": context_content_commitment_hash,
            "context_publication_receipt_hash": context_publication_receipt_hash,
            "context_dataset_version_hash": context_dataset_version_hash,
            "context_identity_hash": context_identity_hash,
            "event_hash": event_hash,
            "event_time": event_time.isoformat(),
            "evidence_bundle_hash": evidence_bundle_hash,
            "impact_identity_hash": impact_identity_hash,
            "metric_values": _metric_payload(metric_values),
            "mechanism_identity_hash": mechanism_identity_hash,
            "ontology_identity_hash": ontology_identity_hash,
            "ontology_version": ontology_version,
            "projection_version": projection_version,
        },
    )


def _projection_identities(
    request: IntelligenceFeatureProjectionRequest,
) -> dict[str, str]:
    evidence_bundle_hash = _evidence_bundle_hash(request.event_evidence)
    ontology_identity_hash = _hash_payload(
        "northstar.intelligence-feature-projection.ontology.v3",
        {
            "commodities": sorted(request.ontology.commodities),
            "entity_types": sorted(request.ontology.entity_types),
            "event_types": sorted(request.ontology.event_types),
            "mechanisms": sorted(request.ontology.mechanisms),
            "relations": sorted(request.ontology.relations),
            "version": request.ontology.version,
        },
    )
    mechanism_identity_hash = _hash_payload(
        "northstar.intelligence-feature-projection.mechanism.v3",
        {
            "mechanism_id": request.mechanism.mechanism_id,
            "ontology_version": request.mechanism.ontology_version,
        },
    )
    impact_identity_hash = _hash_payload(
        "northstar.intelligence-feature-projection.impact.v3",
        {
            "commodity_id": request.selected_impact.commodity_id,
            "direction": request.selected_impact.direction,
            "impact_id": request.selected_impact.impact_id,
        },
    )
    publication_receipt_bundle_hash = _hash_payload(
        "northstar.intelligence-feature-projection.publication-receipt-bundle.v3",
        {"publication_receipt_hashes": list(request.publication_receipt_hashes)},
    )
    projection_hash = _projection_identity_hash(
        projection_version=request.projection_version,
        publication_receipt_bundle_hash=publication_receipt_bundle_hash,
        available_at=request.available_at,
        context_artifact_snapshot_hash=(
            request.authorized_market_context.context_artifact_snapshot_hash
        ),
        context_content_commitment_hash=(
            request.authorized_market_context.context_content_commitment_hash
        ),
        context_publication_receipt_hash=(
            request.authorized_market_context.context_publication_receipt_hash
        ),
        context_dataset_version_hash=(
            request.authorized_market_context.context_dataset_version_hash
        ),
        context_identity_hash=request.authorized_market_context.context_identity_hash,
        event_hash=request.event.event_hash,
        event_time=request.event_time,
        evidence_bundle_hash=evidence_bundle_hash,
        impact_identity_hash=impact_identity_hash,
        metric_values=request.metric_values,
        mechanism_identity_hash=mechanism_identity_hash,
        ontology_identity_hash=ontology_identity_hash,
        ontology_version=request.event.ontology_version,
    )
    return {
        "publication_receipt_bundle_hash": publication_receipt_bundle_hash,
        "evidence_bundle_hash": evidence_bundle_hash,
        "impact_identity_hash": impact_identity_hash,
        "mechanism_identity_hash": mechanism_identity_hash,
        "ontology_identity_hash": ontology_identity_hash,
        "projection_hash": projection_hash,
    }


@dataclass(frozen=True, slots=True)
class IntelligenceFeatureProjectionObservation:
    """One P1-row-ready, hash-only feature vector bound to P4 provenance."""

    projection_observation_id: str
    event_id: str
    commodity_id: str
    event_time: datetime
    available_at: datetime
    event_hash: str
    evidence_bundle_hash: str
    source_publication_receipt_hashes: tuple[str, ...]
    event_evidence: tuple[EventEvidenceAvailability, ...]
    ontology_version: str
    ontology_identity_hash: str
    mechanism_identity_hash: str
    impact_identity_hash: str
    context_identity_hash: str
    context_dataset_version_hash: str
    context_publication_receipt_hash: str
    context_artifact_snapshot_hash: str
    context_content_commitment_hash: str
    publication_receipt_bundle_hash: str
    projection_hash: str
    metric_values: tuple[IntelligenceMetricValue, ...]
    observation_hash: str = field(init=False)
    collection_schema: Literal["intelligence_feature_projection_v3"] = field(
        default=INTELLIGENCE_FEATURE_PROJECTION_SCHEMA_VERSION,
        init=False,
    )
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        projection_observation_id = _identifier(
            self.projection_observation_id,
            "projection_observation_id",
        )
        event_id = _identifier(self.event_id, "event_id")
        commodity_id = _identifier(self.commodity_id, "commodity_id")
        event_time = _time(self.event_time, "event_time")
        available_at = _time(self.available_at, "available_at")
        if event_time > available_at:
            raise IntelligenceFeatureProjectionError("event_time cannot be later than available_at")
        fields = (
            "event_hash",
            "evidence_bundle_hash",
            "ontology_identity_hash",
            "mechanism_identity_hash",
            "impact_identity_hash",
            "context_identity_hash",
            "context_dataset_version_hash",
            "context_publication_receipt_hash",
            "context_artifact_snapshot_hash",
            "context_content_commitment_hash",
            "publication_receipt_bundle_hash",
            "projection_hash",
        )
        values = {name: _sha256(getattr(self, name), name) for name in fields}
        source_publication_receipt_hashes = _publication_receipt_hashes(
            self.source_publication_receipt_hashes,
            "source_publication_receipt_hashes",
        )
        event_evidence = _event_evidence(self.event_evidence)
        expected_evidence_bundle_hash = _evidence_bundle_hash(event_evidence)
        if values["evidence_bundle_hash"] != expected_evidence_bundle_hash:
            raise IntelligenceFeatureProjectionError(
                "evidence_bundle_hash must exactly bind every immutable source evidence record"
            )
        expected_source_receipt_hashes = tuple(
            sorted(
                {
                    item.source_publication_receipt_hash
                    for item in event_evidence
                }
            )
        )
        if source_publication_receipt_hashes != expected_source_receipt_hashes:
            raise IntelligenceFeatureProjectionError(
                "source_publication_receipt_hashes must exactly bind retained event evidence"
            )
        expected_publication_receipt_bundle_hash = _hash_payload(
            "northstar.intelligence-feature-projection.publication-receipt-bundle.v3",
            {
                "publication_receipt_hashes": list(
                    sorted(
                        {
                            *source_publication_receipt_hashes,
                            values["context_publication_receipt_hash"],
                        }
                    )
                )
            },
        )
        if (
            values["publication_receipt_bundle_hash"]
            != expected_publication_receipt_bundle_hash
        ):
            raise IntelligenceFeatureProjectionError(
                "publication_receipt_bundle_hash must exactly bind source and context receipts"
            )
        ontology_version = _identifier(self.ontology_version, "ontology_version")
        metric_values = _metric_values(self.metric_values, "metric_values")
        object.__setattr__(self, "projection_observation_id", projection_observation_id)
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "commodity_id", commodity_id)
        object.__setattr__(self, "event_time", event_time)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(
            self,
            "source_publication_receipt_hashes",
            source_publication_receipt_hashes,
        )
        object.__setattr__(self, "event_evidence", event_evidence)
        object.__setattr__(self, "ontology_version", ontology_version)
        object.__setattr__(self, "metric_values", metric_values)
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(
            self,
            "observation_hash",
            _hash_payload(
                "northstar.intelligence-feature-projection.observation.v3",
                self._payload_without_observation_hash(),
            ),
        )

    def _payload_without_observation_hash(self) -> dict[str, object]:
        return {
            "publication_receipt_bundle_hash": self.publication_receipt_bundle_hash,
            "available_at": self.available_at.isoformat(),
            "collection_schema": self.collection_schema,
            "commodity_id": self.commodity_id,
            "context_publication_receipt_hash": self.context_publication_receipt_hash,
            "context_dataset_version_hash": self.context_dataset_version_hash,
            "context_artifact_snapshot_hash": self.context_artifact_snapshot_hash,
            "context_content_commitment_hash": self.context_content_commitment_hash,
            "context_identity_hash": self.context_identity_hash,
            "eligible_for_trading": False,
            "event_hash": self.event_hash,
            "event_id": self.event_id,
            "event_time": self.event_time.isoformat(),
            "evidence_bundle_hash": self.evidence_bundle_hash,
            "event_evidence": _event_evidence_payload(self.event_evidence),
            "impact_identity_hash": self.impact_identity_hash,
            "mechanism_identity_hash": self.mechanism_identity_hash,
            "metric_values": _metric_payload(self.metric_values),
            "ontology_identity_hash": self.ontology_identity_hash,
            "ontology_version": self.ontology_version,
            "projection_hash": self.projection_hash,
            "projection_observation_id": self.projection_observation_id,
            "source_publication_receipt_hashes": list(
                self.source_publication_receipt_hashes
            ),
        }

    def _row(self, *, projection_version: str) -> Mapping[str, object]:
        row: dict[str, object] = {
            "projection_observation_id": self.projection_observation_id,
            "event_id": self.event_id,
            "commodity_id": self.commodity_id,
            "event_time": self.event_time,
            "available_at": self.available_at,
            "event_hash": self.event_hash,
            "evidence_bundle_hash": self.evidence_bundle_hash,
            "ontology_version": self.ontology_version,
            "ontology_identity_hash": self.ontology_identity_hash,
            "mechanism_identity_hash": self.mechanism_identity_hash,
            "impact_identity_hash": self.impact_identity_hash,
            "context_identity_hash": self.context_identity_hash,
            "context_dataset_version_hash": self.context_dataset_version_hash,
            "context_publication_receipt_hash": self.context_publication_receipt_hash,
            "context_artifact_snapshot_hash": self.context_artifact_snapshot_hash,
            "context_content_commitment_hash": self.context_content_commitment_hash,
            "publication_receipt_bundle_hash": self.publication_receipt_bundle_hash,
            "projection_hash": self.projection_hash,
            "observation_hash": self.observation_hash,
            "projection_version": projection_version,
            "collection_schema": self.collection_schema,
            "eligible_for_trading": False,
        }
        for metric_value in self.metric_values:
            row[f"{metric_value.kind.value}_input"] = metric_value.score
        for metric_value in self.metric_values:
            row[f"{metric_value.kind.value}_missing_reason"] = metric_value.missing_reason
        return MappingProxyType(row)


def _canonical_observation(value: object) -> IntelligenceFeatureProjectionObservation:
    """Rebuild and verify one observation before it can enter a collection receipt."""

    if type(value) is not IntelligenceFeatureProjectionObservation:
        raise IntelligenceFeatureProjectionError(
            "observations must be IntelligenceFeatureProjectionObservation records"
        )
    try:
        canonical = IntelligenceFeatureProjectionObservation(
            projection_observation_id=value.projection_observation_id,
            event_id=value.event_id,
            commodity_id=value.commodity_id,
            event_time=value.event_time,
            available_at=value.available_at,
            event_hash=value.event_hash,
            evidence_bundle_hash=value.evidence_bundle_hash,
            source_publication_receipt_hashes=value.source_publication_receipt_hashes,
            event_evidence=value.event_evidence,
            ontology_version=value.ontology_version,
            ontology_identity_hash=value.ontology_identity_hash,
            mechanism_identity_hash=value.mechanism_identity_hash,
            impact_identity_hash=value.impact_identity_hash,
            context_identity_hash=value.context_identity_hash,
            context_dataset_version_hash=value.context_dataset_version_hash,
            context_publication_receipt_hash=value.context_publication_receipt_hash,
            context_artifact_snapshot_hash=value.context_artifact_snapshot_hash,
            context_content_commitment_hash=value.context_content_commitment_hash,
            publication_receipt_bundle_hash=value.publication_receipt_bundle_hash,
            projection_hash=value.projection_hash,
            metric_values=value.metric_values,
        )
    except IntelligenceFeatureProjectionError:
        raise
    except (AttributeError, TypeError, ValueError) as exc:
        raise IntelligenceFeatureProjectionError(
            "observation must retain valid P4 projection semantics"
        ) from exc
    if value.collection_schema != canonical.collection_schema:
        raise IntelligenceFeatureProjectionError(
            "observation collection_schema must retain the fixed P4 schema"
        )
    if value.eligible_for_trading is not False:
        raise IntelligenceFeatureProjectionError("observation must remain non-tradable")
    supplied_observation_hash = _sha256(value.observation_hash, "observation_hash")
    if supplied_observation_hash != canonical.observation_hash:
        raise IntelligenceFeatureProjectionError(
            "observation_hash must match the exact observation contents"
        )
    return canonical


def _projection_hash_from_observation(
    observation: IntelligenceFeatureProjectionObservation,
    *,
    projection_version: str,
) -> str:
    """Recompute the projector identity from a canonical collection observation."""

    return _projection_identity_hash(
        projection_version=projection_version,
        publication_receipt_bundle_hash=observation.publication_receipt_bundle_hash,
        available_at=observation.available_at,
        context_artifact_snapshot_hash=observation.context_artifact_snapshot_hash,
        context_content_commitment_hash=observation.context_content_commitment_hash,
        context_publication_receipt_hash=observation.context_publication_receipt_hash,
        context_dataset_version_hash=observation.context_dataset_version_hash,
        context_identity_hash=observation.context_identity_hash,
        event_hash=observation.event_hash,
        event_time=observation.event_time,
        evidence_bundle_hash=observation.evidence_bundle_hash,
        impact_identity_hash=observation.impact_identity_hash,
        metric_values=observation.metric_values,
        mechanism_identity_hash=observation.mechanism_identity_hash,
        ontology_identity_hash=observation.ontology_identity_hash,
        ontology_version=observation.ontology_version,
    )


def _observations(value: object) -> tuple[IntelligenceFeatureProjectionObservation, ...]:
    if (
        not isinstance(value, tuple)
        or not value
        or not all(type(item) is IntelligenceFeatureProjectionObservation for item in value)
    ):
        raise IntelligenceFeatureProjectionError(
            "observations must be a non-empty tuple of IntelligenceFeatureProjectionObservation"
        )
    observations = tuple(_canonical_observation(item) for item in value)
    ids = tuple(item.projection_observation_id for item in observations)
    if len(set(ids)) != len(ids):
        raise IntelligenceFeatureProjectionError("observations cannot contain duplicate IDs")
    return tuple(sorted(observations, key=lambda item: item.projection_observation_id))


@dataclass(frozen=True, slots=True)
class VersionedIntelligenceFeatureProjection:
    """A versioned, deterministic collection of non-tradable P1 input rows."""

    projection_version: str
    projection_hash: str
    observations: tuple[IntelligenceFeatureProjectionObservation, ...]
    collection_schema: Literal["intelligence_feature_projection_v3"] = field(
        default=INTELLIGENCE_FEATURE_PROJECTION_SCHEMA_VERSION,
        init=False,
    )
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        projection_version = _identifier(self.projection_version, "projection_version")
        projection_hash = _sha256(self.projection_hash, "projection_hash")
        observations = _observations(self.observations)
        if any(item.available_at != observations[0].available_at for item in observations[1:]):
            raise IntelligenceFeatureProjectionError(
                "observations must all share one canonical available_at"
            )
        for observation in observations:
            expected_projection_hash = _projection_hash_from_observation(
                observation,
                projection_version=projection_version,
            )
            if observation.projection_hash != expected_projection_hash:
                raise IntelligenceFeatureProjectionError(
                    "observation projection_hash must match the exact projection identity"
                )
            if projection_hash != expected_projection_hash:
                raise IntelligenceFeatureProjectionError(
                    "projection_hash must match every observation projection identity"
                )
        object.__setattr__(self, "projection_version", projection_version)
        object.__setattr__(self, "projection_hash", projection_hash)
        object.__setattr__(self, "observations", observations)

    @property
    def canonical_payload(self) -> bytes:
        """Return canonical, hash-only bytes for durable artifact identity or transport."""

        payload = {
            "collection_schema": self.collection_schema,
            "eligible_for_trading": False,
            "observations": [
                {
                    **item._payload_without_observation_hash(),
                    "observation_hash": item.observation_hash,
                }
                for item in self.observations
            ],
            "projection_hash": self.projection_hash,
            "projection_version": self.projection_version,
        }
        return _canonical_json_bytes(payload)

    @property
    def available_at(self) -> datetime:
        """The one PIT availability shared by every observation in this collection."""

        return self.observations[0].available_at

    def as_rows(self) -> tuple[Mapping[str, object], ...]:
        """Return canonically ordered immutable-safe P1 input rows.

        Every row has all nine ``*_input`` columns, even when a value is
        ``None``; the paired ``*_missing_reason`` fields preserve the explicit
        fail-closed missing-data declaration without exposing rationale text.
        """

        return tuple(item._row(projection_version=self.projection_version) for item in self.observations)

    def as_feature_input_rows(self) -> tuple[Mapping[str, object], ...]:
        """Return the strict, minimal P1 feature-input table projection.

        This deliberately excludes receipt-only fields.  The P2 canonical
        feature contract receives the stable row key, PIT columns, exact
        provenance bindings, every score, and the paired closed missing-data
        declaration needed to preserve feature semantics.
        """

        rows: list[Mapping[str, object]] = []
        for observation in self.observations:
            row: dict[str, object] = {
                "commodity_id": observation.commodity_id,
                "projection_observation_id": observation.projection_observation_id,
                "event_time": observation.event_time,
                "available_at": observation.available_at,
                "event_hash": observation.event_hash,
                "evidence_bundle_hash": observation.evidence_bundle_hash,
                "ontology_version": observation.ontology_version,
                "mechanism_identity_hash": observation.mechanism_identity_hash,
                "impact_identity_hash": observation.impact_identity_hash,
                "context_identity_hash": observation.context_identity_hash,
                "context_dataset_version_hash": observation.context_dataset_version_hash,
                "context_publication_receipt_hash": (
                    observation.context_publication_receipt_hash
                ),
                "projection_hash": observation.projection_hash,
            }
            for metric_value in observation.metric_values:
                row[f"{metric_value.kind.value}_input"] = metric_value.score
            for metric_value in observation.metric_values:
                row[f"{metric_value.kind.value}_missing_reason"] = (
                    metric_value.missing_reason
                )
            rows.append(MappingProxyType(row))
        return tuple(rows)


class IntelligenceFeatureProjector:
    """Pure projector that records supplied P4 metrics without inferring a trade."""

    __slots__ = ()

    def project(
        self,
        request: IntelligenceFeatureProjectionRequest,
    ) -> VersionedIntelligenceFeatureProjection:
        """Produce one deterministic, P1-row-ready feature observation."""

        if type(request) is not IntelligenceFeatureProjectionRequest:
            raise IntelligenceFeatureProjectionError(
                "request must be an IntelligenceFeatureProjectionRequest"
            )
        canonical_request = IntelligenceFeatureProjectionRequest(
            projection_version=request.projection_version,
            ontology=request.ontology,
            event=request.event,
            mechanism=request.mechanism,
            selected_impact=request.selected_impact,
            event_evidence=request.event_evidence,
            authorized_market_context=request.authorized_market_context,
            event_time=request.event_time,
            available_at=request.available_at,
            metric_values=request.metric_values,
            publication_receipt_hashes=request.publication_receipt_hashes,
        )
        identities = _projection_identities(canonical_request)
        projection_hash = identities["projection_hash"]
        observation = IntelligenceFeatureProjectionObservation(
            projection_observation_id=f"ifpobs-{projection_hash}",
            event_id=canonical_request.event.event_id,
            commodity_id=canonical_request.selected_impact.commodity_id,
            event_time=canonical_request.event_time,
            available_at=canonical_request.available_at,
            event_hash=canonical_request.event.event_hash,
            evidence_bundle_hash=identities["evidence_bundle_hash"],
            source_publication_receipt_hashes=tuple(
                sorted(
                    {
                        item.source_publication_receipt_hash
                        for item in canonical_request.event_evidence
                    }
                )
            ),
            event_evidence=canonical_request.event_evidence,
            ontology_version=canonical_request.event.ontology_version,
            ontology_identity_hash=identities["ontology_identity_hash"],
            mechanism_identity_hash=identities["mechanism_identity_hash"],
            impact_identity_hash=identities["impact_identity_hash"],
            context_identity_hash=canonical_request.authorized_market_context.context_identity_hash,
            context_dataset_version_hash=(
                canonical_request.authorized_market_context.context_dataset_version_hash
            ),
            context_publication_receipt_hash=(
                canonical_request.authorized_market_context.context_publication_receipt_hash
            ),
            context_artifact_snapshot_hash=(
                canonical_request.authorized_market_context.context_artifact_snapshot_hash
            ),
            context_content_commitment_hash=(
                canonical_request.authorized_market_context.context_content_commitment_hash
            ),
            publication_receipt_bundle_hash=identities[
                "publication_receipt_bundle_hash"
            ],
            projection_hash=projection_hash,
            metric_values=canonical_request.metric_values,
        )
        return VersionedIntelligenceFeatureProjection(
            projection_version=canonical_request.projection_version,
            projection_hash=projection_hash,
            observations=(observation,),
        )
