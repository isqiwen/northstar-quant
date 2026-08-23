"""Unit coverage for the fail-closed P7 typed research-tool facade."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from northstar_quant.application.agent_tools import (
    AnalogueSummary,
    BacktestRunSummary,
    CompareExperimentsRequest,
    ComparisonSummary,
    CreateExperimentRequest,
    DataQualityFindingKind,
    DataQualityFindingStatus,
    DataQualityFindingSummary,
    DatasetQualityReportSummary,
    DatasetSummary,
    EvidenceCitationSummary,
    EventSummary,
    ExperimentSummary,
    FeatureReference,
    FeatureSelectionMode,
    FeatureSummary,
    GenerateResearchCardRequest,
    GetFeatureRequest,
    ImpactSummary,
    InspectDatasetQualityRequest,
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
class FakeDataQualityCatalog:
    report: DatasetQualityReportSummary
    calls: list[InspectDatasetQualityRequest] = field(default_factory=list)

    def inspect_dataset_quality(
        self,
        *,
        request: InspectDatasetQualityRequest,
    ) -> DatasetQualityReportSummary:
        self.calls.append(request)
        return self.report


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
    features: dict[FeatureReference, FeatureSummary]
    calls: list[tuple[FeatureReference, datetime]] = field(default_factory=list)

    def get_feature(self, *, feature: FeatureReference, as_of: datetime) -> FeatureSummary:
        self.calls.append((feature, as_of))
        return self.features[feature]


@dataclass
class FakeResearchWorkflow:
    create_calls: list[tuple[CreateExperimentRequest, tuple[FeatureSummary, ...]]] = field(
        default_factory=list
    )
    backtest_calls: list[RunBacktestRequest] = field(default_factory=list)
    validation_calls: list[RunValidationRequest] = field(default_factory=list)
    comparison_calls: list[CompareExperimentsRequest] = field(default_factory=list)
    card_calls: list[GenerateResearchCardRequest] = field(default_factory=list)

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
                reference.feature_version_hash for reference in request.feature_references
            ),
            available_at=request.as_of,
            lifecycle="STATIC_REPRODUCIBILITY_ONLY",
        )

    def run_backtest(self, *, request: RunBacktestRequest) -> BacktestRunSummary:
        self.backtest_calls.append(request)
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
        self.comparison_calls.append(request)
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
class ToolFixture:
    api: TypedResearchToolApi
    now: datetime
    dataset: DatasetSummary
    event: EventSummary
    feature_reference: FeatureReference
    feature: FeatureSummary
    dataset_catalog: FakeDatasetCatalog
    data_quality_catalog: FakeDataQualityCatalog
    event_catalog: FakeEventCatalog
    feature_catalog: FakeFeatureCatalog
    workflow: FakeResearchWorkflow


def _fixture() -> ToolFixture:
    now = datetime(2026, 8, 23, 10, 30, tzinfo=UTC)
    dataset_hash = _hash("dataset-v1")
    feature_reference = FeatureReference(
        feature_id="carry-feature",
        feature_version_hash=_hash("carry-feature-v1"),
    )
    dataset = DatasetSummary(
        dataset_id="futures.daily",
        dataset_version_hash=dataset_hash,
        available_at=now - timedelta(minutes=5),
        schema_hash=_hash("dataset-schema"),
        lineage_hash=_hash("dataset-lineage"),
        authorization_status="AUTHORIZED",
    )
    quality_findings = tuple(
        DataQualityFindingSummary(
            kind=kind,
            status=DataQualityFindingStatus.NOT_DETECTED,
            reason_code=f"{kind.name}_NOT_DETECTED",
            finding_hash=_hash(f"quality-finding-{kind.value}"),
            evidence_hashes=(_hash(f"quality-evidence-{kind.value}"),),
            available_at=now - timedelta(minutes=2),
        )
        for kind in DataQualityFindingKind
    )
    quality_report = DatasetQualityReportSummary(
        dataset_id=dataset.dataset_id,
        dataset_version_hash=dataset.dataset_version_hash,
        schema_hash=dataset.schema_hash,
        lineage_hash=dataset.lineage_hash,
        assessment_hash=_hash("quality-assessment"),
        lineage_verification_hash=_hash("lineage-verification"),
        findings=quality_findings,
        available_at=now - timedelta(minutes=1),
        authorization_status="AUTHORIZED",
    )
    event = EventSummary(
        event_id="event-1",
        event_hash=_hash("event-1"),
        event_type="supply-shock",
        ontology_version="ontology-v1",
        event_time=now - timedelta(minutes=5),
        available_at=now - timedelta(minutes=4),
        evidence_citations=(
            EvidenceCitationSummary(
                evidence_hash=_hash("event-evidence"),
                document_id="document-1",
                source_id="source-1",
                document_content_hash=_hash("document-content-1"),
                span_start=0,
                span_end=12,
                published_at=now - timedelta(minutes=6),
                available_at=now - timedelta(minutes=5),
                authorization_status="AUTHORIZED",
            ),
        ),
        lifecycle="CANONICAL",
    )
    feature = FeatureSummary(
        reference=feature_reference,
        available_at=now - timedelta(minutes=3),
        lineage_hash=_hash("feature-lineage"),
        dataset_version_hashes=(dataset_hash,),
        selection_mode=FeatureSelectionMode.PER_DECISION_POINT_IN_TIME_REPLAY,
        decision_time_safe=True,
    )
    dataset_catalog = FakeDatasetCatalog(results=(dataset,))
    data_quality_catalog = FakeDataQualityCatalog(report=quality_report)
    event_catalog = FakeEventCatalog(results=(event,))
    feature_catalog = FakeFeatureCatalog(features={feature_reference: feature})
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
    return ToolFixture(
        api=api,
        now=now,
        dataset=dataset,
        event=event,
        feature_reference=feature_reference,
        feature=feature,
        dataset_catalog=dataset_catalog,
        data_quality_catalog=data_quality_catalog,
        event_catalog=event_catalog,
        feature_catalog=feature_catalog,
        workflow=workflow,
    )


def _create_experiment_request(fixture: ToolFixture) -> CreateExperimentRequest:
    return CreateExperimentRequest(
        experiment_id="experiment-1",
        dataset_version_hash=fixture.dataset.dataset_version_hash,
        feature_references=(fixture.feature_reference,),
        strategy_version_hash=_hash("strategy-version"),
        strategy_code_hash=_hash("strategy-code"),
        configuration_hash=_hash("configuration"),
        cost_model_hash=_hash("cost-model"),
        slippage_model_hash=_hash("slippage-model"),
        as_of=fixture.now,
    )


def _requests(fixture: ToolFixture) -> tuple[object, ...]:
    return (
        SearchDatasetsRequest(query="futures", as_of=fixture.now, limit=5),
        SearchEventsRequest(query="supply", as_of=fixture.now, limit=5),
        GetFeatureRequest(feature=fixture.feature_reference, as_of=fixture.now),
        _create_experiment_request(fixture),
        RunBacktestRequest(
            experiment_id="experiment-1",
            backtest_request_id="backtest-request-1",
            as_of=fixture.now,
        ),
        RunValidationRequest(
            experiment_id="experiment-1",
            backtest_run_id="backtest-run-1",
            validation_request_id="validation-request-1",
            as_of=fixture.now,
        ),
        CompareExperimentsRequest(
            experiment_ids=("experiment-1", "experiment-2"),
            comparison_request_id="comparison-request-1",
            as_of=fixture.now,
        ),
        GenerateResearchCardRequest(
            experiment_id="experiment-1",
            backtest_run_id="backtest-run-1",
            validation_report_id="validation-report-1",
            research_decision_id="research-decision-1",
            research_card_request_id="research-card-request-1",
            as_of=fixture.now,
        ),
        InspectDatasetQualityRequest(dataset=fixture.dataset, as_of=fixture.now),
    )


def test_all_nine_tool_methods_use_only_typed_injected_research_ports() -> None:
    fixture = _fixture()
    (
        search_datasets,
        search_events,
        get_feature,
        create_experiment,
        run_backtest,
        run_validation,
        compare_experiments,
        generate_research_card,
        inspect_dataset_quality,
    ) = _requests(fixture)

    dataset_response = fixture.api.search_datasets(search_datasets)  # type: ignore[arg-type]
    event_response = fixture.api.search_events(search_events)  # type: ignore[arg-type]
    feature_response = fixture.api.get_feature(get_feature)  # type: ignore[arg-type]
    experiment_response = fixture.api.create_experiment(create_experiment)  # type: ignore[arg-type]
    backtest_response = fixture.api.run_backtest(run_backtest)  # type: ignore[arg-type]
    validation_response = fixture.api.run_validation(run_validation)  # type: ignore[arg-type]
    comparison_response = fixture.api.compare_experiments(compare_experiments)  # type: ignore[arg-type]
    card_response = fixture.api.generate_research_card(generate_research_card)  # type: ignore[arg-type]
    quality_response = fixture.api.inspect_dataset_quality(inspect_dataset_quality)  # type: ignore[arg-type]

    assert dataset_response.datasets == (fixture.dataset,)
    assert event_response.events == (fixture.event,)
    assert feature_response == fixture.feature
    assert experiment_response.lifecycle == "STATIC_REPRODUCIBILITY_ONLY"
    assert experiment_response.eligible_for_backtest is False
    assert backtest_response.backtest_run_id == "backtest-run-1"
    assert validation_response.validation_report_id == "validation-report-1"
    assert comparison_response.comparable is True
    assert card_response.decision_stage == "RESEARCH_ONLY"
    assert quality_response == fixture.data_quality_catalog.report
    assert {finding.kind for finding in quality_response.findings} == set(DataQualityFindingKind)
    assert all(
        response.eligible_for_trading is False
        for response in (
            dataset_response,
            event_response,
            feature_response,
            experiment_response,
            backtest_response,
            validation_response,
            comparison_response,
            card_response,
            quality_response,
        )
    )
    assert fixture.dataset_catalog.calls == [("futures", fixture.now, 5)]
    assert fixture.data_quality_catalog.calls == [inspect_dataset_quality]
    assert fixture.event_catalog.calls == [("supply", fixture.now, 5)]
    assert fixture.feature_catalog.calls == [
        (fixture.feature_reference, fixture.now),
        (fixture.feature_reference, fixture.now),
    ]
    assert fixture.workflow.create_calls == [(create_experiment, (fixture.feature,))]
    assert fixture.workflow.backtest_calls == [run_backtest]
    assert fixture.workflow.validation_calls == [run_validation]
    assert fixture.workflow.comparison_calls == [compare_experiments]
    assert fixture.workflow.card_calls == [generate_research_card]


def test_invoke_dispatches_each_allowlisted_tool_to_its_exact_typed_method() -> None:
    fixture = _fixture()
    requests = _requests(fixture)
    dispatches = (
        (ToolName.SEARCH_DATASETS, requests[0], DatasetSummary),
        (ToolName.SEARCH_EVENTS, requests[1], EventSummary),
        (ToolName.GET_FEATURE, requests[2], FeatureSummary),
        (ToolName.CREATE_EXPERIMENT, requests[3], ExperimentSummary),
        (ToolName.RUN_BACKTEST, requests[4], BacktestRunSummary),
        (ToolName.RUN_VALIDATION, requests[5], ValidationSummary),
        (ToolName.COMPARE_EXPERIMENTS, requests[6], ComparisonSummary),
        (ToolName.GENERATE_RESEARCH_CARD, requests[7], ResearchCardSummary),
        (ToolName.INSPECT_DATASET_QUALITY, requests[8], DatasetQualityReportSummary),
    )

    responses = [
        fixture.api.invoke(tool_name, request)  # type: ignore[arg-type]
        for tool_name, request, _ in dispatches
    ]

    assert isinstance(responses[0].datasets[0], dispatches[0][2])
    assert isinstance(responses[1].events[0], dispatches[1][2])
    assert all(
        type(response) is response_type
        for response, (_, _, response_type) in zip(responses[2:], dispatches[2:], strict=True)
    )


def test_pit_visibility_rejects_future_dataset_event_and_feature_records() -> None:
    future_dataset_fixture = _fixture()
    future_dataset_fixture.dataset_catalog.results = (
        replace(future_dataset_fixture.dataset, available_at=future_dataset_fixture.now + timedelta(seconds=1)),
    )
    with pytest.raises(ToolApiError, match="not available"):
        future_dataset_fixture.api.search_datasets(
            SearchDatasetsRequest(query="futures", as_of=future_dataset_fixture.now)
        )

    future_event_fixture = _fixture()
    future_event_fixture.event_catalog.results = (
        replace(future_event_fixture.event, available_at=future_event_fixture.now + timedelta(seconds=1)),
    )
    with pytest.raises(ToolApiError, match="not available"):
        future_event_fixture.api.search_events(
            SearchEventsRequest(query="supply", as_of=future_event_fixture.now)
        )

    future_feature_fixture = _fixture()
    future_feature_fixture.feature_catalog.features[future_feature_fixture.feature_reference] = replace(
        future_feature_fixture.feature,
        available_at=future_feature_fixture.now + timedelta(seconds=1),
    )
    with pytest.raises(ToolApiError, match="not available"):
        future_feature_fixture.api.get_feature(
            GetFeatureRequest(
                feature=future_feature_fixture.feature_reference,
                as_of=future_feature_fixture.now,
            )
        )


def test_create_experiment_rejects_a_feature_from_another_dataset_before_workflow_mutation() -> None:
    fixture = _fixture()
    fixture.feature_catalog.features[fixture.feature_reference] = replace(
        fixture.feature,
        dataset_version_hashes=(_hash("different-dataset-v1"),),
    )

    with pytest.raises(ToolApiError, match="each feature must bind"):
        fixture.api.create_experiment(_create_experiment_request(fixture))

    assert fixture.workflow.create_calls == []


def test_dispatch_rejects_untyped_tool_names_and_mismatched_request_types() -> None:
    fixture = _fixture()
    dataset_request = SearchDatasetsRequest(query="futures", as_of=fixture.now)
    event_request = SearchEventsRequest(query="supply", as_of=fixture.now)

    with pytest.raises(ToolApiError, match="tool_name must be a ToolName"):
        fixture.api.invoke("search_datasets", dataset_request)  # type: ignore[arg-type]
    with pytest.raises(ToolApiError, match="exactly SearchDatasetsRequest"):
        fixture.api.invoke(ToolName.SEARCH_DATASETS, event_request)  # type: ignore[arg-type]
    with pytest.raises(ToolApiError, match="exactly InspectDatasetQualityRequest"):
        fixture.api.invoke(ToolName.INSPECT_DATASET_QUALITY, dataset_request)  # type: ignore[arg-type]

    assert fixture.dataset_catalog.calls == []
    assert fixture.data_quality_catalog.calls == []
    assert fixture.event_catalog.calls == []


def test_search_request_rejects_secret_like_input_before_a_port_is_called() -> None:
    fixture = _fixture()
    secret_label = "api" + "_key"

    with pytest.raises(ToolApiError, match="credential or secret"):
        SearchDatasetsRequest(
            query=f"{secret_label} = {'x' * 32}",
            as_of=fixture.now,
        )

    assert fixture.dataset_catalog.calls == []


def test_feature_and_research_card_summaries_reject_unsafe_semantics() -> None:
    fixture = _fixture()

    with pytest.raises(ToolApiError, match="must be a FeatureSelectionMode"):
        replace(fixture.feature, selection_mode="UNKNOWN")  # type: ignore[arg-type]
    with pytest.raises(ToolApiError, match="cannot claim decision-time safety"):
        replace(
            fixture.feature,
            selection_mode=FeatureSelectionMode.STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY,
            decision_time_safe=True,
        )


def test_data_quality_summary_requires_all_safe_findings_and_reason_codes() -> None:
    fixture = _fixture()
    report = fixture.data_quality_catalog.report

    with pytest.raises(ToolApiError, match="exactly cover each DataQualityFindingKind once"):
        replace(report, findings=report.findings[:-1])
    with pytest.raises(ToolApiError, match="uppercase stable reason code"):
        replace(report.findings[0], reason_code="contains free prose")
    with pytest.raises(ToolApiError, match="at least 1 hash"):
        replace(report.findings[0], evidence_hashes=())


def test_data_quality_inspection_rejects_future_or_mismatched_audit_reports() -> None:
    fixture = _fixture()
    request = InspectDatasetQualityRequest(dataset=fixture.dataset, as_of=fixture.now)

    fixture.data_quality_catalog.report = replace(
        fixture.data_quality_catalog.report,
        available_at=fixture.now + timedelta(seconds=1),
    )
    with pytest.raises(ToolApiError, match="exactly bound"):
        fixture.api.inspect_dataset_quality(request)

    fixture.data_quality_catalog.report = replace(
        fixture.data_quality_catalog.report,
        available_at=fixture.now - timedelta(minutes=1),
        lineage_hash=_hash("mismatched-lineage"),
    )
    with pytest.raises(ToolApiError, match="exactly bound"):
        fixture.api.inspect_dataset_quality(request)

    assert fixture.data_quality_catalog.calls == [request, request]


def test_data_quality_request_rejects_a_future_dataset_before_the_port_is_called() -> None:
    fixture = _fixture()
    future_dataset = replace(
        fixture.dataset,
        available_at=fixture.now + timedelta(seconds=1),
    )

    with pytest.raises(ToolApiError, match="dataset is not available"):
        InspectDatasetQualityRequest(dataset=future_dataset, as_of=fixture.now)

    assert fixture.data_quality_catalog.calls == []


def test_data_quality_catalog_capability_is_explicit_and_mandatory() -> None:
    fixture = _fixture()

    with pytest.raises(ToolApiError, match="missing a required port method"):
        ResearchToolDependencies(
            dataset_catalog=fixture.dataset_catalog,
            data_quality_catalog=object(),  # type: ignore[arg-type]
            event_catalog=fixture.event_catalog,
            feature_catalog=fixture.feature_catalog,
            research_workflow=fixture.workflow,
        )
    with pytest.raises(ToolApiError, match="only be generated for RESEARCH_ONLY"):
        ResearchCardSummary(
            research_card_request_id="research-card-request-1",
            research_card_id="research-card-1",
            research_card_hash=_hash("research-card"),
            research_decision_id="research-decision-1",
            decision_stage="CANDIDATE",  # type: ignore[arg-type]
            available_at=fixture.now,
        )


def test_event_projection_requires_authorized_citations_and_evidence_bound_findings() -> None:
    fixture = _fixture()
    current_citation = fixture.event.evidence_citations[0]
    historic_citation = EvidenceCitationSummary(
        evidence_hash=_hash("historic-evidence"),
        document_id="historic-document-1",
        source_id="historic-source-1",
        document_content_hash=_hash("historic-document-content-1"),
        span_start=4,
        span_end=19,
        published_at=fixture.now - timedelta(minutes=8),
        available_at=fixture.now - timedelta(minutes=7),
        authorization_status="AUTHORIZED",
    )
    impact = ImpactSummary(
        impact_id="impact-1",
        event_id=fixture.event.event_id,
        ontology_version=fixture.event.ontology_version,
        mechanism_id="supply-reduction",
        commodity_id="CU",
        direction="tightening",
        evidence_hashes=(current_citation.evidence_hash,),
        available_at=fixture.now - timedelta(minutes=4),
    )
    analogue = AnalogueSummary(
        reference_event_id=fixture.event.event_id,
        analogue_event_id="event-historic-1",
        analogue_event_hash=_hash("event-historic-1"),
        analogue_event_time=fixture.now - timedelta(minutes=7),
        matching_method_hash=_hash("structured-analogue-v1"),
        structured_similarity=0.875,
        embedding_similarity=None,
        evidence_citations=(historic_citation,),
        available_at=fixture.now - timedelta(minutes=6),
    )

    projection = replace(
        fixture.event,
        impact_summaries=(impact,),
        analogue_summaries=(analogue,),
    )

    assert projection.eligible_for_trading is False
    assert projection.impact_summaries[0].evidence_hashes == (current_citation.evidence_hash,)
    assert projection.analogue_summaries[0].evidence_citations == (historic_citation,)

    with pytest.raises(ToolApiError, match="trading action"):
        ImpactSummary(
            impact_id="impact-buy",
            event_id=fixture.event.event_id,
            ontology_version=fixture.event.ontology_version,
            mechanism_id="supply-reduction",
            commodity_id="CU",
            direction="buy",
            evidence_hashes=(current_citation.evidence_hash,),
            available_at=fixture.now - timedelta(minutes=4),
        )
    with pytest.raises(ToolApiError, match="parent-event, ontology, PIT, and evidence bound"):
        replace(
            fixture.event,
            impact_summaries=(
                replace(impact, evidence_hashes=(_hash("unbound-impact-evidence"),)),
            ),
        )
    with pytest.raises(ToolApiError, match="historical"):
        replace(
            fixture.event,
            analogue_summaries=(
                replace(
                    analogue,
                    analogue_event_time=fixture.event.event_time,
                    available_at=fixture.event.event_time,
                ),
            ),
        )
    with pytest.raises(ToolApiError, match="authorization_status"):
        EvidenceCitationSummary(
            evidence_hash=_hash("unauthorized-evidence"),
            document_id="document-unauthorized-1",
            source_id="source-unauthorized-1",
            document_content_hash=_hash("document-content-unauthorized-1"),
            span_start=0,
            span_end=8,
            published_at=fixture.now - timedelta(minutes=8),
            available_at=fixture.now - timedelta(minutes=7),
            authorization_status="UNAUTHORIZED",  # type: ignore[arg-type]
        )
