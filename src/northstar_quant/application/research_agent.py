"""A deterministic, least-privilege research-agent orchestrator.

The agent owns no domain service, provider client, database, configuration, or
runtime capability.  Its only capability is the closed ``TypedResearchToolApi``
from :mod:`northstar_quant.application.agent_tools`.  A hypothesis and feature
proposal are therefore research artifacts, not executable code, Feature
registration, a trading signal, or an approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
import re
from typing import Literal

from northstar_quant.application.agent_tools import (
    BacktestRunSummary,
    CreateExperimentRequest,
    DatasetSummary,
    EventSummary,
    ExperimentSummary,
    FeatureReference,
    FeatureSelectionMode,
    FeatureSummary,
    GenerateResearchCardRequest,
    GetFeatureRequest,
    ResearchCardSummary,
    RunBacktestRequest,
    RunValidationRequest,
    SearchDatasetsRequest,
    SearchDatasetsResponse,
    SearchEventsRequest,
    SearchEventsResponse,
    ToolName,
    ToolRequest,
    ToolResponse,
    TypedResearchToolApi,
    ValidationSummary,
)


__all__ = [
    "FeatureSpecProposal",
    "ResearchAgent",
    "ResearchAgentError",
    "ResearchAgentRequest",
    "ResearchAgentResult",
    "ResearchAgentTraceEntry",
    "ResearchHypothesis",
    "research_agent_request_hash",
]


class ResearchAgentError(ValueError):
    """Raised when a research plan is incomplete, unsafe, or no longer coherent."""


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?ix)\b(?:api[_ -]?key|password|secret|access[_ -]?token|credential)\b"
    r"\s*(?:=|:)\s*\S+|\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16})\b"
)
_ABSOLUTE_PATH_PATTERN = re.compile(r"(?:^|\s)(?:[A-Za-z]:[\\/]|/|~[\\/])")
_MAX_FEATURE_PROPOSALS = 8


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ResearchAgentError(f"{field_name} must be a string identifier")
    normalized = value.strip()
    if normalized != value or not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ResearchAgentError(f"{field_name} must be a normalized opaque identifier")
    if normalized.casefold() == "latest":
        raise ResearchAgentError(f"{field_name} cannot use the ambiguous 'latest' selector")
    return normalized


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise ResearchAgentError(f"{field_name} must be a lowercase SHA-256 hash")
    return value


def _hashes(value: object, field_name: str, *, minimum: int = 1) -> tuple[str, ...]:
    if isinstance(value, str):
        raise ResearchAgentError(f"{field_name} must be a tuple of hashes")
    try:
        hashes: tuple[object, ...] = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ResearchAgentError(f"{field_name} must be an iterable of hashes") from exc
    if len(hashes) < minimum:
        raise ResearchAgentError(f"{field_name} must contain at least {minimum} hash(es)")
    normalized = tuple(_sha256(item, field_name) for item in hashes)
    if len(set(normalized)) != len(normalized):
        raise ResearchAgentError(f"{field_name} cannot contain duplicate hashes")
    return normalized


def _as_of(value: object, field_name: str = "as_of") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ResearchAgentError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _proposal_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ResearchAgentError(f"{field_name} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > 1_000 or _CONTROL_CHARACTER_PATTERN.search(normalized):
        raise ResearchAgentError(f"{field_name} must be bounded, printable, non-empty text")
    if _SECRET_ASSIGNMENT_PATTERN.search(normalized):
        raise ResearchAgentError(f"{field_name} must not contain a credential or secret")
    if _ABSOLUTE_PATH_PATTERN.search(normalized):
        raise ResearchAgentError(f"{field_name} must not contain a filesystem path")
    return normalized


def _fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _text_digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ResearchHypothesis:
    """An evidence-bound research question, never a trading instruction."""

    hypothesis_id: str
    event_id: str
    event_hash: str
    statement: str
    evidence_hashes: tuple[str, ...]
    as_of: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "hypothesis_id", _identifier(self.hypothesis_id, "hypothesis_id"))
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        object.__setattr__(self, "event_hash", _sha256(self.event_hash, "event_hash"))
        object.__setattr__(self, "statement", _proposal_text(self.statement, "statement"))
        object.__setattr__(self, "evidence_hashes", _hashes(self.evidence_hashes, "evidence_hashes"))
        object.__setattr__(self, "as_of", _as_of(self.as_of))


@dataclass(frozen=True, slots=True)
class FeatureSpecProposal:
    """A non-executable proposal to use one already registered Feature version."""

    proposal_id: str
    hypothesis_id: str
    feature: FeatureReference
    rationale: str
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_id", _identifier(self.proposal_id, "proposal_id"))
        object.__setattr__(self, "hypothesis_id", _identifier(self.hypothesis_id, "hypothesis_id"))
        if type(self.feature) is not FeatureReference:
            raise ResearchAgentError("feature must be an exact FeatureReference")
        object.__setattr__(self, "rationale", _proposal_text(self.rationale, "rationale"))


@dataclass(frozen=True, slots=True)
class ResearchAgentRequest:
    """A fully pre-bound research workflow; no executable or privileged input exists here."""

    run_id: str
    as_of: datetime
    hypothesis: ResearchHypothesis
    feature_proposals: tuple[FeatureSpecProposal, ...]
    event_search: SearchEventsRequest
    dataset_search: SearchDatasetsRequest
    feature_requests: tuple[GetFeatureRequest, ...]
    experiment_request: CreateExperimentRequest
    backtest_request: RunBacktestRequest
    validation_request: RunValidationRequest
    research_card_request: GenerateResearchCardRequest

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _identifier(self.run_id, "run_id"))
        as_of = _as_of(self.as_of)
        object.__setattr__(self, "as_of", as_of)
        if type(self.hypothesis) is not ResearchHypothesis:
            raise ResearchAgentError("hypothesis must be ResearchHypothesis")
        if self.hypothesis.as_of != as_of:
            raise ResearchAgentError("hypothesis.as_of must exactly match request.as_of")

        proposals = tuple(self.feature_proposals)
        if not proposals or len(proposals) > _MAX_FEATURE_PROPOSALS:
            raise ResearchAgentError(
                f"feature_proposals must contain between 1 and {_MAX_FEATURE_PROPOSALS} proposals"
            )
        if not all(type(item) is FeatureSpecProposal for item in proposals):
            raise ResearchAgentError("feature_proposals must contain FeatureSpecProposal records")
        if len({item.proposal_id for item in proposals}) != len(proposals):
            raise ResearchAgentError("feature_proposals cannot contain duplicate proposal_id values")
        if len({item.feature for item in proposals}) != len(proposals):
            raise ResearchAgentError("feature_proposals cannot contain duplicate FeatureReference values")
        if any(item.hypothesis_id != self.hypothesis.hypothesis_id for item in proposals):
            raise ResearchAgentError("every feature proposal must bind the request hypothesis")
        object.__setattr__(self, "feature_proposals", proposals)

        if type(self.event_search) is not SearchEventsRequest:
            raise ResearchAgentError("event_search must be SearchEventsRequest")
        if type(self.dataset_search) is not SearchDatasetsRequest:
            raise ResearchAgentError("dataset_search must be SearchDatasetsRequest")
        if self.event_search.as_of != as_of or self.dataset_search.as_of != as_of:
            raise ResearchAgentError("all search requests must exactly match request.as_of")

        feature_requests = tuple(self.feature_requests)
        if len(feature_requests) != len(proposals) or not all(
            type(item) is GetFeatureRequest for item in feature_requests
        ):
            raise ResearchAgentError("feature_requests must exactly cover the feature proposals")
        if any(item.as_of != as_of for item in feature_requests):
            raise ResearchAgentError("all feature requests must exactly match request.as_of")
        if tuple(item.feature for item in feature_requests) != tuple(item.feature for item in proposals):
            raise ResearchAgentError("feature_requests must preserve the proposal FeatureReference order")
        object.__setattr__(self, "feature_requests", feature_requests)

        if type(self.experiment_request) is not CreateExperimentRequest:
            raise ResearchAgentError("experiment_request must be CreateExperimentRequest")
        if self.experiment_request.as_of != as_of:
            raise ResearchAgentError("experiment_request.as_of must exactly match request.as_of")
        if self.experiment_request.feature_references != tuple(item.feature for item in proposals):
            raise ResearchAgentError("experiment_request must use exactly the proposed FeatureReference values")

        if type(self.backtest_request) is not RunBacktestRequest:
            raise ResearchAgentError("backtest_request must be RunBacktestRequest")
        if (
            self.backtest_request.as_of != as_of
            or self.backtest_request.experiment_id != self.experiment_request.experiment_id
        ):
            raise ResearchAgentError("backtest_request must bind the exact experiment and request.as_of")

        if type(self.validation_request) is not RunValidationRequest:
            raise ResearchAgentError("validation_request must be RunValidationRequest")
        if (
            self.validation_request.as_of != as_of
            or self.validation_request.experiment_id != self.experiment_request.experiment_id
        ):
            raise ResearchAgentError("validation_request must bind the exact experiment and request.as_of")

        if type(self.research_card_request) is not GenerateResearchCardRequest:
            raise ResearchAgentError("research_card_request must be GenerateResearchCardRequest")
        if (
            self.research_card_request.as_of != as_of
            or self.research_card_request.experiment_id != self.experiment_request.experiment_id
            or self.research_card_request.backtest_run_id != self.validation_request.backtest_run_id
        ):
            raise ResearchAgentError("research_card_request must bind the exact experiment/run and as_of")


def research_agent_request_hash(request: ResearchAgentRequest) -> str:
    """Return the deterministic, hash-only commitment for one research-agent request.

    The projection deliberately commits only to opaque identifiers, existing hashes,
    timestamps, and digests of the request's bounded text fields.  Callers can use
    the result to correlate a request without retaining its raw research text.
    """

    if type(request) is not ResearchAgentRequest:
        raise ResearchAgentError("request must be ResearchAgentRequest")
    return _fingerprint(
        {
            "as_of": request.as_of.isoformat(),
            "backtest_request_id": request.backtest_request.backtest_request_id,
            "dataset_query_hash": _text_digest(request.dataset_search.query),
            "event_id": request.hypothesis.event_id,
            "event_hash": request.hypothesis.event_hash,
            "event_query_hash": _text_digest(request.event_search.query),
            "experiment_id": request.experiment_request.experiment_id,
            "feature_proposals": [
                {
                    "feature_id": proposal.feature.feature_id,
                    "feature_version_hash": proposal.feature.feature_version_hash,
                    "hypothesis_id": proposal.hypothesis_id,
                    "proposal_id": proposal.proposal_id,
                    "rationale_hash": _text_digest(proposal.rationale),
                }
                for proposal in request.feature_proposals
            ],
            "hypothesis_evidence_hashes": list(request.hypothesis.evidence_hashes),
            "hypothesis_id": request.hypothesis.hypothesis_id,
            "hypothesis_statement_hash": _text_digest(request.hypothesis.statement),
            "research_card_request_id": request.research_card_request.research_card_request_id,
            "research_decision_id": request.research_card_request.research_decision_id,
            "run_id": request.run_id,
            "validation_request_id": request.validation_request.validation_request_id,
        }
    )


@dataclass(frozen=True, slots=True)
class ResearchAgentTraceEntry:
    """A secret-free immutable record of one ordered typed-tool invocation."""

    sequence: int
    tool_name: ToolName
    request_hash: str
    response_hash: str
    predecessor_trace_hash: str | None
    trace_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ResearchAgentError("sequence must be a positive integer")
        if type(self.tool_name) is not ToolName:
            raise ResearchAgentError("tool_name must be a ToolName")
        request_hash = _sha256(self.request_hash, "request_hash")
        response_hash = _sha256(self.response_hash, "response_hash")
        predecessor = (
            _sha256(self.predecessor_trace_hash, "predecessor_trace_hash")
            if self.predecessor_trace_hash is not None
            else None
        )
        trace_hash = _fingerprint(
            {
                "format": "northstar.research-agent-trace.v1",
                "predecessor_trace_hash": predecessor,
                "request_hash": request_hash,
                "response_hash": response_hash,
                "sequence": self.sequence,
                "tool_name": self.tool_name.value,
            }
        )
        object.__setattr__(self, "request_hash", request_hash)
        object.__setattr__(self, "response_hash", response_hash)
        object.__setattr__(self, "predecessor_trace_hash", predecessor)
        object.__setattr__(self, "trace_hash", trace_hash)


@dataclass(frozen=True, slots=True)
class ResearchAgentResult:
    """A complete research-only evidence chain with no portfolio or trading authority."""

    run_id: str
    as_of: datetime
    hypothesis: ResearchHypothesis
    feature_proposals: tuple[FeatureSpecProposal, ...]
    matched_event: EventSummary
    matched_dataset: DatasetSummary
    features: tuple[FeatureSummary, ...]
    experiment: ExperimentSummary
    backtest: BacktestRunSummary
    validation: ValidationSummary
    research_card: ResearchCardSummary
    trace: tuple[ResearchAgentTraceEntry, ...]
    lifecycle: Literal["RESEARCH_ONLY"]
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _identifier(self.run_id, "run_id"))
        as_of = _as_of(self.as_of)
        object.__setattr__(self, "as_of", as_of)
        if type(self.hypothesis) is not ResearchHypothesis or self.hypothesis.as_of != as_of:
            raise ResearchAgentError("result hypothesis must be typed and bind result.as_of")
        proposals = tuple(self.feature_proposals)
        if not proposals or not all(type(item) is FeatureSpecProposal for item in proposals):
            raise ResearchAgentError("result feature_proposals must contain FeatureSpecProposal records")
        if any(item.hypothesis_id != self.hypothesis.hypothesis_id for item in proposals):
            raise ResearchAgentError("result feature proposals must bind the result hypothesis")
        object.__setattr__(self, "feature_proposals", proposals)
        if type(self.matched_event) is not EventSummary or type(self.matched_dataset) is not DatasetSummary:
            raise ResearchAgentError("result event and dataset must be typed summaries")
        if self.matched_event.available_at > as_of or self.matched_dataset.available_at > as_of:
            raise ResearchAgentError("result event/dataset must be available at result.as_of")
        features = tuple(self.features)
        if not features or not all(type(item) is FeatureSummary for item in features):
            raise ResearchAgentError("result features must contain FeatureSummary records")
        if tuple(item.reference for item in features) != tuple(item.feature for item in proposals):
            raise ResearchAgentError("result features must exactly match proposal FeatureReference values")
        if any(item.available_at > as_of for item in features):
            raise ResearchAgentError("result features must be available at result.as_of")
        object.__setattr__(self, "features", features)
        if not all(
            type(item) is expected
            for item, expected in (
                (self.experiment, ExperimentSummary),
                (self.backtest, BacktestRunSummary),
                (self.validation, ValidationSummary),
                (self.research_card, ResearchCardSummary),
            )
        ):
            raise ResearchAgentError("result workflow artifacts must be typed research summaries")
        if any(
            item.available_at > as_of
            for item in (self.experiment, self.backtest, self.validation, self.research_card)
        ):
            raise ResearchAgentError("result workflow artifacts must be available at result.as_of")
        if (
            self.experiment.experiment_id != self.backtest.experiment_id
            or self.validation.experiment_id != self.experiment.experiment_id
            or self.validation.backtest_run_id != self.backtest.backtest_run_id
            or self.research_card.research_decision_id == ""
            or self.research_card.decision_stage != "RESEARCH_ONLY"
        ):
            raise ResearchAgentError("result research evidence chain is not exactly bound")
        trace = tuple(self.trace)
        if not trace or not all(type(item) is ResearchAgentTraceEntry for item in trace):
            raise ResearchAgentError("trace must contain ResearchAgentTraceEntry records")
        if tuple(item.sequence for item in trace) != tuple(range(1, len(trace) + 1)):
            raise ResearchAgentError("trace sequence must be contiguous and start at one")
        if trace[0].predecessor_trace_hash is not None or any(
            current.predecessor_trace_hash != previous.trace_hash
            for previous, current in zip(trace, trace[1:])
        ):
            raise ResearchAgentError("trace predecessor chain is incomplete or reordered")
        object.__setattr__(self, "trace", trace)
        if self.lifecycle != "RESEARCH_ONLY":
            raise ResearchAgentError("research-agent result must remain RESEARCH_ONLY")


class ResearchAgent:
    """Run one fully pre-bound research chain through the closed typed-tool facade."""

    __slots__ = ("_seen_request_hashes", "_seen_run_ids", "_tool_api")

    def __init__(self, tool_api: TypedResearchToolApi) -> None:
        if type(tool_api) is not TypedResearchToolApi:
            raise ResearchAgentError("tool_api must be TypedResearchToolApi")
        self._tool_api = tool_api
        self._seen_request_hashes: set[str] = set()
        self._seen_run_ids: set[str] = set()

    def run(self, request: ResearchAgentRequest) -> ResearchAgentResult:
        """Execute one deterministic, fail-closed research-only workflow.

        The fixed order is event search, dataset search, exact feature lookup,
        static experiment declaration, trusted backtest, trusted validation, and
        Research-Only card creation.  No failed or unknown action is retried.
        """

        if type(request) is not ResearchAgentRequest:
            raise ResearchAgentError("request must be ResearchAgentRequest")
        request_hash = research_agent_request_hash(request)
        if request.run_id in self._seen_run_ids or request_hash in self._seen_request_hashes:
            raise ResearchAgentError("a research-agent run cannot be retried or replayed automatically")
        # Mark before the first tool call.  A transport failure cannot prove that a
        # side effect did not occur, so it is intentionally not retryable here.
        self._seen_run_ids.add(request.run_id)
        self._seen_request_hashes.add(request_hash)
        trace: list[ResearchAgentTraceEntry] = []

        event_response = self._invoke(ToolName.SEARCH_EVENTS, request.event_search, trace)
        if type(event_response) is not SearchEventsResponse:
            raise ResearchAgentError("search_events returned an unexpected response type")
        matched_event = self._match_event(event_response, request.hypothesis)

        dataset_response = self._invoke(ToolName.SEARCH_DATASETS, request.dataset_search, trace)
        if type(dataset_response) is not SearchDatasetsResponse:
            raise ResearchAgentError("search_datasets returned an unexpected response type")
        matched_dataset = self._match_dataset(dataset_response, request.experiment_request)

        features: list[FeatureSummary] = []
        for feature_request, proposal in zip(
            request.feature_requests,
            request.feature_proposals,
            strict=True,
        ):
            feature = self._invoke(ToolName.GET_FEATURE, feature_request, trace)
            if type(feature) is not FeatureSummary:
                raise ResearchAgentError("get_feature returned an unexpected response type")
            if (
                feature.reference != proposal.feature
                or feature.available_at > request.as_of
                or request.experiment_request.dataset_version_hash not in feature.dataset_version_hashes
                or feature.selection_mode
                is not FeatureSelectionMode.STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY
                or feature.decision_time_safe is not False
            ):
                raise ResearchAgentError("proposed feature is not an exact visible input to the experiment")
            features.append(feature)

        experiment = self._invoke(ToolName.CREATE_EXPERIMENT, request.experiment_request, trace)
        if type(experiment) is not ExperimentSummary:
            raise ResearchAgentError("create_experiment returned an unexpected response type")
        if (
            experiment.experiment_id != request.experiment_request.experiment_id
            or experiment.lifecycle != "STATIC_REPRODUCIBILITY_ONLY"
            or experiment.eligible_for_backtest is not False
        ):
            raise ResearchAgentError("experiment is not a static research-only declaration")

        backtest = self._invoke(ToolName.RUN_BACKTEST, request.backtest_request, trace)
        if type(backtest) is not BacktestRunSummary:
            raise ResearchAgentError("run_backtest returned an unexpected response type")
        if backtest.experiment_id != experiment.experiment_id:
            raise ResearchAgentError("backtest result is not bound to the created experiment")
        if backtest.backtest_run_id != request.validation_request.backtest_run_id:
            raise ResearchAgentError("backtest result does not match the pre-bound validation request")

        validation = self._invoke(ToolName.RUN_VALIDATION, request.validation_request, trace)
        if type(validation) is not ValidationSummary:
            raise ResearchAgentError("run_validation returned an unexpected response type")
        if (
            validation.experiment_id != experiment.experiment_id
            or validation.backtest_run_id != backtest.backtest_run_id
            or validation.validation_request_id != request.validation_request.validation_request_id
            or validation.validation_report_id != request.research_card_request.validation_report_id
        ):
            raise ResearchAgentError("validation result is not bound to the pre-bound research card request")

        research_card = self._invoke(
            ToolName.GENERATE_RESEARCH_CARD,
            request.research_card_request,
            trace,
        )
        if type(research_card) is not ResearchCardSummary:
            raise ResearchAgentError("generate_research_card returned an unexpected response type")
        if (
            research_card.research_card_request_id
            != request.research_card_request.research_card_request_id
            or research_card.research_card_id == ""
            or research_card.research_decision_id != request.research_card_request.research_decision_id
            or research_card.decision_stage != "RESEARCH_ONLY"
        ):
            raise ResearchAgentError("research card did not remain bound to a RESEARCH_ONLY decision")

        return ResearchAgentResult(
            run_id=request.run_id,
            as_of=request.as_of,
            hypothesis=request.hypothesis,
            feature_proposals=request.feature_proposals,
            matched_event=matched_event,
            matched_dataset=matched_dataset,
            features=tuple(features),
            experiment=experiment,
            backtest=backtest,
            validation=validation,
            research_card=research_card,
            trace=tuple(trace),
            lifecycle="RESEARCH_ONLY",
        )

    def _invoke(
        self,
        tool_name: ToolName,
        tool_request: ToolRequest,
        trace: list[ResearchAgentTraceEntry],
    ) -> ToolResponse:
        response = self._tool_api.invoke(tool_name, tool_request)
        predecessor = trace[-1].trace_hash if trace else None
        trace.append(
            ResearchAgentTraceEntry(
                sequence=len(trace) + 1,
                tool_name=tool_name,
                request_hash=self._request_hash(tool_name, tool_request),
                response_hash=self._response_hash(tool_name, response),
                predecessor_trace_hash=predecessor,
            )
        )
        return response

    @staticmethod
    def _match_event(response: SearchEventsResponse, hypothesis: ResearchHypothesis) -> EventSummary:
        matches = tuple(
            event
            for event in response.events
            if event.event_id == hypothesis.event_id
            and event.event_hash == hypothesis.event_hash
            and set(hypothesis.evidence_hashes).issubset(
                {citation.evidence_hash for citation in event.evidence_citations}
            )
        )
        if len(matches) != 1:
            raise ResearchAgentError("hypothesis event/evidence is absent, ambiguous, or unavailable")
        return matches[0]

    @staticmethod
    def _match_dataset(
        response: SearchDatasetsResponse,
        experiment_request: CreateExperimentRequest,
    ) -> DatasetSummary:
        matches = tuple(
            dataset
            for dataset in response.datasets
            if dataset.dataset_version_hash == experiment_request.dataset_version_hash
        )
        if len(matches) != 1:
            raise ResearchAgentError("experiment dataset is absent, ambiguous, or unavailable")
        return matches[0]

    @staticmethod
    def _request_hash(tool_name: ToolName, request: ToolRequest) -> str:
        if tool_name is ToolName.SEARCH_EVENTS and type(request) is SearchEventsRequest:
            payload: dict[str, object] = {
                "as_of": request.as_of.isoformat(),
                "limit": request.limit,
                "query_hash": _text_digest(request.query),
                "tool_name": tool_name.value,
            }
        elif tool_name is ToolName.SEARCH_DATASETS and type(request) is SearchDatasetsRequest:
            payload = {
                "as_of": request.as_of.isoformat(),
                "limit": request.limit,
                "query_hash": _text_digest(request.query),
                "tool_name": tool_name.value,
            }
        elif tool_name is ToolName.GET_FEATURE and type(request) is GetFeatureRequest:
            payload = {
                "as_of": request.as_of.isoformat(),
                "feature_id": request.feature.feature_id,
                "feature_version_hash": request.feature.feature_version_hash,
                "tool_name": tool_name.value,
            }
        elif tool_name is ToolName.CREATE_EXPERIMENT and type(request) is CreateExperimentRequest:
            payload = {
                "as_of": request.as_of.isoformat(),
                "configuration_hash": request.configuration_hash,
                "cost_model_hash": request.cost_model_hash,
                "dataset_version_hash": request.dataset_version_hash,
                "experiment_id": request.experiment_id,
                "feature_version_hashes": [
                    item.feature_version_hash for item in request.feature_references
                ],
                "slippage_model_hash": request.slippage_model_hash,
                "strategy_code_hash": request.strategy_code_hash,
                "strategy_version_hash": request.strategy_version_hash,
                "tool_name": tool_name.value,
            }
        elif tool_name is ToolName.RUN_BACKTEST and type(request) is RunBacktestRequest:
            payload = {
                "as_of": request.as_of.isoformat(),
                "backtest_request_id": request.backtest_request_id,
                "experiment_id": request.experiment_id,
                "tool_name": tool_name.value,
            }
        elif tool_name is ToolName.RUN_VALIDATION and type(request) is RunValidationRequest:
            payload = {
                "as_of": request.as_of.isoformat(),
                "backtest_run_id": request.backtest_run_id,
                "experiment_id": request.experiment_id,
                "tool_name": tool_name.value,
                "validation_request_id": request.validation_request_id,
            }
        elif tool_name is ToolName.GENERATE_RESEARCH_CARD and type(request) is GenerateResearchCardRequest:
            payload = {
                "as_of": request.as_of.isoformat(),
                "backtest_run_id": request.backtest_run_id,
                "experiment_id": request.experiment_id,
                "research_card_request_id": request.research_card_request_id,
                "research_decision_id": request.research_decision_id,
                "tool_name": tool_name.value,
                "validation_report_id": request.validation_report_id,
            }
        else:
            raise ResearchAgentError("trace request does not match the closed tool allowlist")
        return _fingerprint(payload)

    @staticmethod
    def _response_hash(tool_name: ToolName, response: ToolResponse) -> str:
        if tool_name is ToolName.SEARCH_EVENTS and type(response) is SearchEventsResponse:
            payload: dict[str, object] = {
                "as_of": response.as_of.isoformat(),
                "events": [
                    ResearchAgent._event_trace_summary(event)
                    for event in response.events
                ],
                "tool_name": tool_name.value,
            }
        elif tool_name is ToolName.SEARCH_DATASETS and type(response) is SearchDatasetsResponse:
            payload = {
                "as_of": response.as_of.isoformat(),
                "datasets": [
                    {
                        "authorization_status": dataset.authorization_status,
                        "available_at": dataset.available_at.isoformat(),
                        "dataset_id": dataset.dataset_id,
                        "dataset_version_hash": dataset.dataset_version_hash,
                        "lineage_hash": dataset.lineage_hash,
                        "schema_hash": dataset.schema_hash,
                    }
                    for dataset in response.datasets
                ],
                "tool_name": tool_name.value,
            }
        elif tool_name is ToolName.GET_FEATURE and type(response) is FeatureSummary:
            payload = {
                "available_at": response.available_at.isoformat(),
                "dataset_version_hashes": list(response.dataset_version_hashes),
                "decision_time_safe": response.decision_time_safe,
                "feature_id": response.reference.feature_id,
                "feature_version_hash": response.reference.feature_version_hash,
                "lineage_hash": response.lineage_hash,
                "selection_mode": response.selection_mode.value,
                "tool_name": tool_name.value,
            }
        elif tool_name is ToolName.CREATE_EXPERIMENT and type(response) is ExperimentSummary:
            payload = {
                "available_at": response.available_at.isoformat(),
                "dataset_version_hash": response.dataset_version_hash,
                "experiment_id": response.experiment_id,
                "experiment_spec_hash": response.experiment_spec_hash,
                "feature_version_hashes": list(response.feature_version_hashes),
                "lifecycle": response.lifecycle,
                "tool_name": tool_name.value,
            }
        elif tool_name is ToolName.RUN_BACKTEST and type(response) is BacktestRunSummary:
            payload = {
                "available_at": response.available_at.isoformat(),
                "backtest_request_id": response.backtest_request_id,
                "backtest_run_id": response.backtest_run_id,
                "evidence_hash": response.evidence_hash,
                "experiment_id": response.experiment_id,
                "run_manifest_hash": response.run_manifest_hash,
                "tool_name": tool_name.value,
            }
        elif tool_name is ToolName.RUN_VALIDATION and type(response) is ValidationSummary:
            payload = {
                "available_at": response.available_at.isoformat(),
                "backtest_run_id": response.backtest_run_id,
                "evidence_hash": response.evidence_hash,
                "experiment_id": response.experiment_id,
                "tool_name": tool_name.value,
                "validation_report_hash": response.validation_report_hash,
                "validation_report_id": response.validation_report_id,
                "validation_request_id": response.validation_request_id,
            }
        elif tool_name is ToolName.GENERATE_RESEARCH_CARD and type(response) is ResearchCardSummary:
            payload = {
                "available_at": response.available_at.isoformat(),
                "decision_stage": response.decision_stage,
                "research_card_hash": response.research_card_hash,
                "research_card_id": response.research_card_id,
                "research_card_request_id": response.research_card_request_id,
                "research_decision_id": response.research_decision_id,
                "tool_name": tool_name.value,
            }
        else:
            raise ResearchAgentError("trace response does not match the closed tool allowlist")
        return _fingerprint(payload)

    @staticmethod
    def _event_trace_summary(event: EventSummary) -> dict[str, object]:
        """Return a secret-free canonical hash payload for an event projection."""

        return {
            "analogue_summaries": [
                {
                    "analogue_event_hash": analogue.analogue_event_hash,
                    "analogue_event_id": analogue.analogue_event_id,
                    "available_at": analogue.available_at.isoformat(),
                    "embedding_similarity": analogue.embedding_similarity,
                    "evidence_citations": [
                        {
                            "available_at": citation.available_at.isoformat(),
                            "document_content_hash": citation.document_content_hash,
                            "document_id": citation.document_id,
                            "evidence_hash": citation.evidence_hash,
                            "published_at": citation.published_at.isoformat(),
                            "span_end": citation.span_end,
                            "span_start": citation.span_start,
                            "source_id": citation.source_id,
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
                    "span_end": citation.span_end,
                    "span_start": citation.span_start,
                    "source_id": citation.source_id,
                }
                for citation in event.evidence_citations
            ],
            "impact_summaries": [
                {
                    "available_at": impact.available_at.isoformat(),
                    "commodity_id": impact.commodity_id,
                    "direction": impact.direction,
                    "evidence_hashes": list(impact.evidence_hashes),
                    "event_id": impact.event_id,
                    "impact_id": impact.impact_id,
                    "mechanism_id": impact.mechanism_id,
                    "ontology_version": impact.ontology_version,
                }
                for impact in event.impact_summaries
            ],
            "lifecycle": event.lifecycle,
            "ontology_version": event.ontology_version,
        }
