"""Public P7 research-agent contract.

The agent's outward shape stays deliberately small.  Proposal and plan DTOs
remain internal to the typed research-agent module; the only injected runtime
capability is the fail-closed typed research-tool facade.
"""

from __future__ import annotations

import inspect

import northstar_quant.application.research_agent as research_agent_module
from northstar_quant.application.agent_tools import TypedResearchToolApi
from northstar_quant.application.research_agent import (
    ResearchAgent,
    ResearchAgentRequest,
    ResearchAgentResult,
    research_agent_request_hash,
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


def test_research_agent_has_a_single_typed_tool_capability_dependency() -> None:
    signature = inspect.signature(ResearchAgent)
    parameters = tuple(signature.parameters.values())

    assert len(parameters) == 1
    assert parameters[0].annotation in {TypedResearchToolApi, "TypedResearchToolApi"}


def test_research_agent_exposes_only_the_research_run_operation() -> None:
    public_callables = {
        name
        for name, value in inspect.getmembers(ResearchAgent, callable)
        if not name.startswith("_")
    }

    assert public_callables == {"run"}
    assert not public_callables.intersection(FORBIDDEN_PRIVILEGED_OPERATION_NAMES)


def test_research_agent_run_uses_the_public_request_and_result_contracts() -> None:
    signature = inspect.signature(ResearchAgent.run)

    assert tuple(signature.parameters) == ("self", "request")
    assert signature.parameters["request"].annotation in {
        ResearchAgentRequest,
        "ResearchAgentRequest",
    }
    assert signature.return_annotation in {ResearchAgentResult, "ResearchAgentResult"}


def test_research_agent_request_hash_is_a_public_pure_request_commitment() -> None:
    assert "research_agent_request_hash" in research_agent_module.__all__

    signature = inspect.signature(research_agent_request_hash)

    assert tuple(signature.parameters) == ("request",)
    assert signature.parameters["request"].annotation in {
        ResearchAgentRequest,
        "ResearchAgentRequest",
    }
    assert signature.return_annotation in {str, "str"}
