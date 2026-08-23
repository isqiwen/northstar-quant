"""Unit tests for the deterministic, research-only P7 agent workflow."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from northstar_quant.application.agent_tools import (
    BacktestRunSummary,
    CompareExperimentsRequest,
    ComparisonSummary,
    CreateExperimentRequest,
    DatasetSummary,
    EvidenceCitationSummary,
    EventSummary,
    ExperimentSummary,
    FeatureReference,
    FeatureSelectionMode,
    FeatureSummary,
    GenerateResearchCardRequest,
    GetFeatureRequest,
    InspectDatasetQualityRequest,
    DatasetQualityReportSummary,
    ResearchCardSummary,
    ResearchToolDependencies,
    RunBacktestRequest,
    RunValidationRequest,
    SearchDatasetsRequest,
    SearchEventsRequest,
    ToolApiError,
    ToolName,
    TypedResearchToolApi,
    ValidationSummary,
)
from northstar_quant.application.research_agent import (
    FeatureSpecProposal,
    ResearchAgent,
    ResearchAgentError,
    ResearchAgentRequest,
    ResearchHypothesis,
    research_agent_request_hash,
)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


@dataclass
class FakeDatasetCatalog:
    results: tuple[DatasetSummary, ...]
    calls: list[tuple[str, datetime, int]] = field(default_factory=list)

    def search_datasets(
        self,
        *,
        query: str,
        as_of: datetime,
        limit: int,
    ) -> tuple[DatasetSummary, ...]:
        self.calls.append((query, as_of, limit))
        return self.results


@dataclass
class FakeEventCatalog:
    results: tuple[EventSummary, ...]
    calls: list[tuple[str, datetime, int]] = field(default_factory=list)

    def search_events(
        self,
        *,
        query: str,
        as_of: datetime,
        limit: int,
    ) -> tuple[EventSummary, ...]:
        self.calls.append((query, as_of, limit))
        return self.results


@dataclass
class FakeFeatureCatalog:
    results: dict[FeatureReference, FeatureSummary]
    calls: list[tuple[FeatureReference, datetime]] = field(default_factory=list)

    def get_feature(self, *, feature: FeatureReference, as_of: datetime) -> FeatureSummary:
        self.calls.append((feature, as_of))
        return self.results[feature]


@dataclass
class FailingIfCalledDataQualityCatalog:
    calls: list[InspectDatasetQualityRequest] = field(default_factory=list)

    def inspect_dataset_quality(
        self,
        *,
        request: InspectDatasetQualityRequest,
    ) -> DatasetQualityReportSummary:
        self.calls.append(request)
        raise AssertionError("ResearchAgent must not inspect dataset quality")


@dataclass
class FakeResearchWorkflow:
    create_calls: list[tuple[CreateExperimentRequest, tuple[FeatureSummary, ...]]] = field(
        default_factory=list
    )
    backtest_calls: list[RunBacktestRequest] = field(default_factory=list)
    validation_calls: list[RunValidationRequest] = field(default_factory=list)
    card_calls: list[GenerateResearchCardRequest] = field(default_factory=list)
    fail_backtest: bool = False

    def create_experiment(
        self,
        *,
        request: CreateExperimentRequest,
        features: tuple[FeatureSummary, ...],
    ) -> ExperimentSummary:
        self.create_calls.append((request, features))
        return ExperimentSummary(
            experiment_id=request.experiment_id,
            experiment_spec_hash=_hash("experiment-spec"),
            dataset_version_hash=request.dataset_version_hash,
            feature_version_hashes=tuple(
                feature.feature_version_hash for feature in request.feature_references
            ),
            available_at=request.as_of,
            lifecycle="STATIC_REPRODUCIBILITY_ONLY",
        )

    def run_backtest(self, *, request: RunBacktestRequest) -> BacktestRunSummary:
        self.backtest_calls.append(request)
        if self.fail_backtest:
            raise ToolApiError("simulated backtest workflow failure")
        return BacktestRunSummary(
            experiment_id=request.experiment_id,
            backtest_request_id=request.backtest_request_id,
            backtest_run_id="backtest-run-1",
            run_manifest_hash=_hash("run-manifest"),
            evidence_hash=_hash("backtest-evidence"),
            available_at=request.as_of,
        )

    def run_validation(self, *, request: RunValidationRequest) -> ValidationSummary:
        self.validation_calls.append(request)
        return ValidationSummary(
            experiment_id=request.experiment_id,
            backtest_run_id=request.backtest_run_id,
            validation_request_id=request.validation_request_id,
            validation_report_id="validation-report-1",
            validation_report_hash=_hash("validation-report"),
            evidence_hash=_hash("validation-evidence"),
            available_at=request.as_of,
        )

    def compare_experiments(self, *, request: CompareExperimentsRequest) -> ComparisonSummary:
        return ComparisonSummary(
            comparison_request_id=request.comparison_request_id,
            comparison_id="comparison-1",
            experiment_ids=request.experiment_ids,
            comparability_hash=_hash("comparability"),
            available_at=request.as_of,
            comparable=True,
        )

    def generate_research_card(
        self,
        *,
        request: GenerateResearchCardRequest,
    ) -> ResearchCardSummary:
        self.card_calls.append(request)
        return ResearchCardSummary(
            research_card_request_id=request.research_card_request_id,
            research_card_id="research-card-1",
            research_card_hash=_hash("research-card"),
            research_decision_id=request.research_decision_id,
            decision_stage="RESEARCH_ONLY",
            available_at=request.as_of,
        )


@dataclass
class AgentFixture:
    agent: ResearchAgent
    api: TypedResearchToolApi
    request: ResearchAgentRequest
    now: datetime
    dataset: DatasetSummary
    event: EventSummary
    feature: FeatureSummary
    feature_reference: FeatureReference
    dataset_catalog: FakeDatasetCatalog
    event_catalog: FakeEventCatalog
    feature_catalog: FakeFeatureCatalog
    data_quality_catalog: FailingIfCalledDataQualityCatalog
    workflow: FakeResearchWorkflow


def _fixture() -> AgentFixture:
    now = datetime(2026, 8, 23, 11, 0, tzinfo=UTC)
    dataset_hash = _hash("dataset-v1")
    feature_reference = FeatureReference(
        feature_id="inventory-surprise-feature",
        feature_version_hash=_hash("inventory-surprise-feature-v1"),
    )
    dataset = DatasetSummary(
        dataset_id="futures.daily",
        dataset_version_hash=dataset_hash,
        available_at=now - timedelta(minutes=3),
        schema_hash=_hash("dataset-schema"),
        lineage_hash=_hash("dataset-lineage"),
        authorization_status="AUTHORIZED",
    )
    event = EventSummary(
        event_id="event-supply-1",
        event_hash=_hash("event-supply-1"),
        event_type="supply-shock",
        ontology_version="ontology-v1",
        event_time=now - timedelta(minutes=3),
        available_at=now - timedelta(minutes=2),
        evidence_citations=(
            EvidenceCitationSummary(
                evidence_hash=_hash("event-evidence-1"),
                document_id="document-supply-1",
                source_id="source-supply-1",
                document_content_hash=_hash("document-content-supply-1"),
                span_start=0,
                span_end=15,
                published_at=now - timedelta(minutes=4),
                available_at=now - timedelta(minutes=3),
                authorization_status="AUTHORIZED",
            ),
        ),
        lifecycle="CANONICAL",
    )
    feature = FeatureSummary(
        reference=feature_reference,
        available_at=now - timedelta(minutes=1),
        lineage_hash=_hash("feature-lineage"),
        dataset_version_hashes=(dataset_hash,),
        selection_mode=FeatureSelectionMode.STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY,
        decision_time_safe=False,
    )
    dataset_catalog = FakeDatasetCatalog(results=(dataset,))
    event_catalog = FakeEventCatalog(results=(event,))
    feature_catalog = FakeFeatureCatalog(results={feature_reference: feature})
    data_quality_catalog = FailingIfCalledDataQualityCatalog()
    workflow = FakeResearchWorkflow()
    api = TypedResearchToolApi(
        ResearchToolDependencies(
            dataset_catalog=dataset_catalog,
            data_quality_catalog=data_quality_catalog,
            event_catalog=event_catalog,
            feature_catalog=feature_catalog,
            research_workflow=workflow,
        )
    )
    hypothesis = ResearchHypothesis(
        hypothesis_id="supply-shock-hypothesis-1",
        event_id=event.event_id,
        event_hash=event.event_hash,
        statement="Test whether the canonical supply shock changes the registered feature.",
        evidence_hashes=tuple(citation.evidence_hash for citation in event.evidence_citations),
        as_of=now,
    )
    proposal = FeatureSpecProposal(
        proposal_id="inventory-surprise-proposal-1",
        hypothesis_id=hypothesis.hypothesis_id,
        feature=feature_reference,
        rationale="Use only the registered decision-point-in-time feature version.",
    )
    experiment_request = CreateExperimentRequest(
        experiment_id="experiment-supply-1",
        dataset_version_hash=dataset.dataset_version_hash,
        feature_references=(feature_reference,),
        strategy_version_hash=_hash("strategy-version"),
        strategy_code_hash=_hash("strategy-code"),
        configuration_hash=_hash("configuration"),
        cost_model_hash=_hash("cost-model"),
        slippage_model_hash=_hash("slippage-model"),
        as_of=now,
    )
    request = ResearchAgentRequest(
        run_id="research-agent-run-1",
        as_of=now,
        hypothesis=hypothesis,
        feature_proposals=(proposal,),
        event_search=SearchEventsRequest(query="canonical supply shock", as_of=now),
        dataset_search=SearchDatasetsRequest(query="daily futures dataset", as_of=now),
        feature_requests=(GetFeatureRequest(feature=feature_reference, as_of=now),),
        experiment_request=experiment_request,
        backtest_request=RunBacktestRequest(
            experiment_id=experiment_request.experiment_id,
            backtest_request_id="backtest-request-1",
            as_of=now,
        ),
        validation_request=RunValidationRequest(
            experiment_id=experiment_request.experiment_id,
            backtest_run_id="backtest-run-1",
            validation_request_id="validation-request-1",
            as_of=now,
        ),
        research_card_request=GenerateResearchCardRequest(
            experiment_id=experiment_request.experiment_id,
            backtest_run_id="backtest-run-1",
            validation_report_id="validation-report-1",
            research_decision_id="research-decision-1",
            research_card_request_id="research-card-request-1",
            as_of=now,
        ),
    )
    return AgentFixture(
        agent=ResearchAgent(api),
        api=api,
        request=request,
        now=now,
        dataset=dataset,
        event=event,
        feature=feature,
        feature_reference=feature_reference,
        dataset_catalog=dataset_catalog,
        event_catalog=event_catalog,
        feature_catalog=feature_catalog,
        data_quality_catalog=data_quality_catalog,
        workflow=workflow,
    )


def _assert_no_workflow_mutation(fixture: AgentFixture) -> None:
    assert fixture.workflow.create_calls == []
    assert fixture.workflow.backtest_calls == []
    assert fixture.workflow.validation_calls == []
    assert fixture.workflow.card_calls == []


def test_research_agent_request_hash_is_public_pure_and_binds_the_complete_request() -> None:
    fixture = _fixture()

    request_hash = research_agent_request_hash(fixture.request)

    assert len(request_hash) == 64
    assert request_hash == research_agent_request_hash(fixture.request)
    assert request_hash != research_agent_request_hash(
        replace(fixture.request, run_id="research-agent-run-2")
    )
    assert fixture.event_catalog.calls == []
    assert fixture.dataset_catalog.calls == []
    assert fixture.feature_catalog.calls == []
    _assert_no_workflow_mutation(fixture)


def test_research_agent_request_hash_rejects_non_request_records() -> None:
    with pytest.raises(ResearchAgentError, match="request must be ResearchAgentRequest"):
        research_agent_request_hash(object())  # type: ignore[arg-type]


def test_research_agent_runs_the_complete_seven_step_research_only_chain_with_immutable_trace() -> None:
    fixture = _fixture()

    result = fixture.agent.run(fixture.request)

    assert result.lifecycle == "RESEARCH_ONLY"
    assert result.eligible_for_trading is False
    assert result.feature_proposals[0].eligible_for_trading is False
    assert result.matched_event == fixture.event
    assert result.matched_dataset == fixture.dataset
    assert result.features == (fixture.feature,)
    assert result.experiment.eligible_for_backtest is False
    assert result.research_card.decision_stage == "RESEARCH_ONLY"
    assert [entry.tool_name for entry in result.trace] == [
        ToolName.SEARCH_EVENTS,
        ToolName.SEARCH_DATASETS,
        ToolName.GET_FEATURE,
        ToolName.CREATE_EXPERIMENT,
        ToolName.RUN_BACKTEST,
        ToolName.RUN_VALIDATION,
        ToolName.GENERATE_RESEARCH_CARD,
    ]
    assert [entry.sequence for entry in result.trace] == list(range(1, 8))
    assert result.trace[0].predecessor_trace_hash is None
    assert all(
        current.predecessor_trace_hash == previous.trace_hash
        for previous, current in zip(result.trace, result.trace[1:])
    )
    with pytest.raises(FrozenInstanceError):
        result.trace[0].sequence = 99  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.trace += ()  # type: ignore[misc]
    assert fixture.workflow.create_calls == [(fixture.request.experiment_request, (fixture.feature,))]
    assert fixture.workflow.backtest_calls == [fixture.request.backtest_request]
    assert fixture.workflow.validation_calls == [fixture.request.validation_request]
    assert fixture.workflow.card_calls == [fixture.request.research_card_request]


def test_research_agent_rejects_event_evidence_mismatch_before_dataset_or_workflow_access() -> None:
    fixture = _fixture()
    mismatched_hypothesis = replace(
        fixture.request.hypothesis,
        evidence_hashes=(_hash("unmatched-event-evidence"),),
    )
    request = replace(fixture.request, hypothesis=mismatched_hypothesis)

    with pytest.raises(ResearchAgentError, match="event/evidence is absent"):
        fixture.agent.run(request)

    assert len(fixture.event_catalog.calls) == 1
    assert fixture.dataset_catalog.calls == []
    assert fixture.feature_catalog.calls == []
    _assert_no_workflow_mutation(fixture)


def test_research_agent_rejects_a_revised_event_hash_before_dataset_or_workflow_access() -> None:
    fixture = _fixture()
    request = replace(
        fixture.request,
        hypothesis=replace(
            fixture.request.hypothesis,
            event_hash=_hash("revised-event-supply-1"),
        ),
    )

    with pytest.raises(ResearchAgentError, match="event/evidence is absent"):
        fixture.agent.run(request)

    assert len(fixture.event_catalog.calls) == 1
    assert fixture.dataset_catalog.calls == []
    assert fixture.feature_catalog.calls == []
    _assert_no_workflow_mutation(fixture)


@pytest.mark.parametrize("available", [False, True])
def test_research_agent_rejects_missing_or_mismatched_dataset_before_feature_or_workflow_access(
    available: bool,
) -> None:
    fixture = _fixture()
    if available:
        fixture.dataset_catalog.results = (
            replace(fixture.dataset, dataset_version_hash=_hash("other-dataset-v1")),
        )
    else:
        fixture.dataset_catalog.results = ()

    with pytest.raises(ResearchAgentError, match="experiment dataset is absent"):
        fixture.agent.run(fixture.request)

    assert len(fixture.event_catalog.calls) == 1
    assert len(fixture.dataset_catalog.calls) == 1
    assert fixture.feature_catalog.calls == []
    _assert_no_workflow_mutation(fixture)


def test_research_agent_fails_closed_for_a_future_visible_dataset_before_feature_or_workflow_access() -> None:
    fixture = _fixture()
    fixture.dataset_catalog.results = (
        replace(fixture.dataset, available_at=fixture.now + timedelta(seconds=1)),
    )

    with pytest.raises(ToolApiError, match="not available"):
        fixture.agent.run(fixture.request)

    assert len(fixture.event_catalog.calls) == 1
    assert len(fixture.dataset_catalog.calls) == 1
    assert fixture.feature_catalog.calls == []
    _assert_no_workflow_mutation(fixture)


def test_feature_proposal_and_request_mismatch_is_rejected_before_agent_execution() -> None:
    fixture = _fixture()
    other_feature = FeatureReference(
        feature_id="other-feature",
        feature_version_hash=_hash("other-feature-v1"),
    )

    with pytest.raises(ResearchAgentError, match="feature_requests must preserve"):
        replace(
            fixture.request,
            feature_requests=(GetFeatureRequest(feature=other_feature, as_of=fixture.now),),
        )

    assert fixture.event_catalog.calls == []
    assert fixture.dataset_catalog.calls == []
    assert fixture.feature_catalog.calls == []
    _assert_no_workflow_mutation(fixture)


def test_hypothesis_rejects_secret_like_text_before_agent_execution() -> None:
    fixture = _fixture()

    with pytest.raises(ResearchAgentError, match="credential or secret"):
        replace(fixture.request.hypothesis, statement=f"api_key = {'x' * 32}")

    assert fixture.event_catalog.calls == []
    assert fixture.dataset_catalog.calls == []
    assert fixture.feature_catalog.calls == []
    _assert_no_workflow_mutation(fixture)


def test_non_static_feature_response_is_rejected_before_experiment_mutation() -> None:
    fixture = _fixture()
    fixture.feature_catalog.results[fixture.feature_reference] = replace(
        fixture.feature,
        selection_mode=FeatureSelectionMode.PER_DECISION_POINT_IN_TIME_REPLAY,
        decision_time_safe=True,
    )

    with pytest.raises(ResearchAgentError, match="proposed feature is not an exact visible input"):
        fixture.agent.run(fixture.request)

    assert len(fixture.event_catalog.calls) == 1
    assert len(fixture.dataset_catalog.calls) == 1
    assert len(fixture.feature_catalog.calls) == 1
    _assert_no_workflow_mutation(fixture)


def test_malformed_request_chain_is_rejected_before_any_tool_or_workflow_mutation() -> None:
    fixture = _fixture()
    malformed_card_request = replace(
        fixture.request.research_card_request,
        backtest_run_id="unexpected-backtest-run",
    )

    with pytest.raises(ResearchAgentError, match="research_card_request must bind"):
        replace(fixture.request, research_card_request=malformed_card_request)

    assert fixture.event_catalog.calls == []
    assert fixture.dataset_catalog.calls == []
    assert fixture.feature_catalog.calls == []
    _assert_no_workflow_mutation(fixture)


def test_research_agent_does_not_progress_after_a_workflow_failure() -> None:
    fixture = _fixture()
    fixture.workflow.fail_backtest = True

    with pytest.raises(ToolApiError, match="simulated backtest workflow failure"):
        fixture.agent.run(fixture.request)

    assert len(fixture.workflow.create_calls) == 1
    assert len(fixture.workflow.backtest_calls) == 1
    assert fixture.workflow.validation_calls == []
    assert fixture.workflow.card_calls == []


def test_research_agent_rejects_an_unknown_tool_response_without_progressing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture()
    original_invoke = TypedResearchToolApi.invoke

    def unknown_experiment_response(
        self: TypedResearchToolApi,
        tool_name: ToolName,
        request: object,
    ) -> object:
        if tool_name is ToolName.CREATE_EXPERIMENT:
            return BacktestRunSummary(
                experiment_id="experiment-supply-1",
                backtest_request_id="unexpected-backtest-request",
                backtest_run_id="unexpected-backtest-run",
                run_manifest_hash=_hash("unexpected-manifest"),
                evidence_hash=_hash("unexpected-evidence"),
                available_at=fixture.now,
            )
        return original_invoke(self, tool_name, request)  # type: ignore[arg-type]

    monkeypatch.setattr(TypedResearchToolApi, "invoke", unknown_experiment_response)

    with pytest.raises(ResearchAgentError, match="trace response does not match"):
        fixture.agent.run(fixture.request)

    assert fixture.workflow.create_calls == []
    assert fixture.workflow.backtest_calls == []
    assert fixture.workflow.validation_calls == []
    assert fixture.workflow.card_calls == []


def test_failed_run_is_marked_before_first_tool_call_and_cannot_be_retried() -> None:
    fixture = _fixture()
    failed_request = replace(
        fixture.request,
        hypothesis=replace(
            fixture.request.hypothesis,
            evidence_hashes=(_hash("unmatched-event-evidence"),),
        ),
    )

    with pytest.raises(ResearchAgentError, match="event/evidence is absent"):
        fixture.agent.run(failed_request)
    with pytest.raises(ResearchAgentError, match="cannot be retried or replayed"):
        fixture.agent.run(failed_request)

    assert len(fixture.event_catalog.calls) == 1
    assert fixture.dataset_catalog.calls == []
    _assert_no_workflow_mutation(fixture)
