"""Fail-closed, evidence-only orchestration for P7 intelligence research.

``IntelligenceAgent`` deliberately has one capability: the closed
``TypedResearchToolApi``.  It neither fetches sources nor imports intelligence
domain services.  Instead it consumes the safe, point-in-time event projection
returned by ``search_events`` and turns it into cited research findings.  It
cannot create a signal, target, approval, feature, order, or execution plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
import re
from typing import Literal

from northstar_quant.application.agent_tools import (
    AnalogueSummary,
    EventSummary,
    ImpactSummary,
    SearchEventsRequest,
    SearchEventsResponse,
    ToolName,
    TypedResearchToolApi,
)


__all__ = [
    "AnalogueFinding",
    "EventSummaryFinding",
    "ImpactExplanationFinding",
    "IntelligenceAgent",
    "IntelligenceAgentError",
    "IntelligenceAgentRequest",
    "IntelligenceAgentResult",
    "IntelligenceAgentTraceEntry",
    "IntelligenceFocus",
    "SourceResearchFinding",
]


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
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


class IntelligenceAgentError(ValueError):
    """Raised when an intelligence result cannot be proved safe and cited."""


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise IntelligenceAgentError(f"{field_name} must be a string identifier")
    normalized = value.strip()
    if normalized != value or not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise IntelligenceAgentError(f"{field_name} must be a normalized opaque identifier")
    if normalized.casefold() == "latest":
        raise IntelligenceAgentError(f"{field_name} cannot use the ambiguous 'latest' selector")
    return normalized


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise IntelligenceAgentError(f"{field_name} must be a lowercase SHA-256 hash")
    return value


def _hashes(value: object, field_name: str, *, minimum: int = 1) -> tuple[str, ...]:
    if isinstance(value, str):
        raise IntelligenceAgentError(f"{field_name} must be a tuple of hashes")
    try:
        hashes: tuple[object, ...] = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise IntelligenceAgentError(f"{field_name} must be an iterable of hashes") from exc
    if len(hashes) < minimum:
        raise IntelligenceAgentError(f"{field_name} must contain at least {minimum} hash(es)")
    normalized = tuple(_sha256(item, field_name) for item in hashes)
    if len(set(normalized)) != len(normalized):
        raise IntelligenceAgentError(f"{field_name} cannot contain duplicate hashes")
    return normalized


def _as_of(value: object, field_name: str = "as_of") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise IntelligenceAgentError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _fingerprint(payload: object) -> str:
    return sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class IntelligenceFocus:
    """An exact immutable event/evidence identity to investigate."""

    event_id: str
    event_hash: str
    evidence_hashes: tuple[str, ...]
    require_analogue: bool = True
    require_impact: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        object.__setattr__(self, "event_hash", _sha256(self.event_hash, "event_hash"))
        object.__setattr__(self, "evidence_hashes", _hashes(self.evidence_hashes, "evidence_hashes"))
        if type(self.require_analogue) is not bool or type(self.require_impact) is not bool:
            raise IntelligenceAgentError("required finding flags must be bool values")


@dataclass(frozen=True, slots=True)
class IntelligenceAgentRequest:
    """One read-only, point-in-time intelligence research request."""

    run_id: str
    as_of: datetime
    event_search: SearchEventsRequest
    focuses: tuple[IntelligenceFocus, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _identifier(self.run_id, "run_id"))
        object.__setattr__(self, "as_of", _as_of(self.as_of))
        if type(self.event_search) is not SearchEventsRequest:
            raise IntelligenceAgentError("event_search must be a SearchEventsRequest")
        if self.event_search.as_of != self.as_of:
            raise IntelligenceAgentError("event_search must use the request's exact as_of")
        focuses = tuple(self.focuses)
        if not focuses or not all(type(item) is IntelligenceFocus for item in focuses):
            raise IntelligenceAgentError("focuses must contain IntelligenceFocus records")
        if len({item.event_id for item in focuses}) != len(focuses):
            raise IntelligenceAgentError("focuses cannot duplicate event identities")
        object.__setattr__(self, "focuses", focuses)


@dataclass(frozen=True, slots=True)
class SourceResearchFinding:
    """A cited, authorized source/document identity; never raw source content."""

    event_id: str
    evidence_hash: str
    source_id: str
    document_id: str
    document_content_hash: str
    span_start: int
    span_end: int
    published_at: datetime
    available_at: datetime
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        object.__setattr__(self, "source_id", _identifier(self.source_id, "source_id"))
        object.__setattr__(self, "document_id", _identifier(self.document_id, "document_id"))
        object.__setattr__(self, "evidence_hash", _sha256(self.evidence_hash, "evidence_hash"))
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
            raise IntelligenceAgentError("source research span must be a non-empty ordered integer range")
        published_at = _as_of(self.published_at, "published_at")
        available_at = _as_of(self.available_at, "available_at")
        if available_at < published_at:
            raise IntelligenceAgentError("source research finding cannot precede publication")
        object.__setattr__(self, "published_at", published_at)
        object.__setattr__(self, "available_at", available_at)


@dataclass(frozen=True, slots=True)
class EventSummaryFinding:
    """A structured, cited Event summary rather than an unsupported factual narrative."""

    event_id: str
    event_hash: str
    event_type: str
    ontology_version: str
    event_time: datetime
    available_at: datetime
    evidence_hashes: tuple[str, ...]
    lifecycle: str
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        event_type = _identifier(self.event_type, "event_type")
        if event_type.casefold() in _FORBIDDEN_IMPACT_DIRECTIONS:
            raise IntelligenceAgentError("event_type cannot encode a trading action or instruction")
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(
            self,
            "ontology_version",
            _identifier(self.ontology_version, "ontology_version"),
        )
        object.__setattr__(self, "lifecycle", _identifier(self.lifecycle, "lifecycle"))
        object.__setattr__(self, "event_hash", _sha256(self.event_hash, "event_hash"))
        event_time = _as_of(self.event_time, "event_time")
        available_at = _as_of(self.available_at, "available_at")
        if available_at < event_time:
            raise IntelligenceAgentError("event summary finding cannot be available before event_time")
        object.__setattr__(self, "event_time", event_time)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "evidence_hashes", _hashes(self.evidence_hashes, "evidence_hashes"))


@dataclass(frozen=True, slots=True)
class AnalogueFinding:
    """A citation-backed historical analogue, not a prediction or trade recommendation."""

    reference_event_id: str
    reference_event_hash: str
    reference_evidence_hashes: tuple[str, ...]
    analogue_event_id: str
    analogue_event_hash: str
    analogue_event_time: datetime
    analogue_evidence_hashes: tuple[str, ...]
    matching_method_hash: str
    structured_similarity: float
    embedding_similarity: float | None
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
            raise IntelligenceAgentError("an analogue finding cannot reference itself")
        object.__setattr__(
            self,
            "reference_event_hash",
            _sha256(self.reference_event_hash, "reference_event_hash"),
        )
        object.__setattr__(
            self,
            "analogue_event_hash",
            _sha256(self.analogue_event_hash, "analogue_event_hash"),
        )
        object.__setattr__(
            self,
            "matching_method_hash",
            _sha256(self.matching_method_hash, "matching_method_hash"),
        )
        object.__setattr__(
            self,
            "reference_evidence_hashes",
            _hashes(self.reference_evidence_hashes, "reference_evidence_hashes"),
        )
        object.__setattr__(
            self,
            "analogue_evidence_hashes",
            _hashes(self.analogue_evidence_hashes, "analogue_evidence_hashes"),
        )
        if set(self.reference_evidence_hashes).intersection(self.analogue_evidence_hashes):
            raise IntelligenceAgentError("historical analogue evidence must be independent of reference evidence")
        if (
            type(self.structured_similarity) is not float
            or not 0 <= self.structured_similarity <= 1
            or self.structured_similarity != self.structured_similarity
        ):
            raise IntelligenceAgentError("structured_similarity must be a bounded finite float")
        if self.embedding_similarity is not None and (
            type(self.embedding_similarity) is not float
            or not 0 <= self.embedding_similarity <= 1
            or self.embedding_similarity != self.embedding_similarity
        ):
            raise IntelligenceAgentError("embedding_similarity must be a bounded finite float")
        analogue_event_time = _as_of(self.analogue_event_time, "analogue_event_time")
        available_at = _as_of(self.available_at, "available_at")
        if available_at < analogue_event_time:
            raise IntelligenceAgentError("analogue finding cannot be available before event time")
        object.__setattr__(self, "analogue_event_time", analogue_event_time)
        object.__setattr__(self, "available_at", available_at)


@dataclass(frozen=True, slots=True)
class ImpactExplanationFinding:
    """A cited economic impact with no target, price, or execution semantics."""

    impact_id: str
    event_id: str
    event_hash: str
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
            raise IntelligenceAgentError("impact direction cannot encode a trading action or instruction")
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "event_hash", _sha256(self.event_hash, "event_hash"))
        object.__setattr__(self, "evidence_hashes", _hashes(self.evidence_hashes, "evidence_hashes"))
        object.__setattr__(self, "available_at", _as_of(self.available_at, "available_at"))


@dataclass(frozen=True, slots=True)
class IntelligenceAgentTraceEntry:
    """A secret-free, hash-only trace for the single allowed tool invocation."""

    sequence: int
    tool_name: ToolName
    request_hash: str
    response_hash: str
    predecessor_trace_hash: str | None
    trace_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise IntelligenceAgentError("trace sequence must be a positive integer")
        if type(self.tool_name) is not ToolName or self.tool_name is not ToolName.SEARCH_EVENTS:
            raise IntelligenceAgentError("intelligence trace can record only search_events")
        object.__setattr__(self, "request_hash", _sha256(self.request_hash, "request_hash"))
        object.__setattr__(self, "response_hash", _sha256(self.response_hash, "response_hash"))
        if self.predecessor_trace_hash is not None:
            object.__setattr__(
                self,
                "predecessor_trace_hash",
                _sha256(self.predecessor_trace_hash, "predecessor_trace_hash"),
            )
        object.__setattr__(
            self,
            "trace_hash",
            _fingerprint(
                {
                    "predecessor_trace_hash": self.predecessor_trace_hash,
                    "request_hash": self.request_hash,
                    "response_hash": self.response_hash,
                    "sequence": self.sequence,
                    "tool_name": self.tool_name.value,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class IntelligenceAgentResult:
    """Read-only evidence findings that explicitly remain outside trading authority."""

    run_id: str
    as_of: datetime
    source_research: tuple[SourceResearchFinding, ...]
    event_summaries: tuple[EventSummaryFinding, ...]
    analogues: tuple[AnalogueFinding, ...]
    impact_explanations: tuple[ImpactExplanationFinding, ...]
    trace: tuple[IntelligenceAgentTraceEntry, ...]
    lifecycle: Literal["RESEARCH_ONLY"]
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _identifier(self.run_id, "run_id"))
        object.__setattr__(self, "as_of", _as_of(self.as_of))
        source_research = tuple(self.source_research)
        event_summaries = tuple(self.event_summaries)
        analogues = tuple(self.analogues)
        impact_explanations = tuple(self.impact_explanations)
        trace = tuple(self.trace)
        if not source_research or not all(type(item) is SourceResearchFinding for item in source_research):
            raise IntelligenceAgentError("source_research must contain cited source findings")
        if not event_summaries or not all(type(item) is EventSummaryFinding for item in event_summaries):
            raise IntelligenceAgentError("event_summaries must contain cited event findings")
        if not all(type(item) is AnalogueFinding for item in analogues):
            raise IntelligenceAgentError("analogues must contain AnalogueFinding records")
        if not all(type(item) is ImpactExplanationFinding for item in impact_explanations):
            raise IntelligenceAgentError("impact_explanations must contain cited impact findings")
        if len({item.event_id for item in event_summaries}) != len(event_summaries):
            raise IntelligenceAgentError("event_summaries cannot duplicate event identities")
        citation_hashes_by_event = {
            item.event_id: set(item.evidence_hashes) for item in event_summaries
        }
        event_times_by_event = {item.event_id: item.event_time for item in event_summaries}
        source_hashes_by_event: dict[str, set[str]] = {
            item.event_id: set() for item in event_summaries
        }
        for item in source_research:
            if item.event_id in source_hashes_by_event:
                source_hashes_by_event[item.event_id].add(item.evidence_hash)
        if any(
            item.event_id not in citation_hashes_by_event
            or item.evidence_hash not in citation_hashes_by_event[item.event_id]
            or item.available_at > self.as_of
            for item in source_research
        ):
            raise IntelligenceAgentError("source research findings must cite visible selected event evidence")
        if any(
            source_hashes_by_event[event_id] != evidence_hashes
            for event_id, evidence_hashes in citation_hashes_by_event.items()
        ):
            raise IntelligenceAgentError("source research findings must cover every selected event citation")
        if any(item.available_at > self.as_of for item in event_summaries):
            raise IntelligenceAgentError("event summary finding is not visible at as_of")
        if any(
            item.reference_event_id not in citation_hashes_by_event
            or set(item.reference_evidence_hashes)
            != citation_hashes_by_event[item.reference_event_id]
            or item.analogue_event_time >= event_times_by_event[item.reference_event_id]
            or item.available_at > self.as_of
            for item in analogues
        ):
            raise IntelligenceAgentError("analogue findings must be visible and bound to selected evidence")
        if len({(item.reference_event_id, item.analogue_event_id) for item in analogues}) != len(
            analogues
        ):
            raise IntelligenceAgentError("analogue findings cannot duplicate historical event identities")
        if any(
            item.event_id not in citation_hashes_by_event
            or item.event_hash
            != next(
                summary.event_hash
                for summary in event_summaries
                if summary.event_id == item.event_id
            )
            or not set(item.evidence_hashes).issubset(citation_hashes_by_event[item.event_id])
            or item.available_at > self.as_of
            for item in impact_explanations
        ):
            raise IntelligenceAgentError("impact findings must be visible and evidence-bound to selected events")
        if len({(item.event_id, item.impact_id) for item in impact_explanations}) != len(
            impact_explanations
        ):
            raise IntelligenceAgentError("impact findings cannot duplicate event impact identities")
        if len(trace) != 1 or type(trace[0]) is not IntelligenceAgentTraceEntry:
            raise IntelligenceAgentError("intelligence result requires exactly one typed tool trace entry")
        if trace[0].sequence != 1 or trace[0].predecessor_trace_hash is not None:
            raise IntelligenceAgentError("intelligence trace must begin with the sole tool invocation")
        if self.lifecycle != "RESEARCH_ONLY":
            raise IntelligenceAgentError("intelligence output must remain RESEARCH_ONLY")
        object.__setattr__(self, "source_research", source_research)
        object.__setattr__(self, "event_summaries", event_summaries)
        object.__setattr__(self, "analogues", analogues)
        object.__setattr__(self, "impact_explanations", impact_explanations)
        object.__setattr__(self, "trace", trace)


class IntelligenceAgent:
    """Build deterministic cited findings through one closed, read-only tool call."""

    __slots__ = ("_tool_api",)

    def __init__(self, tool_api: TypedResearchToolApi) -> None:
        if type(tool_api) is not TypedResearchToolApi:
            raise IntelligenceAgentError("tool_api must be a TypedResearchToolApi")
        self._tool_api = tool_api

    def run(self, request: IntelligenceAgentRequest) -> IntelligenceAgentResult:
        """Return only explicitly cited source, event, analogue, and impact findings.

        The call is intentionally single-shot.  This agent never retries a port
        failure and never uses a second tool call to fill a missing evidence gap.
        The underlying operation is read-only, so an explicit caller retry is a
        separate deterministic request rather than hidden agent behavior.
        """

        if type(request) is not IntelligenceAgentRequest:
            raise IntelligenceAgentError("request must be an IntelligenceAgentRequest")
        response = self._tool_api.invoke(ToolName.SEARCH_EVENTS, request.event_search)
        if type(response) is not SearchEventsResponse:
            raise IntelligenceAgentError("search_events returned an unexpected response type")
        trace = IntelligenceAgentTraceEntry(
            sequence=1,
            tool_name=ToolName.SEARCH_EVENTS,
            request_hash=self._request_hash(request.event_search),
            response_hash=self._response_hash(response),
            predecessor_trace_hash=None,
        )
        source_research: list[SourceResearchFinding] = []
        event_summaries: list[EventSummaryFinding] = []
        analogues: list[AnalogueFinding] = []
        impact_explanations: list[ImpactExplanationFinding] = []
        for focus in request.focuses:
            event = self._match_event(response, focus)
            source_research.extend(self._source_findings(event))
            event_summaries.append(self._event_finding(event))
            analogue_findings = self._analogue_findings(event, focus)
            if focus.require_analogue and not analogue_findings:
                raise IntelligenceAgentError("required historical analogue is absent or not safely cited")
            analogues.extend(analogue_findings)
            impact_findings = self._impact_findings(event, focus)
            if focus.require_impact and not impact_findings:
                raise IntelligenceAgentError("required impact explanation is absent or not safely cited")
            impact_explanations.extend(impact_findings)
        return IntelligenceAgentResult(
            run_id=request.run_id,
            as_of=request.as_of,
            source_research=tuple(source_research),
            event_summaries=tuple(event_summaries),
            analogues=tuple(analogues),
            impact_explanations=tuple(impact_explanations),
            trace=(trace,),
            lifecycle="RESEARCH_ONLY",
        )

    @staticmethod
    def _match_event(response: SearchEventsResponse, focus: IntelligenceFocus) -> EventSummary:
        matches = tuple(
            event
            for event in response.events
            if event.event_id == focus.event_id
            and event.event_hash == focus.event_hash
            and tuple(citation.evidence_hash for citation in event.evidence_citations)
            == focus.evidence_hashes
        )
        if len(matches) != 1:
            raise IntelligenceAgentError("focused event identity/evidence is absent, ambiguous, or revised")
        return matches[0]

    @staticmethod
    def _source_findings(event: EventSummary) -> tuple[SourceResearchFinding, ...]:
        return tuple(
            SourceResearchFinding(
                event_id=event.event_id,
                evidence_hash=citation.evidence_hash,
                source_id=citation.source_id,
                document_id=citation.document_id,
                document_content_hash=citation.document_content_hash,
                span_start=citation.span_start,
                span_end=citation.span_end,
                published_at=citation.published_at,
                available_at=citation.available_at,
            )
            for citation in event.evidence_citations
        )

    @staticmethod
    def _event_finding(event: EventSummary) -> EventSummaryFinding:
        return EventSummaryFinding(
            event_id=event.event_id,
            event_hash=event.event_hash,
            event_type=event.event_type,
            ontology_version=event.ontology_version,
            event_time=event.event_time,
            available_at=event.available_at,
            evidence_hashes=tuple(citation.evidence_hash for citation in event.evidence_citations),
            lifecycle=event.lifecycle,
        )

    @staticmethod
    def _analogue_findings(
        event: EventSummary,
        focus: IntelligenceFocus,
    ) -> tuple[AnalogueFinding, ...]:
        findings: list[AnalogueFinding] = []
        reference_hashes = tuple(citation.evidence_hash for citation in event.evidence_citations)
        for analogue in event.analogue_summaries:
            analogue_hashes = tuple(
                citation.evidence_hash for citation in analogue.evidence_citations
            )
            if set(reference_hashes).intersection(analogue_hashes):
                raise IntelligenceAgentError("historical analogue must use independent cited evidence")
            findings.append(
                IntelligenceAgent._analogue_finding(
                    analogue=analogue,
                    focus=focus,
                    reference_hashes=reference_hashes,
                )
            )
        return tuple(findings)

    @staticmethod
    def _analogue_finding(
        *,
        analogue: AnalogueSummary,
        focus: IntelligenceFocus,
        reference_hashes: tuple[str, ...],
    ) -> AnalogueFinding:
        return AnalogueFinding(
            reference_event_id=focus.event_id,
            reference_event_hash=focus.event_hash,
            reference_evidence_hashes=reference_hashes,
            analogue_event_id=analogue.analogue_event_id,
            analogue_event_hash=analogue.analogue_event_hash,
            analogue_event_time=analogue.analogue_event_time,
            analogue_evidence_hashes=tuple(
                citation.evidence_hash for citation in analogue.evidence_citations
            ),
            matching_method_hash=analogue.matching_method_hash,
            structured_similarity=analogue.structured_similarity,
            embedding_similarity=analogue.embedding_similarity,
            available_at=analogue.available_at,
        )

    @staticmethod
    def _impact_findings(
        event: EventSummary,
        focus: IntelligenceFocus,
    ) -> tuple[ImpactExplanationFinding, ...]:
        return tuple(
            IntelligenceAgent._impact_finding(impact=impact, focus=focus)
            for impact in event.impact_summaries
        )

    @staticmethod
    def _impact_finding(
        *,
        impact: ImpactSummary,
        focus: IntelligenceFocus,
    ) -> ImpactExplanationFinding:
        return ImpactExplanationFinding(
            impact_id=impact.impact_id,
            event_id=focus.event_id,
            event_hash=focus.event_hash,
            ontology_version=impact.ontology_version,
            mechanism_id=impact.mechanism_id,
            commodity_id=impact.commodity_id,
            direction=impact.direction,
            evidence_hashes=impact.evidence_hashes,
            available_at=impact.available_at,
        )

    @staticmethod
    def _request_hash(request: SearchEventsRequest) -> str:
        return _fingerprint(
            {
                "as_of": request.as_of.isoformat(),
                "limit": request.limit,
                "query_hash": sha256(request.query.encode("utf-8")).hexdigest(),
                "tool_name": ToolName.SEARCH_EVENTS.value,
            }
        )

    @staticmethod
    def _response_hash(response: SearchEventsResponse) -> str:
        return _fingerprint(
            {
                "as_of": response.as_of.isoformat(),
                "events": [
                    IntelligenceAgent._event_projection_hash_payload(event) for event in response.events
                ],
                "tool_name": ToolName.SEARCH_EVENTS.value,
            }
        )

    @staticmethod
    def _event_projection_hash_payload(event: EventSummary) -> dict[str, object]:
        return {
            "analogue_summaries": [
                {
                    "analogue_event_hash": analogue.analogue_event_hash,
                    "analogue_event_id": analogue.analogue_event_id,
                    "analogue_event_time": analogue.analogue_event_time.isoformat(),
                    "available_at": analogue.available_at.isoformat(),
                    "embedding_similarity": analogue.embedding_similarity,
                    "evidence_citations": [
                        {
                            "available_at": citation.available_at.isoformat(),
                            "document_content_hash": citation.document_content_hash,
                            "document_id": citation.document_id,
                            "evidence_hash": citation.evidence_hash,
                            "published_at": citation.published_at.isoformat(),
                            "source_id": citation.source_id,
                            "span_end": citation.span_end,
                            "span_start": citation.span_start,
                        }
                        for citation in analogue.evidence_citations
                    ],
                    "matching_method_hash": analogue.matching_method_hash,
                    "reference_event_id": analogue.reference_event_id,
                    "structured_similarity": analogue.structured_similarity,
                }
                for analogue in event.analogue_summaries
            ],
            "available_at": event.available_at.isoformat(),
            "event_hash": event.event_hash,
            "event_id": event.event_id,
            "event_time": event.event_time.isoformat(),
            "event_type": event.event_type,
            "evidence_citations": [
                {
                    "available_at": citation.available_at.isoformat(),
                    "document_content_hash": citation.document_content_hash,
                    "document_id": citation.document_id,
                    "evidence_hash": citation.evidence_hash,
                    "published_at": citation.published_at.isoformat(),
                    "source_id": citation.source_id,
                    "span_end": citation.span_end,
                    "span_start": citation.span_start,
                }
                for citation in event.evidence_citations
            ],
            "impact_summaries": [
                {
                    "available_at": impact.available_at.isoformat(),
                    "commodity_id": impact.commodity_id,
                    "direction": impact.direction,
                    "evidence_hashes": list(impact.evidence_hashes),
                    "impact_id": impact.impact_id,
                    "mechanism_id": impact.mechanism_id,
                    "ontology_version": impact.ontology_version,
                }
                for impact in event.impact_summaries
            ],
            "lifecycle": event.lifecycle,
            "ontology_version": event.ontology_version,
        }
