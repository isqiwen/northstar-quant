"""Unit coverage for the fail-closed, diagnostic-only data-quality agent."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from northstar_quant.application.agent_tools import (
    DataQualityFindingKind,
    DataQualityFindingStatus,
    DataQualityFindingSummary,
    DatasetQualityReportSummary,
    DatasetSummary,
    FeatureReference,
    FeatureSummary,
    InspectDatasetQualityRequest,
    ResearchToolDependencies,
    SearchDatasetsRequest,
    SearchDatasetsResponse,
    ToolApiError,
    ToolName,
    TypedResearchToolApi,
)
from northstar_quant.application.data_quality_agent import (
    DataQualityAgent,
    DataQualityAgentError,
    DataQualityAgentRequest,
    DataQualityDatasetFocus,
)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


@dataclass
class FakeDatasetCatalog:
    results: tuple[DatasetSummary, ...]
    fail: bool = False
    calls: list[tuple[str, datetime, int]] = field(default_factory=list)

    def search_datasets(
        self,
        *,
        query: str,
        as_of: datetime,
        limit: int,
    ) -> tuple[DatasetSummary, ...]:
        self.calls.append((query, as_of, limit))
        if self.fail:
            raise ToolApiError("simulated dataset-catalog failure")
        return self.results


@dataclass
class FakeDataQualityCatalog:
    report: DatasetQualityReportSummary
    fail: bool = False
    calls: list[InspectDatasetQualityRequest] = field(default_factory=list)

    def inspect_dataset_quality(
        self,
        *,
        request: InspectDatasetQualityRequest,
    ) -> DatasetQualityReportSummary:
        self.calls.append(request)
        if self.fail:
            raise ToolApiError("simulated data-quality-catalog failure")
        return self.report


@dataclass
class FailingIfCalledEventCatalog:
    calls: list[tuple[str, datetime, int]] = field(default_factory=list)

    def search_events(
        self,
        *,
        query: str,
        as_of: datetime,
        limit: int,
    ) -> tuple[object, ...]:
        self.calls.append((query, as_of, limit))
        raise AssertionError("DataQualityAgent must not search events")


@dataclass
class FailingIfCalledFeatureCatalog:
    calls: list[tuple[FeatureReference, datetime]] = field(default_factory=list)

    def get_feature(self, *, feature: FeatureReference, as_of: datetime) -> FeatureSummary:
        self.calls.append((feature, as_of))
        raise AssertionError("DataQualityAgent must not retrieve a feature")


@dataclass
class FailingIfCalledWorkflow:
    calls: list[str] = field(default_factory=list)

    def create_experiment(self, **_: object) -> object:
        self.calls.append("create_experiment")
        raise AssertionError("DataQualityAgent must not create an experiment")

    def run_backtest(self, **_: object) -> object:
        self.calls.append("run_backtest")
        raise AssertionError("DataQualityAgent must not run a backtest")

    def run_validation(self, **_: object) -> object:
        self.calls.append("run_validation")
        raise AssertionError("DataQualityAgent must not run validation")

    def compare_experiments(self, **_: object) -> object:
        self.calls.append("compare_experiments")
        raise AssertionError("DataQualityAgent must not compare experiments")

    def generate_research_card(self, **_: object) -> object:
        self.calls.append("generate_research_card")
        raise AssertionError("DataQualityAgent must not generate a research card")


@dataclass
class AgentFixture:
    agent: DataQualityAgent
    api: TypedResearchToolApi
    request: DataQualityAgentRequest
    now: datetime
    dataset: DatasetSummary
    report: DatasetQualityReportSummary
    dataset_catalog: FakeDatasetCatalog
    data_quality_catalog: FakeDataQualityCatalog
    event_catalog: FailingIfCalledEventCatalog
    feature_catalog: FailingIfCalledFeatureCatalog
    workflow: FailingIfCalledWorkflow


def _fixture() -> AgentFixture:
    now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    dataset = DatasetSummary(
        dataset_id="dataset.copper.daily",
        dataset_version_hash=_hash("dataset-copper-daily-v1"),
        available_at=now - timedelta(minutes=30),
        schema_hash=_hash("dataset-copper-schema-v1"),
        lineage_hash=_hash("dataset-copper-lineage-v1"),
        authorization_status="AUTHORIZED",
    )
    findings = tuple(
        DataQualityFindingSummary(
            kind=kind,
            status=DataQualityFindingStatus.NOT_DETECTED,
            reason_code=f"{kind.name}_NOT_DETECTED",
            finding_hash=_hash(f"finding-{kind.value}-v1"),
            evidence_hashes=(_hash(f"evidence-{kind.value}-v1"),),
            available_at=now - timedelta(minutes=10),
        )
        for kind in DataQualityFindingKind
    )
    report = DatasetQualityReportSummary(
        dataset_id=dataset.dataset_id,
        dataset_version_hash=dataset.dataset_version_hash,
        schema_hash=dataset.schema_hash,
        lineage_hash=dataset.lineage_hash,
        assessment_hash=_hash("dataset-copper-quality-assessment-v1"),
        lineage_verification_hash=_hash("dataset-copper-lineage-verification-v1"),
        findings=findings,
        available_at=now - timedelta(minutes=5),
        authorization_status="AUTHORIZED",
    )
    dataset_catalog = FakeDatasetCatalog(results=(dataset,))
    data_quality_catalog = FakeDataQualityCatalog(report=report)
    event_catalog = FailingIfCalledEventCatalog()
    feature_catalog = FailingIfCalledFeatureCatalog()
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
    request = DataQualityAgentRequest(
        run_id="data-quality-agent-run-1",
        as_of=now,
        dataset_search=SearchDatasetsRequest(
            query="copper futures daily quality audit",
            as_of=now,
            limit=3,
        ),
        focus=DataQualityDatasetFocus(
            dataset_id=dataset.dataset_id,
            dataset_version_hash=dataset.dataset_version_hash,
            schema_hash=dataset.schema_hash,
            lineage_hash=dataset.lineage_hash,
            assessment_hash=report.assessment_hash,
        ),
    )
    return AgentFixture(
        agent=DataQualityAgent(api),
        api=api,
        request=request,
        now=now,
        dataset=dataset,
        report=report,
        dataset_catalog=dataset_catalog,
        data_quality_catalog=data_quality_catalog,
        event_catalog=event_catalog,
        feature_catalog=feature_catalog,
        workflow=workflow,
    )


def _assert_no_other_capabilities(fixture: AgentFixture) -> None:
    assert fixture.event_catalog.calls == []
    assert fixture.feature_catalog.calls == []
    assert fixture.workflow.calls == []


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _replace_finding(
    report: DatasetQualityReportSummary,
    *,
    kind: DataQualityFindingKind,
    status: DataQualityFindingStatus,
) -> DatasetQualityReportSummary:
    return replace(
        report,
        findings=tuple(
            replace(
                finding,
                status=status,
                reason_code=f"{kind.name}_{status.name}",
            )
            if finding.kind is kind
            else finding
            for finding in report.findings
        ),
    )


def test_data_quality_agent_returns_six_opaque_diagnostics_from_the_ordered_two_read_chain() -> None:
    fixture = _fixture()

    result = fixture.agent.run(fixture.request)

    assert result.lifecycle == "DIAGNOSTIC_ONLY"
    assert result.eligible_for_trading is False
    assert result.focus == fixture.request.focus
    assert len(result.diagnostics) == len(DataQualityFindingKind)
    assert tuple(diagnostic.kind for diagnostic in result.diagnostics) == tuple(DataQualityFindingKind)
    findings_by_kind = {finding.kind: finding for finding in fixture.report.findings}
    for diagnostic in result.diagnostics:
        finding = findings_by_kind[diagnostic.kind]
        assert (
            diagnostic.dataset_id,
            diagnostic.dataset_version_hash,
            diagnostic.schema_hash,
            diagnostic.lineage_hash,
            diagnostic.assessment_hash,
            diagnostic.lineage_verification_hash,
            diagnostic.status,
            diagnostic.reason_code,
            diagnostic.finding_hash,
            diagnostic.evidence_hashes,
            diagnostic.available_at,
            diagnostic.eligible_for_trading,
        ) == (
            fixture.dataset.dataset_id,
            fixture.dataset.dataset_version_hash,
            fixture.dataset.schema_hash,
            fixture.dataset.lineage_hash,
            fixture.report.assessment_hash,
            fixture.report.lineage_verification_hash,
            finding.status,
            finding.reason_code,
            finding.finding_hash,
            finding.evidence_hashes,
            finding.available_at,
            False,
        )
    assert [entry.tool_name for entry in result.trace] == [
        ToolName.SEARCH_DATASETS,
        ToolName.INSPECT_DATASET_QUALITY,
    ]
    assert [entry.sequence for entry in result.trace] == [1, 2]
    assert result.trace[0].predecessor_trace_hash is None
    assert result.trace[1].predecessor_trace_hash == result.trace[0].trace_hash
    assert all(
        _is_sha256(value)
        for entry in result.trace
        for value in (entry.request_hash, entry.response_hash, entry.trace_hash)
    )
    assert fixture.request.dataset_search.query not in repr(result.trace)
    with pytest.raises(FrozenInstanceError):
        result.trace[0].sequence = 99  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.trace += ()  # type: ignore[misc]
    assert fixture.dataset_catalog.calls == [
        (
            fixture.request.dataset_search.query,
            fixture.now,
            fixture.request.dataset_search.limit,
        )
    ]
    assert fixture.data_quality_catalog.calls == [
        InspectDatasetQualityRequest(dataset=fixture.dataset, as_of=fixture.now)
    ]
    _assert_no_other_capabilities(fixture)


@pytest.mark.parametrize(
    "status",
    (DataQualityFindingStatus.DETECTED, DataQualityFindingStatus.UNKNOWN),
)
def test_data_quality_agent_preserves_detected_and_unknown_states_without_trading_authority(
    status: DataQualityFindingStatus,
) -> None:
    fixture = _fixture()
    target_kind = DataQualityFindingKind.ANOMALY
    fixture.data_quality_catalog.report = _replace_finding(
        fixture.report,
        kind=target_kind,
        status=status,
    )

    result = fixture.agent.run(fixture.request)

    diagnostic = next(item for item in result.diagnostics if item.kind is target_kind)
    assert diagnostic.status is status
    assert diagnostic.eligible_for_trading is False
    assert result.lifecycle == "DIAGNOSTIC_ONLY"
    assert result.eligible_for_trading is False
    assert len(fixture.dataset_catalog.calls) == 1
    assert len(fixture.data_quality_catalog.calls) == 1
    _assert_no_other_capabilities(fixture)


@pytest.mark.parametrize(
    "field_name",
    (
        "dataset_id",
        "dataset_version_hash",
        "schema_hash",
        "lineage_hash",
        "assessment_hash",
    ),
)
def test_data_quality_agent_rejects_every_mismatched_focus_component(
    field_name: str,
) -> None:
    fixture = _fixture()
    value = "dataset.copper.other" if field_name == "dataset_id" else _hash(f"other-{field_name}")
    request = replace(
        fixture.request,
        focus=replace(fixture.request.focus, **{field_name: value}),
    )

    with pytest.raises(
        DataQualityAgentError,
        match="focused dataset identity|quality report is not exactly bound",
    ):
        fixture.agent.run(request)

    assert len(fixture.dataset_catalog.calls) == 1
    assert len(fixture.data_quality_catalog.calls) == (1 if field_name == "assessment_hash" else 0)
    _assert_no_other_capabilities(fixture)


def test_data_quality_agent_fails_closed_when_a_quality_report_is_future_visible() -> None:
    fixture = _fixture()
    fixture.data_quality_catalog.report = replace(
        fixture.report,
        available_at=fixture.now + timedelta(seconds=1),
    )

    with pytest.raises(ToolApiError, match="not exactly bound"):
        fixture.agent.run(fixture.request)

    assert len(fixture.dataset_catalog.calls) == 1
    assert len(fixture.data_quality_catalog.calls) == 1
    _assert_no_other_capabilities(fixture)


def test_quality_finding_rejects_missing_evidence_at_the_typed_boundary() -> None:
    fixture = _fixture()

    with pytest.raises(ToolApiError, match="at least 1 hash"):
        replace(fixture.report.findings[0], evidence_hashes=())

    assert fixture.dataset_catalog.calls == []
    assert fixture.data_quality_catalog.calls == []
    _assert_no_other_capabilities(fixture)


def test_data_quality_agent_rejects_a_malformed_missing_evidence_response_without_remediation() -> None:
    fixture = _fixture()
    object.__setattr__(fixture.report.findings[0], "evidence_hashes", ())

    with pytest.raises(DataQualityAgentError, match="immutable evidence"):
        fixture.agent.run(fixture.request)

    assert len(fixture.dataset_catalog.calls) == 1
    assert len(fixture.data_quality_catalog.calls) == 1
    _assert_no_other_capabilities(fixture)


def test_data_quality_agent_rejects_an_unexpected_inspection_response_without_another_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture()
    original_invoke = TypedResearchToolApi.invoke

    def unexpected_response(
        self: TypedResearchToolApi,
        tool_name: ToolName,
        request: object,
    ) -> object:
        if tool_name is ToolName.INSPECT_DATASET_QUALITY:
            assert type(request) is InspectDatasetQualityRequest
            return SearchDatasetsResponse(as_of=fixture.now, datasets=(fixture.dataset,))
        return original_invoke(self, tool_name, request)  # type: ignore[arg-type]

    monkeypatch.setattr(TypedResearchToolApi, "invoke", unexpected_response)

    with pytest.raises(DataQualityAgentError, match="unexpected response type"):
        fixture.agent.run(fixture.request)

    assert len(fixture.dataset_catalog.calls) == 1
    assert fixture.data_quality_catalog.calls == []
    _assert_no_other_capabilities(fixture)


def test_data_quality_agent_stops_after_quality_port_failure_without_later_capabilities() -> None:
    fixture = _fixture()
    fixture.data_quality_catalog.fail = True

    with pytest.raises(ToolApiError, match="simulated data-quality-catalog failure"):
        fixture.agent.run(fixture.request)

    assert len(fixture.dataset_catalog.calls) == 1
    assert len(fixture.data_quality_catalog.calls) == 1
    _assert_no_other_capabilities(fixture)


def test_failed_run_is_consumed_before_the_first_tool_call_and_cannot_be_retried() -> None:
    fixture = _fixture()
    fixture.dataset_catalog.fail = True

    with pytest.raises(ToolApiError, match="simulated dataset-catalog failure"):
        fixture.agent.run(fixture.request)
    with pytest.raises(DataQualityAgentError, match="cannot be retried or replayed"):
        fixture.agent.run(fixture.request)

    assert len(fixture.dataset_catalog.calls) == 1
    assert fixture.data_quality_catalog.calls == []
    _assert_no_other_capabilities(fixture)
