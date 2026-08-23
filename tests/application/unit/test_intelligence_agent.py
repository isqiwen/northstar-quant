"""Unit coverage for the P7 evidence-only Intelligence Agent."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from northstar_quant.application.agent_tools import (
    AnalogueSummary,
    DatasetSummary,
    EvidenceCitationSummary,
    EventSummary,
    FeatureReference,
    FeatureSummary,
    ImpactSummary,
    InspectDatasetQualityRequest,
    DatasetQualityReportSummary,
    ResearchToolDependencies,
    SearchEventsRequest,
    ToolApiError,
    ToolName,
    TypedResearchToolApi,
)
from northstar_quant.application.intelligence_agent import (
    IntelligenceAgent,
    IntelligenceAgentError,
    IntelligenceAgentRequest,
    IntelligenceFocus,
)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


@dataclass
class FakeDatasetCatalog:
    calls: list[tuple[str, datetime, int]] = field(default_factory=list)

    def search_datasets(
        self,
        *,
        query: str,
        as_of: datetime,
        limit: int,
    ) -> tuple[DatasetSummary, ...]:
        self.calls.append((query, as_of, limit))
        return ()


@dataclass
class FakeEventCatalog:
    results: tuple[EventSummary, ...]
    fail: bool = False
    calls: list[tuple[str, datetime, int]] = field(default_factory=list)

    def search_events(
        self,
        *,
        query: str,
        as_of: datetime,
        limit: int,
    ) -> tuple[EventSummary, ...]:
        self.calls.append((query, as_of, limit))
        if self.fail:
            raise ToolApiError("simulated event-catalog failure")
        return self.results


@dataclass
class FakeFeatureCatalog:
    calls: list[tuple[FeatureReference, datetime]] = field(default_factory=list)

    def get_feature(self, *, feature: FeatureReference, as_of: datetime) -> FeatureSummary:
        self.calls.append((feature, as_of))
        raise AssertionError("IntelligenceAgent must not request a Feature")


@dataclass
class FailingIfCalledDataQualityCatalog:
    calls: list[InspectDatasetQualityRequest] = field(default_factory=list)

    def inspect_dataset_quality(
        self,
        *,
        request: InspectDatasetQualityRequest,
    ) -> DatasetQualityReportSummary:
        self.calls.append(request)
        raise AssertionError("IntelligenceAgent must not inspect dataset quality")


@dataclass
class FailingIfCalledWorkflow:
    calls: list[str] = field(default_factory=list)

    def create_experiment(self, **_: object) -> object:
        self.calls.append("create_experiment")
        raise AssertionError("IntelligenceAgent must not create an experiment")

    def run_backtest(self, **_: object) -> object:
        self.calls.append("run_backtest")
        raise AssertionError("IntelligenceAgent must not run a backtest")

    def run_validation(self, **_: object) -> object:
        self.calls.append("run_validation")
        raise AssertionError("IntelligenceAgent must not run validation")

    def compare_experiments(self, **_: object) -> object:
        self.calls.append("compare_experiments")
        raise AssertionError("IntelligenceAgent must not compare experiments")

    def generate_research_card(self, **_: object) -> object:
        self.calls.append("generate_research_card")
        raise AssertionError("IntelligenceAgent must not generate a research card")


@dataclass
class AgentFixture:
    agent: IntelligenceAgent
    api: TypedResearchToolApi
    request: IntelligenceAgentRequest
    now: datetime
    event: EventSummary
    citation: EvidenceCitationSummary
    historic_citation: EvidenceCitationSummary
    analogue: AnalogueSummary
    impact: ImpactSummary
    dataset_catalog: FakeDatasetCatalog
    event_catalog: FakeEventCatalog
    feature_catalog: FakeFeatureCatalog
    data_quality_catalog: FailingIfCalledDataQualityCatalog
    workflow: FailingIfCalledWorkflow


def _fixture() -> AgentFixture:
    now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    citation = EvidenceCitationSummary(
        evidence_hash=_hash("current-event-evidence"),
        document_id="document-current-1",
        source_id="source-current-1",
        document_content_hash=_hash("current-document-content"),
        span_start=12,
        span_end=48,
        published_at=now - timedelta(minutes=30),
        available_at=now - timedelta(minutes=29),
        authorization_status="AUTHORIZED",
    )
    historic_citation = EvidenceCitationSummary(
        evidence_hash=_hash("historical-event-evidence"),
        document_id="document-historical-1",
        source_id="source-historical-1",
        document_content_hash=_hash("historical-document-content"),
        span_start=2,
        span_end=29,
        published_at=now - timedelta(hours=3),
        available_at=now - timedelta(hours=2, minutes=59),
        authorization_status="AUTHORIZED",
    )
    analogue = AnalogueSummary(
        reference_event_id="event-supply-1",
        analogue_event_id="event-supply-historical-1",
        analogue_event_hash=_hash("event-supply-historical-1"),
        analogue_event_time=now - timedelta(hours=2),
        matching_method_hash=_hash("structured-analogue-v1"),
        structured_similarity=0.875,
        embedding_similarity=0.75,
        evidence_citations=(historic_citation,),
        available_at=now - timedelta(hours=1, minutes=58),
    )
    impact = ImpactSummary(
        impact_id="impact-supply-tightening-1",
        event_id="event-supply-1",
        ontology_version="ontology-v1",
        mechanism_id="supply-reduction",
        commodity_id="CU",
        direction="tightening",
        evidence_hashes=(citation.evidence_hash,),
        available_at=now - timedelta(minutes=10),
    )
    event = EventSummary(
        event_id="event-supply-1",
        event_hash=_hash("event-supply-1"),
        event_type="supply-shock",
        ontology_version="ontology-v1",
        event_time=now - timedelta(minutes=20),
        available_at=now - timedelta(minutes=5),
        evidence_citations=(citation,),
        lifecycle="CANONICAL",
        impact_summaries=(impact,),
        analogue_summaries=(analogue,),
    )
    dataset_catalog = FakeDatasetCatalog()
    event_catalog = FakeEventCatalog(results=(event,))
    feature_catalog = FakeFeatureCatalog()
    data_quality_catalog = FailingIfCalledDataQualityCatalog()
    workflow = FailingIfCalledWorkflow()
    api = TypedResearchToolApi(
        ResearchToolDependencies(
            dataset_catalog=dataset_catalog,
            data_quality_catalog=data_quality_catalog,
            event_catalog=event_catalog,
            feature_catalog=feature_catalog,
            research_workflow=workflow,
        )
    )
    request = IntelligenceAgentRequest(
        run_id="intelligence-agent-run-1",
        as_of=now,
        event_search=SearchEventsRequest(
            query="canonical copper supply interruption",
            as_of=now,
            limit=4,
        ),
        focuses=(
            IntelligenceFocus(
                event_id=event.event_id,
                event_hash=event.event_hash,
                evidence_hashes=(citation.evidence_hash,),
            ),
        ),
    )
    return AgentFixture(
        agent=IntelligenceAgent(api),
        api=api,
        request=request,
        now=now,
        event=event,
        citation=citation,
        historic_citation=historic_citation,
        analogue=analogue,
        impact=impact,
        dataset_catalog=dataset_catalog,
        event_catalog=event_catalog,
        feature_catalog=feature_catalog,
        data_quality_catalog=data_quality_catalog,
        workflow=workflow,
    )


def _assert_no_later_capabilities(fixture: AgentFixture) -> None:
    assert fixture.dataset_catalog.calls == []
    assert fixture.data_quality_catalog.calls == []
    assert fixture.feature_catalog.calls == []
    assert fixture.workflow.calls == []


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def test_intelligence_agent_returns_only_cited_research_findings_from_one_event_search() -> None:
    fixture = _fixture()

    result = fixture.agent.run(fixture.request)

    assert result.lifecycle == "RESEARCH_ONLY"
    assert result.eligible_for_trading is False
    assert len(result.source_research) == 1
    source = result.source_research[0]
    assert (
        source.event_id,
        source.evidence_hash,
        source.source_id,
        source.document_id,
        source.document_content_hash,
        source.span_start,
        source.span_end,
    ) == (
        fixture.event.event_id,
        fixture.citation.evidence_hash,
        fixture.citation.source_id,
        fixture.citation.document_id,
        fixture.citation.document_content_hash,
        fixture.citation.span_start,
        fixture.citation.span_end,
    )
    assert result.event_summaries[0].event_hash == fixture.event.event_hash
    assert result.event_summaries[0].evidence_hashes == (fixture.citation.evidence_hash,)
    assert result.analogues[0].reference_event_id == fixture.event.event_id
    assert result.analogues[0].analogue_event_id == fixture.analogue.analogue_event_id
    assert result.analogues[0].analogue_evidence_hashes == (
        fixture.historic_citation.evidence_hash,
    )
    assert result.analogues[0].analogue_event_time < result.event_summaries[0].event_time
    assert result.impact_explanations[0].event_hash == fixture.event.event_hash
    assert result.impact_explanations[0].direction == fixture.impact.direction
    assert result.impact_explanations[0].evidence_hashes == (fixture.citation.evidence_hash,)
    assert all(
        finding.eligible_for_trading is False
        for finding in (
            *result.source_research,
            *result.event_summaries,
            *result.analogues,
            *result.impact_explanations,
        )
    )
    assert len(result.trace) == 1
    trace = result.trace[0]
    assert trace.sequence == 1
    assert trace.tool_name is ToolName.SEARCH_EVENTS
    assert trace.predecessor_trace_hash is None
    assert all(
        _is_sha256(value) for value in (trace.request_hash, trace.response_hash, trace.trace_hash)
    )
    assert fixture.request.event_search.query not in repr(trace)
    with pytest.raises(FrozenInstanceError):
        trace.sequence = 2  # type: ignore[misc]
    assert fixture.event_catalog.calls == [
        (
            fixture.request.event_search.query,
            fixture.now,
            fixture.request.event_search.limit,
        )
    ]
    _assert_no_later_capabilities(fixture)


@pytest.mark.parametrize("mismatch", ("event_id", "event_hash", "evidence"))
def test_intelligence_agent_rejects_mismatched_focus_identity_or_evidence(
    mismatch: str,
) -> None:
    fixture = _fixture()
    focus = fixture.request.focuses[0]
    if mismatch == "event_id":
        mismatched_focus = replace(focus, event_id="event-other-1")
    elif mismatch == "event_hash":
        mismatched_focus = replace(focus, event_hash=_hash("event-other-1"))
    else:
        mismatched_focus = replace(focus, evidence_hashes=(_hash("other-evidence"),))

    with pytest.raises(IntelligenceAgentError, match="identity/evidence is absent"):
        fixture.agent.run(replace(fixture.request, focuses=(mismatched_focus,)))

    assert len(fixture.event_catalog.calls) == 1
    _assert_no_later_capabilities(fixture)


def test_future_projection_components_are_rejected_at_the_typed_boundary() -> None:
    fixture = _fixture()
    future = fixture.now + timedelta(seconds=1)

    with pytest.raises(ToolApiError, match="event evidence is not available"):
        replace(
            fixture.event,
            evidence_citations=(replace(fixture.citation, available_at=future),),
        )
    with pytest.raises(ToolApiError, match="impact summaries must be"):
        replace(
            fixture.event,
            impact_summaries=(replace(fixture.impact, available_at=future),),
        )
    with pytest.raises(ToolApiError, match="analogue summaries must be"):
        replace(
            fixture.event,
            analogue_summaries=(replace(fixture.analogue, available_at=future),),
        )

    fixture.event_catalog.results = (replace(fixture.event, available_at=future),)
    with pytest.raises(ToolApiError, match="event result is not available"):
        fixture.agent.run(fixture.request)

    assert len(fixture.event_catalog.calls) == 1
    _assert_no_later_capabilities(fixture)


def test_analogue_and_impact_summaries_reject_unsafe_semantics_before_agent_execution() -> None:
    fixture = _fixture()

    with pytest.raises(ToolApiError, match="cannot be its own reference"):
        replace(fixture.analogue, analogue_event_id=fixture.event.event_id)
    with pytest.raises(ToolApiError, match="must contain authorized"):
        replace(fixture.analogue, evidence_citations=())
    nonhistorical = replace(
        fixture.analogue,
        analogue_event_time=fixture.event.event_time,
        available_at=fixture.event.available_at,
    )
    with pytest.raises(ToolApiError, match="must be historical"):
        replace(fixture.event, analogue_summaries=(nonhistorical,))
    with pytest.raises(ToolApiError, match="trading action or instruction"):
        replace(fixture.impact, direction="buy")

    _assert_no_later_capabilities(fixture)


@pytest.mark.parametrize("missing", ("analogue", "impact"))
def test_intelligence_agent_requires_requested_analogue_and_impact_findings(missing: str) -> None:
    fixture = _fixture()
    if missing == "analogue":
        fixture.event_catalog.results = (replace(fixture.event, analogue_summaries=()),)
        match = "required historical analogue"
    else:
        fixture.event_catalog.results = (replace(fixture.event, impact_summaries=()),)
        match = "required impact explanation"

    with pytest.raises(IntelligenceAgentError, match=match):
        fixture.agent.run(fixture.request)

    assert len(fixture.event_catalog.calls) == 1
    _assert_no_later_capabilities(fixture)


def test_search_events_request_rejects_secret_like_query_before_the_port_is_called() -> None:
    fixture = _fixture()
    secret_label = "api" + "_key"

    with pytest.raises(ToolApiError, match="credential or secret"):
        SearchEventsRequest(query=f"{secret_label} = {'x' * 32}", as_of=fixture.now)

    assert fixture.event_catalog.calls == []
    _assert_no_later_capabilities(fixture)


def test_intelligence_agent_rejects_an_unexpected_tool_response_without_other_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture()

    def unexpected_response(
        self: TypedResearchToolApi,
        tool_name: ToolName,
        request: object,
    ) -> object:
        assert self is fixture.api
        assert tool_name is ToolName.SEARCH_EVENTS
        assert request is fixture.request.event_search
        return fixture.request.event_search

    monkeypatch.setattr(TypedResearchToolApi, "invoke", unexpected_response)

    with pytest.raises(IntelligenceAgentError, match="unexpected response type"):
        fixture.agent.run(fixture.request)

    assert fixture.event_catalog.calls == []
    _assert_no_later_capabilities(fixture)


def test_intelligence_agent_never_retries_or_uses_later_capabilities_after_port_failure() -> None:
    fixture = _fixture()
    fixture.event_catalog.fail = True

    with pytest.raises(ToolApiError, match="simulated event-catalog failure"):
        fixture.agent.run(fixture.request)

    assert len(fixture.event_catalog.calls) == 1
    _assert_no_later_capabilities(fixture)
