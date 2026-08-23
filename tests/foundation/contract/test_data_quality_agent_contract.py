"""Public P7 data-quality-agent contract.

The agent's public runtime surface remains a single injected, read-only typed
tool facade.  Its result is an explicitly non-tradable diagnostic artifact;
all quality inspection, authorization, and point-in-time checks stay inside
the closed tool boundary and the agent's immutable DTOs.
"""

from __future__ import annotations

import inspect
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from typing import Literal, get_args, get_origin, get_type_hints

import pytest

from northstar_quant.application.agent_tools import (
    DataQualityFindingKind,
    DataQualityFindingStatus,
    ToolName,
    TypedResearchToolApi,
)
from northstar_quant.application.data_quality_agent import (
    DataQualityAgent,
    DataQualityAgentError,
    DataQualityAgentRequest,
    DataQualityAgentResult,
    DataQualityAgentTraceEntry,
    DataQualityDatasetFocus,
    DataQualityDiagnostic,
)


FORBIDDEN_PRIVILEGED_OPERATION_NAMES = frozenset(
    {
        "approve",
        "approve_production",
        "broker",
        "cancel_order",
        "create_execution_plan",
        "create_target",
        "delete",
        "enable_live_trading",
        "publish",
        "publish_dataset",
        "repair",
        "repair_dataset",
        "resume_risk",
        "submit_order",
        "trade",
        "trading",
        "write",
    }
)
_HASH = "a" * 64
_AS_OF = datetime(2026, 8, 23, tzinfo=UTC)


def test_data_quality_agent_has_a_single_typed_tool_capability_dependency() -> None:
    signature = inspect.signature(DataQualityAgent)
    parameters = tuple(signature.parameters.values())

    assert len(parameters) == 1
    assert parameters[0].annotation in {TypedResearchToolApi, "TypedResearchToolApi"}


def test_data_quality_agent_exposes_only_the_diagnostic_run_operation() -> None:
    public_callables = {
        name
        for name, value in inspect.getmembers(DataQualityAgent, callable)
        if not name.startswith("_")
    }

    assert public_callables == {"run"}
    assert not public_callables.intersection(FORBIDDEN_PRIVILEGED_OPERATION_NAMES)


def test_data_quality_agent_run_uses_the_public_request_and_result_contracts() -> None:
    signature = inspect.signature(DataQualityAgent.run)

    assert tuple(signature.parameters) == ("self", "request")
    assert signature.parameters["request"].annotation in {
        DataQualityAgentRequest,
        "DataQualityAgentRequest",
    }
    assert signature.return_annotation in {DataQualityAgentResult, "DataQualityAgentResult"}


def test_data_quality_agent_dtos_are_immutable_slotted_public_value_contracts() -> None:
    for dto in (
        DataQualityDatasetFocus,
        DataQualityAgentRequest,
        DataQualityAgentTraceEntry,
        DataQualityAgentResult,
    ):
        assert is_dataclass(dto)
        assert dto.__dataclass_params__.frozen is True
        assert hasattr(dto, "__slots__")


def test_data_quality_agent_has_exactly_the_closed_two_tool_trace_vocabulary() -> None:
    trace_hints = get_type_hints(DataQualityAgentTraceEntry)

    assert trace_hints["tool_name"] is ToolName
    assert ToolName.SEARCH_DATASETS.value == "search_datasets"
    assert ToolName.INSPECT_DATASET_QUALITY.value == "inspect_dataset_quality"


def test_data_quality_agent_result_is_diagnostic_only_non_tradable_and_traced() -> None:
    result_hints = get_type_hints(DataQualityAgentResult)
    result_fields = {field.name: field for field in fields(DataQualityAgentResult)}

    assert get_origin(result_hints["trace"]) is tuple
    assert get_args(result_hints["trace"]) == (DataQualityAgentTraceEntry, Ellipsis)
    assert get_origin(result_hints["lifecycle"]) is Literal
    assert get_args(result_hints["lifecycle"]) == ("DIAGNOSTIC_ONLY",)
    assert get_origin(result_hints["eligible_for_trading"]) is Literal
    assert get_args(result_hints["eligible_for_trading"]) == (False,)
    assert result_fields["eligible_for_trading"].default is False
    assert result_fields["eligible_for_trading"].init is False


def _focus() -> DataQualityDatasetFocus:
    return DataQualityDatasetFocus(
        dataset_id="dataset.copper.daily",
        dataset_version_hash=_HASH,
        schema_hash=_HASH,
        lineage_hash=_HASH,
        assessment_hash=_HASH,
    )


def _diagnostics() -> tuple[DataQualityDiagnostic, ...]:
    return tuple(
        DataQualityDiagnostic(
            dataset_id="dataset.copper.daily",
            dataset_version_hash=_HASH,
            schema_hash=_HASH,
            lineage_hash=_HASH,
            assessment_hash=_HASH,
            lineage_verification_hash=_HASH,
            kind=kind,
            status=DataQualityFindingStatus.NOT_DETECTED,
            reason_code="NO_ISSUE",
            finding_hash=_HASH,
            evidence_hashes=(_HASH,),
            available_at=_AS_OF,
        )
        for kind in DataQualityFindingKind
    )


def _result(trace: tuple[DataQualityAgentTraceEntry, ...]) -> DataQualityAgentResult:
    return DataQualityAgentResult(
        run_id="quality.run.1",
        as_of=_AS_OF,
        focus=_focus(),
        diagnostics=_diagnostics(),
        trace=trace,
        lifecycle="DIAGNOSTIC_ONLY",
    )


def test_data_quality_agent_result_requires_the_exact_ordered_two_step_trace() -> None:
    search_trace = DataQualityAgentTraceEntry(
        sequence=1,
        tool_name=ToolName.SEARCH_DATASETS,
        request_hash=_HASH,
        response_hash=_HASH,
        predecessor_trace_hash=None,
    )
    inspection_trace = DataQualityAgentTraceEntry(
        sequence=2,
        tool_name=ToolName.INSPECT_DATASET_QUALITY,
        request_hash=_HASH,
        response_hash=_HASH,
        predecessor_trace_hash=search_trace.trace_hash,
    )

    result = _result((search_trace, inspection_trace))

    assert result.trace == (search_trace, inspection_trace)
    assert result.lifecycle == "DIAGNOSTIC_ONLY"
    assert result.eligible_for_trading is False
    with pytest.raises(DataQualityAgentError, match="ordered two-step quality inspection"):
        _result((search_trace,))
