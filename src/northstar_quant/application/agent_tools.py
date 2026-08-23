"""Fail-closed typed boundary for AI-assisted research tools.

This module is an application composition boundary, not an agent runtime and not a
transport implementation.  It intentionally has no configuration, database,
filesystem, network, broker, portfolio, or execution dependency.  A caller must
inject read-only catalog ports and a research-only workflow port explicitly.

The nine tool names in :class:`ToolName` are the complete capability allowlist.
Every response is research-only and is deliberately incapable of granting a
trading, portfolio, broker, or approval capability.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import math
import re
from typing import Literal, Protocol, TypeAlias, TypeVar


__all__ = [
    "AnalogueSummary",
    "BacktestRunSummary",
    "CompareExperimentsRequest",
    "ComparisonSummary",
    "CreateExperimentRequest",
    "DataQualityCatalog",
    "DataQualityFindingKind",
    "DataQualityFindingStatus",
    "DataQualityFindingSummary",
    "DatasetQualityReportSummary",
    "DatasetCatalog",
    "DatasetSummary",
    "EvidenceCitationSummary",
    "EventCatalog",
    "EventSummary",
    "ExperimentSummary",
    "FeatureCatalog",
    "FeatureReference",
    "FeatureSelectionMode",
    "FeatureSummary",
    "GenerateResearchCardRequest",
    "GetFeatureRequest",
    "ImpactSummary",
    "InspectDatasetQualityRequest",
    "ResearchCardSummary",
    "ResearchToolDependencies",
    "ResearchWorkflowPort",
    "RunBacktestRequest",
    "RunValidationRequest",
    "SearchDatasetsRequest",
    "SearchDatasetsResponse",
    "SearchEventsRequest",
    "SearchEventsResponse",
    "TOOL_ALLOWLIST",
    "ToolApiError",
    "ToolName",
    "ToolRequest",
    "ToolResponse",
    "TypedResearchToolApi",
    "ValidationSummary",
]


class ToolApiError(ValueError):
    """Raised when a typed tool request or trusted port result is unsafe."""


class ToolName(StrEnum):
    """The closed, research-only capability allowlist for an AI caller."""

    SEARCH_DATASETS = "search_datasets"
    INSPECT_DATASET_QUALITY = "inspect_dataset_quality"
    SEARCH_EVENTS = "search_events"
    GET_FEATURE = "get_feature"
    CREATE_EXPERIMENT = "create_experiment"
    RUN_BACKTEST = "run_backtest"
    RUN_VALIDATION = "run_validation"
    COMPARE_EXPERIMENTS = "compare_experiments"
    GENERATE_RESEARCH_CARD = "generate_research_card"


TOOL_ALLOWLIST: frozenset[ToolName] = frozenset(ToolName)

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?ix)\b(?:api[_ -]?key|password|secret|access[_ -]?token|credential)\b"
    r"\s*(?:=|:)\s*\S+|\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16})\b"
)
_ABSOLUTE_PATH_PATTERN = re.compile(r"^(?:[A-Za-z]:[\\/]|/|~[\\/])")


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ToolApiError(f"{field_name} must be a string identifier")
    normalized = value.strip()
    if normalized != value or not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ToolApiError(f"{field_name} must be a normalized opaque identifier")
    if normalized.casefold() == "latest":
        raise ToolApiError(f"{field_name} cannot use the ambiguous 'latest' selector")
    return normalized


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise ToolApiError(f"{field_name} must be a lowercase SHA-256 hash")
    return value


def _as_of(value: object, field_name: str = "as_of") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ToolApiError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _query(value: object, field_name: str = "query") -> str:
    if not isinstance(value, str):
        raise ToolApiError(f"{field_name} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > 256 or _CONTROL_CHARACTER_PATTERN.search(normalized):
        raise ToolApiError(f"{field_name} must be non-empty, bounded, printable text")
    if _SECRET_ASSIGNMENT_PATTERN.search(normalized):
        raise ToolApiError(f"{field_name} must not contain a credential or secret")
    if _ABSOLUTE_PATH_PATTERN.match(normalized):
        raise ToolApiError(f"{field_name} must not be a filesystem path")
    return normalized


def _limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ToolApiError("limit must be an integer between 1 and 100")
    return value


def _hashes(value: object, field_name: str, *, minimum: int = 1) -> tuple[str, ...]:
    if isinstance(value, str):
        raise ToolApiError(f"{field_name} must be a tuple of hashes")
    try:
        hashes: tuple[object, ...] = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ToolApiError(f"{field_name} must be an iterable of hashes") from exc
    if len(hashes) < minimum:
        raise ToolApiError(f"{field_name} must contain at least {minimum} hash(es)")
    normalized = tuple(_sha256(item, field_name) for item in hashes)
    if len(set(normalized)) != len(normalized):
        raise ToolApiError(f"{field_name} cannot contain duplicate hashes")
    return normalized


def _identifiers(value: object, field_name: str, *, minimum: int = 1) -> tuple[str, ...]:
    if isinstance(value, str):
        raise ToolApiError(f"{field_name} must be a tuple of identifiers")
    try:
        identifiers: tuple[object, ...] = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ToolApiError(f"{field_name} must be an iterable of identifiers") from exc
    if len(identifiers) < minimum:
        raise ToolApiError(f"{field_name} must contain at least {minimum} identifier(s)")
    normalized = tuple(_identifier(item, field_name) for item in identifiers)
    if len(set(normalized)) != len(normalized):
        raise ToolApiError(f"{field_name} cannot contain duplicate identifiers")
    return normalized


@dataclass(frozen=True, slots=True)
class DatasetSummary:
    """A safe immutable-dataset summary; it deliberately carries no payload or path."""

    dataset_id: str
    dataset_version_hash: str
    available_at: datetime
    schema_hash: str
    lineage_hash: str
    authorization_status: Literal["AUTHORIZED"]

    def __post_init__(self) -> None:
        object.__setattr__(self, "dataset_id", _identifier(self.dataset_id, "dataset_id"))
        object.__setattr__(
            self,
            "dataset_version_hash",
            _sha256(self.dataset_version_hash, "dataset_version_hash"),
        )
        object.__setattr__(self, "available_at", _as_of(self.available_at, "available_at"))
        object.__setattr__(self, "schema_hash", _sha256(self.schema_hash, "schema_hash"))
        object.__setattr__(self, "lineage_hash", _sha256(self.lineage_hash, "lineage_hash"))
        if self.authorization_status != "AUTHORIZED":
            raise ToolApiError("dataset authorization_status must be AUTHORIZED")


class DataQualityFindingKind(StrEnum):
    """The complete, read-only quality-audit vocabulary exposed to an AI caller."""

    GAP = "gap"
    REVISION = "revision"
    ANOMALY = "anomaly"
    STALE_SOURCE = "stale_source"
    CONTRACT_MISMATCH = "contract_mismatch"
    BROKEN_LINEAGE = "broken_lineage"


class DataQualityFindingStatus(StrEnum):
    """A finding's audit state; it is never a repair or a data-use authorization."""

    DETECTED = "DETECTED"
    NOT_DETECTED = "NOT_DETECTED"
    UNKNOWN = "UNKNOWN"


_REASON_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


def _reason_code(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _REASON_CODE_PATTERN.fullmatch(value) is None:
        raise ToolApiError(f"{field_name} must be an uppercase stable reason code")
    return value


@dataclass(frozen=True, slots=True)
class DataQualityFindingSummary:
    """A hash-only immutable quality finding with no raw data or remediation instruction."""

    kind: DataQualityFindingKind
    status: DataQualityFindingStatus
    reason_code: str
    finding_hash: str
    evidence_hashes: tuple[str, ...]
    available_at: datetime
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if type(self.kind) is not DataQualityFindingKind:
            raise ToolApiError("quality finding kind must be a DataQualityFindingKind")
        if type(self.status) is not DataQualityFindingStatus:
            raise ToolApiError("quality finding status must be a DataQualityFindingStatus")
        object.__setattr__(self, "reason_code", _reason_code(self.reason_code, "reason_code"))
        object.__setattr__(self, "finding_hash", _sha256(self.finding_hash, "finding_hash"))
        object.__setattr__(
            self,
            "evidence_hashes",
            _hashes(self.evidence_hashes, "evidence_hashes"),
        )
        object.__setattr__(self, "available_at", _as_of(self.available_at, "available_at"))


@dataclass(frozen=True, slots=True)
class DatasetQualityReportSummary:
    """A safe, immutable quality-audit projection for one exact authorized dataset version."""

    dataset_id: str
    dataset_version_hash: str
    schema_hash: str
    lineage_hash: str
    assessment_hash: str
    lineage_verification_hash: str
    findings: tuple[DataQualityFindingSummary, ...]
    available_at: datetime
    authorization_status: Literal["AUTHORIZED"]
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "dataset_id", _identifier(self.dataset_id, "dataset_id"))
        object.__setattr__(
            self,
            "dataset_version_hash",
            _sha256(self.dataset_version_hash, "dataset_version_hash"),
        )
        object.__setattr__(self, "schema_hash", _sha256(self.schema_hash, "schema_hash"))
        object.__setattr__(self, "lineage_hash", _sha256(self.lineage_hash, "lineage_hash"))
        object.__setattr__(self, "assessment_hash", _sha256(self.assessment_hash, "assessment_hash"))
        object.__setattr__(
            self,
            "lineage_verification_hash",
            _sha256(self.lineage_verification_hash, "lineage_verification_hash"),
        )
        if self.authorization_status != "AUTHORIZED":
            raise ToolApiError("data quality report authorization_status must be AUTHORIZED")
        findings = tuple(self.findings)
        if not all(type(item) is DataQualityFindingSummary for item in findings):
            raise ToolApiError("quality findings must contain DataQualityFindingSummary records")
        expected_kinds = frozenset(DataQualityFindingKind)
        if {item.kind for item in findings} != expected_kinds or len(findings) != len(expected_kinds):
            raise ToolApiError("quality findings must exactly cover each DataQualityFindingKind once")
        available_at = _as_of(self.available_at, "available_at")
        if any(item.available_at > available_at for item in findings):
            raise ToolApiError("quality finding is not available at the report time")
        object.__setattr__(
            self,
            "findings",
            tuple(sorted(findings, key=lambda item: item.kind.value)),
        )
        object.__setattr__(self, "available_at", available_at)


@dataclass(frozen=True, slots=True)
class EvidenceCitationSummary:
    """A privacy-preserving, point-in-time evidence citation.

    The citation deliberately identifies an authorized source, immutable document
    content, and a precomputed evidence hash, but never carries document text,
    URLs, provider payloads, filesystem locations, or credentials.
    """

    evidence_hash: str
    document_id: str
    source_id: str
    document_content_hash: str
    span_start: int
    span_end: int
    published_at: datetime
    available_at: datetime
    authorization_status: Literal["AUTHORIZED"]

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_hash", _sha256(self.evidence_hash, "evidence_hash"))
        object.__setattr__(self, "document_id", _identifier(self.document_id, "document_id"))
        object.__setattr__(self, "source_id", _identifier(self.source_id, "source_id"))
        object.__setattr__(
            self,
            "document_content_hash",
            _sha256(self.document_content_hash, "document_content_hash"),
        )
        if (
            type(self.span_start) is not int
            or type(self.span_end) is not int
            or self.span_start < 0
            or self.span_end <= self.span_start
        ):
            raise ToolApiError("evidence citation span must be a non-empty ordered integer range")
        published_at = _as_of(self.published_at, "published_at")
        available_at = _as_of(self.available_at, "available_at")
        if available_at < published_at:
            raise ToolApiError("evidence citation cannot be available before publication")
        object.__setattr__(self, "published_at", published_at)
        object.__setattr__(self, "available_at", available_at)
        if self.authorization_status != "AUTHORIZED":
            raise ToolApiError("evidence citation authorization_status must be AUTHORIZED")


_FORBIDDEN_IMPACT_DIRECTIONS = frozenset(
    {
        "approve",
        "broker",
        "buy",
        "execution",
        "long",
        "order",
        "position",
        "sell",
        "short",
        "signal",
        "target",
        "trade",
    }
)


@dataclass(frozen=True, slots=True)
class ImpactSummary:
    """A cited Event-to-mechanism-to-commodity impact, never an execution instruction."""

    impact_id: str
    event_id: str
    ontology_version: str
    mechanism_id: str
    commodity_id: str
    direction: str
    evidence_hashes: tuple[str, ...]
    available_at: datetime
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "impact_id", _identifier(self.impact_id, "impact_id"))
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        object.__setattr__(
            self,
            "ontology_version",
            _identifier(self.ontology_version, "ontology_version"),
        )
        object.__setattr__(self, "mechanism_id", _identifier(self.mechanism_id, "mechanism_id"))
        object.__setattr__(self, "commodity_id", _identifier(self.commodity_id, "commodity_id"))
        direction = _identifier(self.direction, "direction")
        if direction.casefold() in _FORBIDDEN_IMPACT_DIRECTIONS:
            raise ToolApiError("impact direction cannot encode a trading action or instruction")
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "evidence_hashes", _hashes(self.evidence_hashes, "evidence_hashes"))
        object.__setattr__(self, "available_at", _as_of(self.available_at, "available_at"))


@dataclass(frozen=True, slots=True)
class AnalogueSummary:
    """A cited historical analogue result with bounded structured similarity."""

    reference_event_id: str
    analogue_event_id: str
    analogue_event_hash: str
    analogue_event_time: datetime
    matching_method_hash: str
    structured_similarity: float
    embedding_similarity: float | None
    evidence_citations: tuple[EvidenceCitationSummary, ...]
    available_at: datetime
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reference_event_id",
            _identifier(self.reference_event_id, "reference_event_id"),
        )
        object.__setattr__(
            self,
            "analogue_event_id",
            _identifier(self.analogue_event_id, "analogue_event_id"),
        )
        if self.reference_event_id == self.analogue_event_id:
            raise ToolApiError("an analogue cannot be its own reference event")
        object.__setattr__(
            self,
            "analogue_event_hash",
            _sha256(self.analogue_event_hash, "analogue_event_hash"),
        )
        analogue_event_time = _as_of(self.analogue_event_time, "analogue_event_time")
        object.__setattr__(
            self,
            "matching_method_hash",
            _sha256(self.matching_method_hash, "matching_method_hash"),
        )
        if (
            type(self.structured_similarity) is not float
            or not math.isfinite(self.structured_similarity)
            or not 0 <= self.structured_similarity <= 1
        ):
            raise ToolApiError("structured_similarity must be a bounded finite float")
        if self.embedding_similarity is not None and (
            type(self.embedding_similarity) is not float
            or not math.isfinite(self.embedding_similarity)
            or not 0 <= self.embedding_similarity <= 1
        ):
            raise ToolApiError("embedding_similarity must be a bounded finite float")
        citations = tuple(self.evidence_citations)
        if not citations or not all(type(item) is EvidenceCitationSummary for item in citations):
            raise ToolApiError("analogue must contain authorized EvidenceCitationSummary records")
        if len({item.evidence_hash for item in citations}) != len(citations):
            raise ToolApiError("analogue evidence citations cannot duplicate evidence hashes")
        available_at = _as_of(self.available_at, "available_at")
        if available_at < analogue_event_time:
            raise ToolApiError("analogue cannot be available before its event time")
        if any(citation.available_at > available_at for citation in citations):
            raise ToolApiError("analogue evidence is not available at the analogue result time")
        object.__setattr__(self, "evidence_citations", citations)
        object.__setattr__(self, "analogue_event_time", analogue_event_time)
        object.__setattr__(self, "available_at", available_at)


@dataclass(frozen=True, slots=True)
class EventSummary:
    """A fully cited event projection without raw Document or provider payloads.

    The projection is deliberately rich enough for the Intelligence Agent to
    research cited sources, select a historical analogue, and explain an
    Event-to-mechanism-to-commodity impact without a second domain or
    infrastructure capability.
    """

    event_id: str
    event_hash: str
    event_type: str
    ontology_version: str
    event_time: datetime
    available_at: datetime
    evidence_citations: tuple[EvidenceCitationSummary, ...]
    lifecycle: str
    impact_summaries: tuple[ImpactSummary, ...] = ()
    analogue_summaries: tuple[AnalogueSummary, ...] = ()
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        object.__setattr__(self, "event_hash", _sha256(self.event_hash, "event_hash"))
        event_type = _identifier(self.event_type, "event_type")
        if event_type.casefold() in _FORBIDDEN_IMPACT_DIRECTIONS:
            raise ToolApiError("event_type cannot encode a trading action or instruction")
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "ontology_version", _identifier(self.ontology_version, "ontology_version"))
        event_time = _as_of(self.event_time, "event_time")
        available_at = _as_of(self.available_at, "available_at")
        if available_at < event_time:
            raise ToolApiError("event summary cannot be available before event_time")
        object.__setattr__(self, "event_time", event_time)
        object.__setattr__(self, "available_at", available_at)
        citations = tuple(self.evidence_citations)
        if not citations or not all(type(item) is EvidenceCitationSummary for item in citations):
            raise ToolApiError("event must contain authorized EvidenceCitationSummary records")
        if len({item.evidence_hash for item in citations}) != len(citations):
            raise ToolApiError("event evidence citations cannot duplicate evidence hashes")
        if any(citation.available_at > available_at for citation in citations):
            raise ToolApiError("event evidence is not available at the event summary time")
        object.__setattr__(self, "evidence_citations", citations)
        object.__setattr__(self, "lifecycle", _identifier(self.lifecycle, "lifecycle"))
        impact_summaries = tuple(self.impact_summaries)
        if not all(type(item) is ImpactSummary for item in impact_summaries):
            raise ToolApiError("impact_summaries must contain ImpactSummary records")
        evidence_hashes = {citation.evidence_hash for citation in citations}
        if any(
            item.event_id != self.event_id
            or item.ontology_version != self.ontology_version
            or item.available_at > available_at
            or not set(item.evidence_hashes).issubset(evidence_hashes)
            for item in impact_summaries
        ):
            raise ToolApiError("impact summaries must be parent-event, ontology, PIT, and evidence bound")
        if len({item.impact_id for item in impact_summaries}) != len(impact_summaries):
            raise ToolApiError("impact_summaries cannot duplicate impact identifiers")
        object.__setattr__(self, "impact_summaries", impact_summaries)
        analogue_summaries = tuple(self.analogue_summaries)
        if not all(type(item) is AnalogueSummary for item in analogue_summaries):
            raise ToolApiError("analogue_summaries must contain AnalogueSummary records")
        if any(
            item.reference_event_id != self.event_id
            or item.analogue_event_time >= event_time
            or item.available_at > available_at
            for item in analogue_summaries
        ):
            raise ToolApiError("analogue summaries must be historical and bind the parent event")
        if len({item.analogue_event_id for item in analogue_summaries}) != len(analogue_summaries):
            raise ToolApiError("analogue_summaries cannot duplicate analogue event identifiers")
        object.__setattr__(self, "analogue_summaries", analogue_summaries)


@dataclass(frozen=True, slots=True)
class FeatureReference:
    """Exact Feature identity.  Version selection is never implicit."""

    feature_id: str
    feature_version_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "feature_id", _identifier(self.feature_id, "feature_id"))
        object.__setattr__(
            self,
            "feature_version_hash",
            _sha256(self.feature_version_hash, "feature_version_hash"),
        )


class FeatureSelectionMode(StrEnum):
    """The only Feature lineage selection semantics exposed to an AI caller."""

    STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY = "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY"
    PER_DECISION_POINT_IN_TIME_REPLAY = "PER_DECISION_POINT_IN_TIME_REPLAY"


@dataclass(frozen=True, slots=True)
class FeatureSummary:
    """A hash-only feature summary suitable for reproducibility linkage."""

    reference: FeatureReference
    available_at: datetime
    lineage_hash: str
    dataset_version_hashes: tuple[str, ...]
    selection_mode: FeatureSelectionMode
    decision_time_safe: bool
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if type(self.reference) is not FeatureReference:
            raise ToolApiError("reference must be a FeatureReference")
        object.__setattr__(self, "available_at", _as_of(self.available_at, "available_at"))
        object.__setattr__(self, "lineage_hash", _sha256(self.lineage_hash, "lineage_hash"))
        object.__setattr__(
            self,
            "dataset_version_hashes",
            _hashes(self.dataset_version_hashes, "dataset_version_hashes"),
        )
        if type(self.selection_mode) is not FeatureSelectionMode:
            raise ToolApiError("selection_mode must be a FeatureSelectionMode")
        if type(self.decision_time_safe) is not bool:
            raise ToolApiError("decision_time_safe must be a bool")
        if (
            self.selection_mode is FeatureSelectionMode.STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY
            and self.decision_time_safe is not False
        ):
            raise ToolApiError("static feature selection cannot claim decision-time safety")
        if (
            self.selection_mode is FeatureSelectionMode.PER_DECISION_POINT_IN_TIME_REPLAY
            and self.decision_time_safe is not True
        ):
            raise ToolApiError("per-decision feature selection must prove decision-time safety")


@dataclass(frozen=True, slots=True)
class SearchDatasetsRequest:
    query: str
    as_of: datetime
    limit: int = 20

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", _query(self.query))
        object.__setattr__(self, "as_of", _as_of(self.as_of))
        object.__setattr__(self, "limit", _limit(self.limit))


@dataclass(frozen=True, slots=True)
class SearchDatasetsResponse:
    as_of: datetime
    datasets: tuple[DatasetSummary, ...]
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", _as_of(self.as_of))
        datasets = tuple(self.datasets)
        if not all(type(item) is DatasetSummary for item in datasets):
            raise ToolApiError("datasets must contain DatasetSummary records")
        if any(item.available_at > self.as_of for item in datasets):
            raise ToolApiError("dataset result is not available at the requested as_of")
        object.__setattr__(self, "datasets", datasets)


@dataclass(frozen=True, slots=True)
class InspectDatasetQualityRequest:
    """Inspect one exact authorized dataset version; raw data and repair inputs are forbidden."""

    dataset: DatasetSummary
    as_of: datetime

    def __post_init__(self) -> None:
        if type(self.dataset) is not DatasetSummary:
            raise ToolApiError("dataset must be a DatasetSummary")
        if self.dataset.authorization_status != "AUTHORIZED":
            raise ToolApiError("dataset must be authorized for quality inspection")
        as_of = _as_of(self.as_of)
        if self.dataset.available_at > as_of:
            raise ToolApiError("dataset is not available at the requested as_of")
        object.__setattr__(self, "as_of", as_of)


@dataclass(frozen=True, slots=True)
class SearchEventsRequest:
    query: str
    as_of: datetime
    limit: int = 20

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", _query(self.query))
        object.__setattr__(self, "as_of", _as_of(self.as_of))
        object.__setattr__(self, "limit", _limit(self.limit))


@dataclass(frozen=True, slots=True)
class SearchEventsResponse:
    as_of: datetime
    events: tuple[EventSummary, ...]
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", _as_of(self.as_of))
        events = tuple(self.events)
        if not all(type(item) is EventSummary for item in events):
            raise ToolApiError("events must contain EventSummary records")
        if any(item.available_at > self.as_of for item in events):
            raise ToolApiError("event result is not available at the requested as_of")
        object.__setattr__(self, "events", events)


@dataclass(frozen=True, slots=True)
class GetFeatureRequest:
    feature: FeatureReference
    as_of: datetime

    def __post_init__(self) -> None:
        if type(self.feature) is not FeatureReference:
            raise ToolApiError("feature must be a FeatureReference")
        object.__setattr__(self, "as_of", _as_of(self.as_of))


@dataclass(frozen=True, slots=True)
class CreateExperimentRequest:
    """An immutable research specification request, never a run authorization."""

    experiment_id: str
    dataset_version_hash: str
    feature_references: tuple[FeatureReference, ...]
    strategy_version_hash: str
    strategy_code_hash: str
    configuration_hash: str
    cost_model_hash: str
    slippage_model_hash: str
    as_of: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "experiment_id", _identifier(self.experiment_id, "experiment_id"))
        object.__setattr__(
            self,
            "dataset_version_hash",
            _sha256(self.dataset_version_hash, "dataset_version_hash"),
        )
        references = tuple(self.feature_references)
        if not references or not all(type(item) is FeatureReference for item in references):
            raise ToolApiError("feature_references must contain FeatureReference records")
        if len({item.feature_version_hash for item in references}) != len(references):
            raise ToolApiError("feature_references cannot contain duplicate feature versions")
        object.__setattr__(self, "feature_references", references)
        object.__setattr__(
            self,
            "strategy_version_hash",
            _sha256(self.strategy_version_hash, "strategy_version_hash"),
        )
        object.__setattr__(self, "strategy_code_hash", _sha256(self.strategy_code_hash, "strategy_code_hash"))
        object.__setattr__(
            self,
            "configuration_hash",
            _sha256(self.configuration_hash, "configuration_hash"),
        )
        object.__setattr__(self, "cost_model_hash", _sha256(self.cost_model_hash, "cost_model_hash"))
        object.__setattr__(
            self,
            "slippage_model_hash",
            _sha256(self.slippage_model_hash, "slippage_model_hash"),
        )
        object.__setattr__(self, "as_of", _as_of(self.as_of))


@dataclass(frozen=True, slots=True)
class ExperimentSummary:
    experiment_id: str
    experiment_spec_hash: str
    dataset_version_hash: str
    feature_version_hashes: tuple[str, ...]
    available_at: datetime
    lifecycle: Literal["STATIC_REPRODUCIBILITY_ONLY"]
    eligible_for_backtest: Literal[False] = field(default=False, init=False)
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "experiment_id", _identifier(self.experiment_id, "experiment_id"))
        object.__setattr__(
            self,
            "experiment_spec_hash",
            _sha256(self.experiment_spec_hash, "experiment_spec_hash"),
        )
        object.__setattr__(
            self,
            "dataset_version_hash",
            _sha256(self.dataset_version_hash, "dataset_version_hash"),
        )
        object.__setattr__(
            self,
            "feature_version_hashes",
            _hashes(self.feature_version_hashes, "feature_version_hashes"),
        )
        object.__setattr__(self, "available_at", _as_of(self.available_at, "available_at"))
        if self.lifecycle != "STATIC_REPRODUCIBILITY_ONLY":
            raise ToolApiError("experiment lifecycle must remain STATIC_REPRODUCIBILITY_ONLY")


@dataclass(frozen=True, slots=True)
class RunBacktestRequest:
    """References a pre-registered research backtest request; it accepts no executable input."""

    experiment_id: str
    backtest_request_id: str
    as_of: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "experiment_id", _identifier(self.experiment_id, "experiment_id"))
        object.__setattr__(
            self,
            "backtest_request_id",
            _identifier(self.backtest_request_id, "backtest_request_id"),
        )
        object.__setattr__(self, "as_of", _as_of(self.as_of))


@dataclass(frozen=True, slots=True)
class BacktestRunSummary:
    experiment_id: str
    backtest_request_id: str
    backtest_run_id: str
    run_manifest_hash: str
    evidence_hash: str
    available_at: datetime
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "experiment_id", _identifier(self.experiment_id, "experiment_id"))
        object.__setattr__(
            self,
            "backtest_request_id",
            _identifier(self.backtest_request_id, "backtest_request_id"),
        )
        object.__setattr__(self, "backtest_run_id", _identifier(self.backtest_run_id, "backtest_run_id"))
        object.__setattr__(self, "run_manifest_hash", _sha256(self.run_manifest_hash, "run_manifest_hash"))
        object.__setattr__(self, "evidence_hash", _sha256(self.evidence_hash, "evidence_hash"))
        object.__setattr__(self, "available_at", _as_of(self.available_at, "available_at"))


@dataclass(frozen=True, slots=True)
class RunValidationRequest:
    experiment_id: str
    backtest_run_id: str
    validation_request_id: str
    as_of: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "experiment_id", _identifier(self.experiment_id, "experiment_id"))
        object.__setattr__(self, "backtest_run_id", _identifier(self.backtest_run_id, "backtest_run_id"))
        object.__setattr__(
            self,
            "validation_request_id",
            _identifier(self.validation_request_id, "validation_request_id"),
        )
        object.__setattr__(self, "as_of", _as_of(self.as_of))


@dataclass(frozen=True, slots=True)
class ValidationSummary:
    experiment_id: str
    backtest_run_id: str
    validation_request_id: str
    validation_report_id: str
    validation_report_hash: str
    evidence_hash: str
    available_at: datetime
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "experiment_id", _identifier(self.experiment_id, "experiment_id"))
        object.__setattr__(self, "backtest_run_id", _identifier(self.backtest_run_id, "backtest_run_id"))
        object.__setattr__(
            self,
            "validation_request_id",
            _identifier(self.validation_request_id, "validation_request_id"),
        )
        object.__setattr__(
            self,
            "validation_report_id",
            _identifier(self.validation_report_id, "validation_report_id"),
        )
        object.__setattr__(
            self,
            "validation_report_hash",
            _sha256(self.validation_report_hash, "validation_report_hash"),
        )
        object.__setattr__(self, "evidence_hash", _sha256(self.evidence_hash, "evidence_hash"))
        object.__setattr__(self, "available_at", _as_of(self.available_at, "available_at"))


@dataclass(frozen=True, slots=True)
class CompareExperimentsRequest:
    experiment_ids: tuple[str, ...]
    comparison_request_id: str
    as_of: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "experiment_ids",
            _identifiers(self.experiment_ids, "experiment_ids", minimum=2),
        )
        object.__setattr__(
            self,
            "comparison_request_id",
            _identifier(self.comparison_request_id, "comparison_request_id"),
        )
        object.__setattr__(self, "as_of", _as_of(self.as_of))


@dataclass(frozen=True, slots=True)
class ComparisonSummary:
    comparison_request_id: str
    comparison_id: str
    experiment_ids: tuple[str, ...]
    comparability_hash: str
    available_at: datetime
    comparable: Literal[True]
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "comparison_request_id",
            _identifier(self.comparison_request_id, "comparison_request_id"),
        )
        object.__setattr__(self, "comparison_id", _identifier(self.comparison_id, "comparison_id"))
        object.__setattr__(
            self,
            "experiment_ids",
            _identifiers(self.experiment_ids, "experiment_ids", minimum=2),
        )
        object.__setattr__(
            self,
            "comparability_hash",
            _sha256(self.comparability_hash, "comparability_hash"),
        )
        object.__setattr__(self, "available_at", _as_of(self.available_at, "available_at"))
        if self.comparable is not True:
            raise ToolApiError("comparison result must be comparable; otherwise fail closed")


@dataclass(frozen=True, slots=True)
class GenerateResearchCardRequest:
    experiment_id: str
    backtest_run_id: str
    validation_report_id: str
    research_decision_id: str
    research_card_request_id: str
    as_of: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "experiment_id", _identifier(self.experiment_id, "experiment_id"))
        object.__setattr__(self, "backtest_run_id", _identifier(self.backtest_run_id, "backtest_run_id"))
        object.__setattr__(
            self,
            "validation_report_id",
            _identifier(self.validation_report_id, "validation_report_id"),
        )
        object.__setattr__(
            self,
            "research_decision_id",
            _identifier(self.research_decision_id, "research_decision_id"),
        )
        object.__setattr__(
            self,
            "research_card_request_id",
            _identifier(self.research_card_request_id, "research_card_request_id"),
        )
        object.__setattr__(self, "as_of", _as_of(self.as_of))


@dataclass(frozen=True, slots=True)
class ResearchCardSummary:
    research_card_request_id: str
    research_card_id: str
    research_card_hash: str
    research_decision_id: str
    decision_stage: Literal["RESEARCH_ONLY"]
    available_at: datetime
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "research_card_request_id",
            _identifier(self.research_card_request_id, "research_card_request_id"),
        )
        object.__setattr__(
            self,
            "research_card_id",
            _identifier(self.research_card_id, "research_card_id"),
        )
        object.__setattr__(self, "research_card_hash", _sha256(self.research_card_hash, "research_card_hash"))
        object.__setattr__(
            self,
            "research_decision_id",
            _identifier(self.research_decision_id, "research_decision_id"),
        )
        if self.decision_stage != "RESEARCH_ONLY":
            raise ToolApiError("research cards can only be generated for RESEARCH_ONLY decisions")
        object.__setattr__(self, "available_at", _as_of(self.available_at, "available_at"))


class DatasetCatalog(Protocol):
    """Read-only, publication-controlled dataset search seam."""

    def search_datasets(
        self,
        *,
        query: str,
        as_of: datetime,
        limit: int,
    ) -> tuple[DatasetSummary, ...]: ...


class DataQualityCatalog(Protocol):
    """Read-only immutable quality-audit projection seam; it cannot repair or republish data."""

    def inspect_dataset_quality(
        self,
        *,
        request: InspectDatasetQualityRequest,
    ) -> DatasetQualityReportSummary: ...


class EventCatalog(Protocol):
    """Read-only event catalog that has already bound evidence and availability."""

    def search_events(
        self,
        *,
        query: str,
        as_of: datetime,
        limit: int,
    ) -> tuple[EventSummary, ...]: ...


class FeatureCatalog(Protocol):
    """Exact version feature lookup seam; no implicit registry mutation is allowed."""

    def get_feature(self, *, feature: FeatureReference, as_of: datetime) -> FeatureSummary: ...


class ResearchWorkflowPort(Protocol):
    """Trusted evidence-linkage workflow boundary for research-only side effects."""

    def create_experiment(
        self,
        *,
        request: CreateExperimentRequest,
        features: tuple[FeatureSummary, ...],
    ) -> ExperimentSummary: ...

    def run_backtest(self, *, request: RunBacktestRequest) -> BacktestRunSummary: ...

    def run_validation(self, *, request: RunValidationRequest) -> ValidationSummary: ...

    def compare_experiments(
        self,
        *,
        request: CompareExperimentsRequest,
    ) -> ComparisonSummary: ...

    def generate_research_card(
        self,
        *,
        request: GenerateResearchCardRequest,
    ) -> ResearchCardSummary: ...


@dataclass(frozen=True, slots=True)
class ResearchToolDependencies:
    """All capabilities are explicit injection points; there is no global fallback."""

    dataset_catalog: DatasetCatalog
    data_quality_catalog: DataQualityCatalog
    event_catalog: EventCatalog
    feature_catalog: FeatureCatalog
    research_workflow: ResearchWorkflowPort

    def __post_init__(self) -> None:
        try:
            dataset_search = self.dataset_catalog.search_datasets
            data_quality_inspection = self.data_quality_catalog.inspect_dataset_quality
            event_search = self.event_catalog.search_events
            feature_lookup = self.feature_catalog.get_feature
            create_experiment = self.research_workflow.create_experiment
            run_backtest = self.research_workflow.run_backtest
            run_validation = self.research_workflow.run_validation
            compare_experiments = self.research_workflow.compare_experiments
            generate_research_card = self.research_workflow.generate_research_card
        except AttributeError as exc:
            raise ToolApiError("research tool dependencies are missing a required port method") from exc
        if not all(
            callable(method)
            for method in (
                dataset_search,
                data_quality_inspection,
                event_search,
                feature_lookup,
                create_experiment,
                run_backtest,
                run_validation,
                compare_experiments,
                generate_research_card,
            )
        ):
            raise ToolApiError("research tool dependencies must expose callable port methods")


ToolRequest: TypeAlias = (
    SearchDatasetsRequest
    | InspectDatasetQualityRequest
    | SearchEventsRequest
    | GetFeatureRequest
    | CreateExperimentRequest
    | RunBacktestRequest
    | RunValidationRequest
    | CompareExperimentsRequest
    | GenerateResearchCardRequest
)
ToolResponse: TypeAlias = (
    SearchDatasetsResponse
    | DatasetQualityReportSummary
    | SearchEventsResponse
    | FeatureSummary
    | ExperimentSummary
    | BacktestRunSummary
    | ValidationSummary
    | ComparisonSummary
    | ResearchCardSummary
)
SummaryT = TypeVar("SummaryT")


class TypedResearchToolApi:
    """The complete research-only agent tool surface.

    The constructor intentionally requires explicit ports.  Implementations of
    those ports own durable repositories and audit handling in later work
    packages; this facade only enforces that all data crossing the agent boundary
    remains typed, point-in-time safe, and unable to grant trading authority.
    """

    __slots__ = ("_dependencies",)

    def __init__(self, dependencies: ResearchToolDependencies) -> None:
        if type(dependencies) is not ResearchToolDependencies:
            raise ToolApiError("dependencies must be ResearchToolDependencies")
        self._dependencies = dependencies

    def search_datasets(self, request: SearchDatasetsRequest) -> SearchDatasetsResponse:
        """Search only published, authorized immutable dataset summaries."""

        self._require_request(request, SearchDatasetsRequest)
        datasets = self._tuple_from_port(
            self._dependencies.dataset_catalog.search_datasets(
                query=request.query,
                as_of=request.as_of,
                limit=request.limit,
            ),
            "dataset catalog result",
        )
        if len(datasets) > request.limit:
            raise ToolApiError("dataset catalog exceeded the requested result limit")
        return SearchDatasetsResponse(as_of=request.as_of, datasets=datasets)

    def inspect_dataset_quality(
        self,
        request: InspectDatasetQualityRequest,
    ) -> DatasetQualityReportSummary:
        """Read one immutable, evidence-bound quality audit; this cannot repair or alter data."""

        self._require_request(request, InspectDatasetQualityRequest)
        result = self._dependencies.data_quality_catalog.inspect_dataset_quality(request=request)
        if type(result) is not DatasetQualityReportSummary:
            raise ToolApiError("data quality catalog must return DatasetQualityReportSummary")
        dataset = request.dataset
        if (
            result.dataset_id != dataset.dataset_id
            or result.dataset_version_hash != dataset.dataset_version_hash
            or result.schema_hash != dataset.schema_hash
            or result.lineage_hash != dataset.lineage_hash
            or result.authorization_status != "AUTHORIZED"
            or result.available_at < dataset.available_at
            or result.available_at > request.as_of
        ):
            raise ToolApiError("data quality report is not exactly bound to the typed request")
        return result

    def search_events(self, request: SearchEventsRequest) -> SearchEventsResponse:
        """Search evidence-referenced events visible at the requested as-of time."""

        self._require_request(request, SearchEventsRequest)
        events = self._tuple_from_port(
            self._dependencies.event_catalog.search_events(
                query=request.query,
                as_of=request.as_of,
                limit=request.limit,
            ),
            "event catalog result",
        )
        if len(events) > request.limit:
            raise ToolApiError("event catalog exceeded the requested result limit")
        return SearchEventsResponse(as_of=request.as_of, events=events)

    def get_feature(self, request: GetFeatureRequest) -> FeatureSummary:
        """Retrieve one exact feature version if it was available at ``as_of``."""

        self._require_request(request, GetFeatureRequest)
        feature = self._dependencies.feature_catalog.get_feature(
            feature=request.feature,
            as_of=request.as_of,
        )
        if type(feature) is not FeatureSummary:
            raise ToolApiError("feature catalog must return FeatureSummary")
        if feature.reference != request.feature:
            raise ToolApiError("feature catalog returned a different feature version")
        if feature.available_at > request.as_of:
            raise ToolApiError("feature is not available at the requested as_of")
        return feature

    def create_experiment(self, request: CreateExperimentRequest) -> ExperimentSummary:
        """Create a static reproducibility record using exact, visible features only."""

        self._require_request(request, CreateExperimentRequest)
        features = tuple(
            self.get_feature(GetFeatureRequest(feature=reference, as_of=request.as_of))
            for reference in request.feature_references
        )
        if any(request.dataset_version_hash not in item.dataset_version_hashes for item in features):
            raise ToolApiError("each feature must bind the requested immutable dataset version")
        result = self._dependencies.research_workflow.create_experiment(
            request=request,
            features=features,
        )
        if type(result) is not ExperimentSummary:
            raise ToolApiError("research workflow must return ExperimentSummary")
        if (
            result.experiment_id != request.experiment_id
            or result.dataset_version_hash != request.dataset_version_hash
            or result.feature_version_hashes
            != tuple(reference.feature_version_hash for reference in request.feature_references)
            or result.available_at > request.as_of
        ):
            raise ToolApiError("experiment result is not exactly bound to the typed request")
        return result

    def run_backtest(self, request: RunBacktestRequest) -> BacktestRunSummary:
        """Run only a pre-registered research request; no strategy or data input is accepted."""

        self._require_request(request, RunBacktestRequest)
        result = self._dependencies.research_workflow.run_backtest(request=request)
        if type(result) is not BacktestRunSummary:
            raise ToolApiError("research workflow must return BacktestRunSummary")
        if (
            result.experiment_id != request.experiment_id
            or result.backtest_request_id != request.backtest_request_id
            or result.available_at > request.as_of
        ):
            raise ToolApiError("backtest result is not exactly bound to the typed request")
        return result

    def run_validation(self, request: RunValidationRequest) -> ValidationSummary:
        """Validate only a trusted backtest record; naked return series are not accepted."""

        self._require_request(request, RunValidationRequest)
        result = self._dependencies.research_workflow.run_validation(request=request)
        if type(result) is not ValidationSummary:
            raise ToolApiError("research workflow must return ValidationSummary")
        if (
            result.experiment_id != request.experiment_id
            or result.backtest_run_id != request.backtest_run_id
            or result.validation_request_id != request.validation_request_id
            or result.available_at > request.as_of
        ):
            raise ToolApiError("validation result is not exactly bound to the typed request")
        return result

    def compare_experiments(self, request: CompareExperimentsRequest) -> ComparisonSummary:
        """Compare only evidence-linked, comparable research experiments; never rank for trading."""

        self._require_request(request, CompareExperimentsRequest)
        result = self._dependencies.research_workflow.compare_experiments(request=request)
        if type(result) is not ComparisonSummary:
            raise ToolApiError("research workflow must return ComparisonSummary")
        if (
            result.comparison_request_id != request.comparison_request_id
            or result.experiment_ids != request.experiment_ids
            or result.available_at > request.as_of
        ):
            raise ToolApiError("comparison result is not exactly bound to the typed request")
        return result

    def generate_research_card(self, request: GenerateResearchCardRequest) -> ResearchCardSummary:
        """Generate a card for an existing RESEARCH_ONLY decision, never an approval."""

        self._require_request(request, GenerateResearchCardRequest)
        result = self._dependencies.research_workflow.generate_research_card(request=request)
        if type(result) is not ResearchCardSummary:
            raise ToolApiError("research workflow must return ResearchCardSummary")
        if (
            result.research_card_request_id != request.research_card_request_id
            or result.research_decision_id != request.research_decision_id
            or result.available_at > request.as_of
        ):
            raise ToolApiError("research card result is not exactly bound to the typed request")
        return result

    def invoke(self, tool_name: ToolName, request: ToolRequest) -> ToolResponse:
        """Dispatch an already-enumerated tool name with its exact request type."""

        if type(tool_name) is not ToolName:
            raise ToolApiError("tool_name must be a ToolName enum value")
        if tool_name is ToolName.SEARCH_DATASETS:
            if type(request) is not SearchDatasetsRequest:
                raise ToolApiError("request must be exactly SearchDatasetsRequest")
            return self.search_datasets(request)
        if tool_name is ToolName.INSPECT_DATASET_QUALITY:
            if type(request) is not InspectDatasetQualityRequest:
                raise ToolApiError("request must be exactly InspectDatasetQualityRequest")
            return self.inspect_dataset_quality(request)
        if tool_name is ToolName.SEARCH_EVENTS:
            if type(request) is not SearchEventsRequest:
                raise ToolApiError("request must be exactly SearchEventsRequest")
            return self.search_events(request)
        if tool_name is ToolName.GET_FEATURE:
            if type(request) is not GetFeatureRequest:
                raise ToolApiError("request must be exactly GetFeatureRequest")
            return self.get_feature(request)
        if tool_name is ToolName.CREATE_EXPERIMENT:
            if type(request) is not CreateExperimentRequest:
                raise ToolApiError("request must be exactly CreateExperimentRequest")
            return self.create_experiment(request)
        if tool_name is ToolName.RUN_BACKTEST:
            if type(request) is not RunBacktestRequest:
                raise ToolApiError("request must be exactly RunBacktestRequest")
            return self.run_backtest(request)
        if tool_name is ToolName.RUN_VALIDATION:
            if type(request) is not RunValidationRequest:
                raise ToolApiError("request must be exactly RunValidationRequest")
            return self.run_validation(request)
        if tool_name is ToolName.COMPARE_EXPERIMENTS:
            if type(request) is not CompareExperimentsRequest:
                raise ToolApiError("request must be exactly CompareExperimentsRequest")
            return self.compare_experiments(request)
        if tool_name is ToolName.GENERATE_RESEARCH_CARD:
            if type(request) is not GenerateResearchCardRequest:
                raise ToolApiError("request must be exactly GenerateResearchCardRequest")
            return self.generate_research_card(request)
        raise ToolApiError("tool_name is outside the research tool allowlist")

    @staticmethod
    def _require_request(request: object, request_type: type[object]) -> None:
        if type(request) is not request_type:
            raise ToolApiError(f"request must be exactly {request_type.__name__}")

    @staticmethod
    def _tuple_from_port(value: Iterable[SummaryT], field_name: str) -> tuple[SummaryT, ...]:
        if isinstance(value, (str, bytes)):
            raise ToolApiError(f"{field_name} must be an iterable of typed summaries")
        return tuple(value)
