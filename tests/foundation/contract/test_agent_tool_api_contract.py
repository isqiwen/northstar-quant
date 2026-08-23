"""Public P7 Typed Tool API contract.

This intentionally verifies the narrow, stable tool vocabulary before the
research-agent work packages add higher-level behavior.  It does not prescribe
the dependency-injection or request/response implementation details.
"""

from __future__ import annotations

from enum import Enum
import inspect

from northstar_quant.application.agent_tools import ToolName, TypedResearchToolApi


EXPECTED_TOOL_NAMES = frozenset(
    {
        "search_datasets",
        "inspect_dataset_quality",
        "search_events",
        "get_feature",
        "create_experiment",
        "run_backtest",
        "run_validation",
        "compare_experiments",
        "generate_research_card",
    }
)
FORBIDDEN_PRIVILEGED_TOOL_NAMES = frozenset(
    {
        "approve_production",
        "create_execution_plan",
        "enable_live_trading",
        "resume_risk",
        "submit_order",
    }
)


def test_tool_name_is_a_closed_string_enum_with_the_master_plan_allowlist() -> None:
    assert issubclass(ToolName, str)
    assert issubclass(ToolName, Enum)
    assert {member.value for member in ToolName} == EXPECTED_TOOL_NAMES
    assert len(ToolName.__members__) == len(EXPECTED_TOOL_NAMES)
    assert len(ToolName) == len(EXPECTED_TOOL_NAMES)


def test_typed_research_tool_api_dispatch_accepts_only_tool_name_enum() -> None:
    invoke = TypedResearchToolApi.invoke
    signature = inspect.signature(invoke)

    assert tuple(signature.parameters)[:3] == ("self", "tool_name", "request")
    assert signature.parameters["tool_name"].annotation in {ToolName, "ToolName"}


def test_typed_research_tool_api_has_no_privileged_public_operation() -> None:
    public_callables = {
        name
        for name, value in inspect.getmembers(TypedResearchToolApi, callable)
        if not name.startswith("_")
    }

    assert "invoke" in public_callables
    assert public_callables.difference({"invoke"}) == EXPECTED_TOOL_NAMES
    assert not public_callables.intersection(FORBIDDEN_PRIVILEGED_TOOL_NAMES)
