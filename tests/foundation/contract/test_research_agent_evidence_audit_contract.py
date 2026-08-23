"""Public P10-WP06 contract for durable, hash-only Research Agent audit."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
import inspect
from typing import Literal, get_args, get_origin, get_type_hints

from sqlalchemy.orm import Session, sessionmaker

import northstar_quant.application.research_agent_evidence_audit as evidence_audit
from northstar_quant.application.research_agent import (
    ResearchAgent,
    ResearchAgentRequest,
    ResearchAgentResult,
    ResearchAgentTraceEntry,
)


_RAW_AUDIT_FIELD_FRAGMENTS = (
    "detail",
    "exception",
    "message",
    "payload",
    "prompt",
    "query",
    "rationale",
    "reason",
    "statement",
    "text",
)
_CONTROL_AUDIT_FIELD_FRAGMENTS = (
    "broker",
    "command",
    "live",
    "order",
    "portfolio",
    "risk",
    "submit",
    "tool",
    "trade",
)
_SAFE_FIELD_NAMES = frozenset({"eligible_for_trading"})


def _public_methods(type_: type[object]) -> set[str]:
    return {
        name
        for name, value in inspect.getmembers(type_, predicate=inspect.isfunction)
        if not name.startswith("_")
    }


def _field_names(type_: type[object]) -> tuple[str, ...]:
    return tuple(field.name for field in fields(type_))


def _is_hash_only_field_name(field_name: str) -> bool:
    return field_name.casefold().endswith(("_hash", "_hashes"))


def test_durable_audit_has_exactly_the_small_declared_public_api() -> None:
    assert set(evidence_audit.__all__) == {
        "DurableResearchAgentAuditReceipt",
        "DurableResearchAgentResult",
        "DurableResearchAgentRunner",
        "ResearchAgentEvidenceAuditError",
    }
    assert issubclass(evidence_audit.ResearchAgentEvidenceAuditError, Exception)

    signature = inspect.signature(evidence_audit.DurableResearchAgentRunner)
    constructor_hints = get_type_hints(evidence_audit.DurableResearchAgentRunner.__init__)

    assert tuple(signature.parameters) == ("agent", "session_factory")
    assert signature.parameters["agent"].default is inspect.Parameter.empty
    assert signature.parameters["session_factory"].default is inspect.Parameter.empty
    assert constructor_hints["agent"] is ResearchAgent
    assert get_origin(constructor_hints["session_factory"]) is sessionmaker
    assert get_args(constructor_hints["session_factory"]) == (Session,)
    assert _public_methods(evidence_audit.DurableResearchAgentRunner) == {"run"}

    run_signature = inspect.signature(evidence_audit.DurableResearchAgentRunner.run)
    run_hints = get_type_hints(evidence_audit.DurableResearchAgentRunner.run)
    assert tuple(run_signature.parameters) == ("self", "request")
    assert run_hints["request"] is ResearchAgentRequest
    assert run_hints["return"] is evidence_audit.DurableResearchAgentResult


def test_durable_audit_result_is_frozen_and_binds_only_in_memory_result_and_receipt() -> None:
    result_type = evidence_audit.DurableResearchAgentResult

    assert is_dataclass(result_type)
    assert result_type.__dataclass_params__.frozen is True
    assert hasattr(result_type, "__slots__")

    result_hints = get_type_hints(result_type)
    result_types = tuple(result_hints.values())
    assert result_types.count(ResearchAgentResult) == 1
    assert result_types.count(evidence_audit.DurableResearchAgentAuditReceipt) == 1
    assert len(_field_names(result_type)) == 2


def test_durable_audit_receipt_is_frozen_hash_only_and_never_embeds_raw_agent_objects() -> None:
    receipt_type = evidence_audit.DurableResearchAgentAuditReceipt

    assert is_dataclass(receipt_type)
    assert receipt_type.__dataclass_params__.frozen is True
    assert hasattr(receipt_type, "__slots__")

    receipt_hints = get_type_hints(receipt_type)
    forbidden_values = {
        ResearchAgent,
        ResearchAgentRequest,
        ResearchAgentResult,
        ResearchAgentTraceEntry,
    }
    embedded = {
        name: annotation
        for name, annotation in receipt_hints.items()
        if annotation in forbidden_values
    }
    forbidden_fields = sorted(
        field_name
        for field_name in _field_names(receipt_type)
        if field_name not in _SAFE_FIELD_NAMES
        and not _is_hash_only_field_name(field_name)
        and any(
            fragment in field_name.casefold()
            for fragment in _RAW_AUDIT_FIELD_FRAGMENTS + _CONTROL_AUDIT_FIELD_FRAGMENTS
        )
    )

    assert not embedded, (
        "hash-only receipt must not retain a raw ResearchAgent request/result/trace object: "
        f"{embedded}"
    )
    assert not forbidden_fields, (
        "hash-only receipt must not expose raw prompt/query/text/rationale/exception payload or "
        f"trading control fields: {forbidden_fields}"
    )

    if "eligible_for_trading" in receipt_hints:
        assert get_origin(receipt_hints["eligible_for_trading"]) is Literal
        assert get_args(receipt_hints["eligible_for_trading"]) == (False,)


def test_durable_audit_module_exposes_no_generic_agent_or_tool_operation() -> None:
    module_functions = {
        name
        for name, value in vars(evidence_audit).items()
        if inspect.isfunction(value)
        and value.__module__ == evidence_audit.__name__
        and not name.startswith("_")
    }

    assert module_functions == set(), (
        "durable audit must expose only its typed runner, not a generic agent/tool dispatch function: "
        f"{sorted(module_functions)}"
    )
