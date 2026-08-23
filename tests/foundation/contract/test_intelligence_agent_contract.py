"""Public P7 intelligence-agent contract.

The agent has one narrow, read-only runtime capability: the existing closed
research-tool facade.  All intelligence request/result DTOs stay local to the
agent module rather than becoming a second public tool surface.
"""

from __future__ import annotations

import inspect

from northstar_quant.application.agent_tools import TypedResearchToolApi
from northstar_quant.application.intelligence_agent import (
    IntelligenceAgent,
    IntelligenceAgentRequest,
    IntelligenceAgentResult,
)


FORBIDDEN_PRIVILEGED_OPERATION_NAMES = frozenset(
    {
        "approve",
        "approve_production",
        "create_execution_plan",
        "enable_live_trading",
        "resume_risk",
        "submit_order",
    }
)


def test_intelligence_agent_has_a_single_typed_tool_capability_dependency() -> None:
    signature = inspect.signature(IntelligenceAgent)
    parameters = tuple(signature.parameters.values())

    assert len(parameters) == 1
    assert parameters[0].annotation in {TypedResearchToolApi, "TypedResearchToolApi"}


def test_intelligence_agent_exposes_only_the_intelligence_run_operation() -> None:
    public_callables = {
        name
        for name, value in inspect.getmembers(IntelligenceAgent, callable)
        if not name.startswith("_")
    }

    assert public_callables == {"run"}
    assert not public_callables.intersection(FORBIDDEN_PRIVILEGED_OPERATION_NAMES)


def test_intelligence_agent_run_uses_the_public_request_and_result_contracts() -> None:
    signature = inspect.signature(IntelligenceAgent.run)

    assert tuple(signature.parameters) == ("self", "request")
    assert signature.parameters["request"].annotation in {
        IntelligenceAgentRequest,
        "IntelligenceAgentRequest",
    }
    assert signature.return_annotation in {IntelligenceAgentResult, "IntelligenceAgentResult"}
