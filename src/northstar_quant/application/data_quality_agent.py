"""Fail-closed, diagnostic-only orchestration for dataset quality findings.

``DataQualityAgent`` has no direct data-platform, persistence, filesystem,
network, configuration, broker, portfolio, or execution capability.  Its only
runtime capability is the closed ``TypedResearchToolApi``.  One invocation
performs exactly two typed, read-only calls: it selects one immutable Dataset
summary and then requests that dataset's already-audited quality report.

The agent never evaluates a raw frame, repairs data, publishes a replacement,
or turns a quality observation into a trading permission.  Unknown evidence is
preserved as an UNKNOWN diagnostic rather than guessed away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
import re
from typing import Literal

from northstar_quant.application.agent_tools import (
    DataQualityFindingKind,
    DataQualityFindingStatus,
    DataQualityFindingSummary,
    DatasetQualityReportSummary,
    DatasetSummary,
    InspectDatasetQualityRequest,
    SearchDatasetsRequest,
    SearchDatasetsResponse,
    ToolName,
    TypedResearchToolApi,
)


__all__ = [
    "DataQualityAgent",
    "DataQualityAgentError",
    "DataQualityAgentRequest",
    "DataQualityAgentResult",
    "DataQualityAgentTraceEntry",
    "DataQualityDatasetFocus",
    "DataQualityDiagnostic",
]


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class DataQualityAgentError(ValueError):
    """Raised when a diagnostic result cannot be proved point-in-time safe."""


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise DataQualityAgentError(f"{field_name} must be a string identifier")
    normalized = value.strip()
    if normalized != value or not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise DataQualityAgentError(f"{field_name} must be a normalized opaque identifier")
    if normalized.casefold() == "latest":
        raise DataQualityAgentError(f"{field_name} cannot use the ambiguous 'latest' selector")
    return normalized


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise DataQualityAgentError(f"{field_name} must be a lowercase SHA-256 hash")
    return value


def _hashes(value: object, field_name: str, *, minimum: int = 1) -> tuple[str, ...]:
    if isinstance(value, str):
        raise DataQualityAgentError(f"{field_name} must be a tuple of hashes")
    try:
        hashes: tuple[object, ...] = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise DataQualityAgentError(f"{field_name} must be an iterable of hashes") from exc
    if len(hashes) < minimum:
        raise DataQualityAgentError(f"{field_name} must contain at least {minimum} hash(es)")
    normalized = tuple(_sha256(item, field_name) for item in hashes)
    if len(set(normalized)) != len(normalized):
        raise DataQualityAgentError(f"{field_name} cannot contain duplicate hashes")
    return normalized


def _as_of(value: object, field_name: str = "as_of") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DataQualityAgentError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _fingerprint(payload: object) -> str:
    return sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class DataQualityDatasetFocus:
    """An exact immutable dataset/report identity selected for diagnosis."""

    dataset_id: str
    dataset_version_hash: str
    schema_hash: str
    lineage_hash: str
    assessment_hash: str

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


@dataclass(frozen=True, slots=True)
class DataQualityAgentRequest:
    """One bounded, read-only diagnostic request at one explicit point in time."""

    run_id: str
    as_of: datetime
    dataset_search: SearchDatasetsRequest
    focus: DataQualityDatasetFocus

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _identifier(self.run_id, "run_id"))
        as_of = _as_of(self.as_of)
        object.__setattr__(self, "as_of", as_of)
        if type(self.dataset_search) is not SearchDatasetsRequest:
            raise DataQualityAgentError("dataset_search must be a SearchDatasetsRequest")
        if self.dataset_search.as_of != as_of:
            raise DataQualityAgentError("dataset_search must use the request's exact as_of")
        if type(self.focus) is not DataQualityDatasetFocus:
            raise DataQualityAgentError("focus must be a DataQualityDatasetFocus")


@dataclass(frozen=True, slots=True)
class DataQualityDiagnostic:
    """One opaque audit fact, never a remediation, publish, or trading instruction."""

    dataset_id: str
    dataset_version_hash: str
    schema_hash: str
    lineage_hash: str
    assessment_hash: str
    lineage_verification_hash: str
    kind: DataQualityFindingKind
    status: DataQualityFindingStatus
    reason_code: str
    finding_hash: str
    evidence_hashes: tuple[str, ...]
    available_at: datetime
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
        object.__setattr__(self, "finding_hash", _sha256(self.finding_hash, "finding_hash"))
        if type(self.kind) is not DataQualityFindingKind:
            raise DataQualityAgentError("kind must be a DataQualityFindingKind")
        if type(self.status) is not DataQualityFindingStatus:
            raise DataQualityAgentError("status must be a DataQualityFindingStatus")
        reason_code = _identifier(self.reason_code, "reason_code")
        if reason_code.upper() != reason_code:
            raise DataQualityAgentError("reason_code must be an uppercase stable identifier")
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "evidence_hashes", _hashes(self.evidence_hashes, "evidence_hashes"))
        object.__setattr__(self, "available_at", _as_of(self.available_at, "available_at"))


@dataclass(frozen=True, slots=True)
class DataQualityAgentTraceEntry:
    """A secret-free, hash-only record of one closed tool invocation."""

    sequence: int
    tool_name: ToolName
    request_hash: str
    response_hash: str
    predecessor_trace_hash: str | None
    trace_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise DataQualityAgentError("trace sequence must be a positive integer")
        if type(self.tool_name) is not ToolName or self.tool_name not in {
            ToolName.SEARCH_DATASETS,
            ToolName.INSPECT_DATASET_QUALITY,
        }:
            raise DataQualityAgentError("data-quality trace contains a forbidden tool")
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
class DataQualityAgentResult:
    """The six opaque diagnostic categories for one selected immutable dataset."""

    run_id: str
    as_of: datetime
    focus: DataQualityDatasetFocus
    diagnostics: tuple[DataQualityDiagnostic, ...]
    trace: tuple[DataQualityAgentTraceEntry, ...]
    lifecycle: Literal["DIAGNOSTIC_ONLY"]
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _identifier(self.run_id, "run_id"))
        object.__setattr__(self, "as_of", _as_of(self.as_of))
        if type(self.focus) is not DataQualityDatasetFocus:
            raise DataQualityAgentError("focus must be a DataQualityDatasetFocus")
        diagnostics = tuple(self.diagnostics)
        if not all(type(item) is DataQualityDiagnostic for item in diagnostics):
            raise DataQualityAgentError("diagnostics must contain DataQualityDiagnostic records")
        if tuple(item.kind for item in diagnostics) != tuple(DataQualityFindingKind):
            raise DataQualityAgentError("diagnostics must contain each quality kind exactly once")
        if any(
            item.dataset_id != self.focus.dataset_id
            or item.dataset_version_hash != self.focus.dataset_version_hash
            or item.schema_hash != self.focus.schema_hash
            or item.lineage_hash != self.focus.lineage_hash
            or item.assessment_hash != self.focus.assessment_hash
            or item.available_at > self.as_of
            for item in diagnostics
        ):
            raise DataQualityAgentError("diagnostics must be visible and exactly bound to the focus")
        trace = tuple(self.trace)
        if not all(type(item) is DataQualityAgentTraceEntry for item in trace):
            raise DataQualityAgentError("trace must contain DataQualityAgentTraceEntry records")
        if len(trace) != 2 or (
            trace[0].sequence,
            trace[0].tool_name,
            trace[0].predecessor_trace_hash,
            trace[1].sequence,
            trace[1].tool_name,
            trace[1].predecessor_trace_hash,
        ) != (
            1,
            ToolName.SEARCH_DATASETS,
            None,
            2,
            ToolName.INSPECT_DATASET_QUALITY,
            trace[0].trace_hash,
        ):
            raise DataQualityAgentError("trace must be the ordered two-step quality inspection")
        if self.lifecycle != "DIAGNOSTIC_ONLY":
            raise DataQualityAgentError("data-quality output must remain DIAGNOSTIC_ONLY")
        object.__setattr__(self, "diagnostics", diagnostics)
        object.__setattr__(self, "trace", trace)


class DataQualityAgent:
    """Select and inspect one immutable dataset without any repair or trading authority."""

    __slots__ = ("_seen_request_hashes", "_seen_run_ids", "_tool_api")

    def __init__(self, tool_api: TypedResearchToolApi) -> None:
        if type(tool_api) is not TypedResearchToolApi:
            raise DataQualityAgentError("tool_api must be a TypedResearchToolApi")
        self._tool_api = tool_api
        self._seen_request_hashes: set[str] = set()
        self._seen_run_ids: set[str] = set()

    def run(self, request: DataQualityAgentRequest) -> DataQualityAgentResult:
        """Return exactly one precomputed diagnostic report through two typed reads.

        There is deliberately no retry, fallback dataset, second inspection, data
        evaluation, or remediation after an error or an unknown quality finding.
        An explicit caller may make a separate read-only request if appropriate.
        """

        if type(request) is not DataQualityAgentRequest:
            raise DataQualityAgentError("request must be a DataQualityAgentRequest")
        request_hash = self._run_request_hash(request)
        if request.run_id in self._seen_run_ids or request_hash in self._seen_request_hashes:
            raise DataQualityAgentError(
                "a data-quality-agent run cannot be retried or replayed automatically"
            )
        # The identity is consumed before the first typed call.  A failed or
        # malformed response cannot prove that an upstream operation had no
        # observable effect, so this agent never replays it automatically.
        self._seen_run_ids.add(request.run_id)
        self._seen_request_hashes.add(request_hash)
        search_response = self._tool_api.invoke(ToolName.SEARCH_DATASETS, request.dataset_search)
        if type(search_response) is not SearchDatasetsResponse:
            raise DataQualityAgentError("search_datasets returned an unexpected response type")
        search_trace = DataQualityAgentTraceEntry(
            sequence=1,
            tool_name=ToolName.SEARCH_DATASETS,
            request_hash=self._search_request_hash(request.dataset_search),
            response_hash=self._search_response_hash(search_response),
            predecessor_trace_hash=None,
        )
        dataset = self._match_dataset(search_response, request.focus)
        inspection_request = InspectDatasetQualityRequest(dataset=dataset, as_of=request.as_of)
        report = self._tool_api.invoke(ToolName.INSPECT_DATASET_QUALITY, inspection_request)
        if type(report) is not DatasetQualityReportSummary:
            raise DataQualityAgentError("inspect_dataset_quality returned an unexpected response type")
        self._assert_report(report=report, focus=request.focus, as_of=request.as_of)
        inspection_trace = DataQualityAgentTraceEntry(
            sequence=2,
            tool_name=ToolName.INSPECT_DATASET_QUALITY,
            request_hash=self._inspection_request_hash(inspection_request),
            response_hash=self._inspection_response_hash(report),
            predecessor_trace_hash=search_trace.trace_hash,
        )
        findings_by_kind = {finding.kind: finding for finding in report.findings}
        diagnostics = tuple(
            self._diagnostic(report=report, finding=findings_by_kind[kind])
            for kind in DataQualityFindingKind
        )
        return DataQualityAgentResult(
            run_id=request.run_id,
            as_of=request.as_of,
            focus=request.focus,
            diagnostics=diagnostics,
            trace=(search_trace, inspection_trace),
            lifecycle="DIAGNOSTIC_ONLY",
        )

    @staticmethod
    def _match_dataset(
        response: SearchDatasetsResponse,
        focus: DataQualityDatasetFocus,
    ) -> DatasetSummary:
        matches = tuple(
            dataset
            for dataset in response.datasets
            if dataset.dataset_id == focus.dataset_id
            and dataset.dataset_version_hash == focus.dataset_version_hash
            and dataset.schema_hash == focus.schema_hash
            and dataset.lineage_hash == focus.lineage_hash
        )
        if len(matches) != 1:
            raise DataQualityAgentError(
                "focused dataset identity is absent, ambiguous, or revised at the requested as_of"
            )
        return matches[0]

    @staticmethod
    def _assert_report(
        *,
        report: DatasetQualityReportSummary,
        focus: DataQualityDatasetFocus,
        as_of: datetime,
    ) -> None:
        if (
            report.dataset_id != focus.dataset_id
            or report.dataset_version_hash != focus.dataset_version_hash
            or report.schema_hash != focus.schema_hash
            or report.lineage_hash != focus.lineage_hash
            or report.assessment_hash != focus.assessment_hash
        ):
            raise DataQualityAgentError("quality report is not exactly bound to the focused dataset")
        if report.authorization_status != "AUTHORIZED":
            raise DataQualityAgentError("quality report authorization is not AUTHORIZED")
        if report.available_at > as_of:
            raise DataQualityAgentError("quality report is not visible at the requested as_of")
        findings = tuple(report.findings)
        if not all(type(item) is DataQualityFindingSummary for item in findings):
            raise DataQualityAgentError("quality report findings have an unexpected type")
        if {item.kind for item in findings} != set(DataQualityFindingKind) or len(findings) != len(
            DataQualityFindingKind
        ):
            raise DataQualityAgentError("quality report must contain each quality kind exactly once")
        if any(item.available_at > as_of for item in findings):
            raise DataQualityAgentError("quality finding is not visible at the requested as_of")
        if any(not item.evidence_hashes for item in findings):
            raise DataQualityAgentError("quality finding must retain immutable evidence hashes")

    @staticmethod
    def _diagnostic(
        *,
        report: DatasetQualityReportSummary,
        finding: DataQualityFindingSummary,
    ) -> DataQualityDiagnostic:
        return DataQualityDiagnostic(
            dataset_id=report.dataset_id,
            dataset_version_hash=report.dataset_version_hash,
            schema_hash=report.schema_hash,
            lineage_hash=report.lineage_hash,
            assessment_hash=report.assessment_hash,
            lineage_verification_hash=report.lineage_verification_hash,
            kind=finding.kind,
            status=finding.status,
            reason_code=finding.reason_code,
            finding_hash=finding.finding_hash,
            evidence_hashes=finding.evidence_hashes,
            available_at=finding.available_at,
        )

    @staticmethod
    def _search_request_hash(request: SearchDatasetsRequest) -> str:
        return _fingerprint(
            {
                "as_of": request.as_of.isoformat(),
                "limit": request.limit,
                "query_hash": sha256(request.query.encode("utf-8")).hexdigest(),
                "tool_name": ToolName.SEARCH_DATASETS.value,
            }
        )

    @staticmethod
    def _search_response_hash(response: SearchDatasetsResponse) -> str:
        return _fingerprint(
            {
                "as_of": response.as_of.isoformat(),
                "datasets": [DataQualityAgent._dataset_payload(item) for item in response.datasets],
                "tool_name": ToolName.SEARCH_DATASETS.value,
            }
        )

    @staticmethod
    def _inspection_request_hash(request: InspectDatasetQualityRequest) -> str:
        return _fingerprint(
            {
                "as_of": request.as_of.isoformat(),
                "dataset": DataQualityAgent._dataset_payload(request.dataset),
                "tool_name": ToolName.INSPECT_DATASET_QUALITY.value,
            }
        )

    @staticmethod
    def _inspection_response_hash(report: DatasetQualityReportSummary) -> str:
        return _fingerprint(
            {
                "assessment_hash": report.assessment_hash,
                "authorization_status": report.authorization_status,
                "available_at": report.available_at.isoformat(),
                "dataset_id": report.dataset_id,
                "dataset_version_hash": report.dataset_version_hash,
                "findings": [
                    {
                        "available_at": finding.available_at.isoformat(),
                        "evidence_hashes": list(finding.evidence_hashes),
                        "finding_hash": finding.finding_hash,
                        "kind": finding.kind.value,
                        "reason_code": finding.reason_code,
                        "status": finding.status.value,
                    }
                    for finding in report.findings
                ],
                "lineage_hash": report.lineage_hash,
                "lineage_verification_hash": report.lineage_verification_hash,
                "schema_hash": report.schema_hash,
                "tool_name": ToolName.INSPECT_DATASET_QUALITY.value,
            }
        )

    @staticmethod
    def _dataset_payload(dataset: DatasetSummary) -> dict[str, object]:
        return {
            "authorization_status": dataset.authorization_status,
            "available_at": dataset.available_at.isoformat(),
            "dataset_id": dataset.dataset_id,
            "dataset_version_hash": dataset.dataset_version_hash,
            "lineage_hash": dataset.lineage_hash,
            "schema_hash": dataset.schema_hash,
        }

    @staticmethod
    def _run_request_hash(request: DataQualityAgentRequest) -> str:
        return _fingerprint(
            {
                "assessment_hash": request.focus.assessment_hash,
                "as_of": request.as_of.isoformat(),
                "dataset_id": request.focus.dataset_id,
                "dataset_query_hash": sha256(request.dataset_search.query.encode("utf-8")).hexdigest(),
                "dataset_version_hash": request.focus.dataset_version_hash,
                "lineage_hash": request.focus.lineage_hash,
                "run_id": request.run_id,
                "schema_hash": request.focus.schema_hash,
            }
        )
