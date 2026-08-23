"""P10-WP06 boundaries for durable, hash-only Research Agent evidence audit.

The durable audit runner is an application composition boundary around the
existing least-privilege ResearchAgent.  It may persist audit evidence through
the Platform database contract, but it must never become an agent tool, a
generic dispatcher, or a trading/control entrypoint.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import fields
import inspect

import northstar_quant.application.research_agent as research_agent
import northstar_quant.application.research_agent_evidence_audit as evidence_audit
from northstar_quant.application.agent_tools import TypedResearchToolApi
from tests.architecture._imports import ImportEdge, PACKAGE_ROOT, runtime_import_edges


MODULE = "northstar_quant.application.research_agent_evidence_audit"
PATH = PACKAGE_ROOT / "application" / "research_agent_evidence_audit.py"
RESEARCH_AGENT_MODULE = "northstar_quant.application.research_agent"
TOOL_API_MODULE = "northstar_quant.application.agent_tools"

_ALLOWED_INTERNAL_IMPORT_PREFIXES = (
    "northstar_quant.application.research_agent",
    "northstar_quant.foundation.common",
    "northstar_quant.foundation.db",
)
_FORBIDDEN_DIRECT_INTERNAL_PREFIXES = (
    "northstar_quant.application.agent_tools",
    "northstar_quant.application.cli",
    "northstar_quant.application.live_service",
    "northstar_quant.application.scheduler",
    "northstar_quant.application.target_service",
    "northstar_quant.data",
    "northstar_quant.intelligence",
    "northstar_quant.portfolio_risk",
    "northstar_quant.research",
    "northstar_quant.trading_execution",
)
_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "aiohttp",
        "argparse",
        "boto3",
        "httpx",
        "os",
        "paramiko",
        "pathlib",
        "psycopg",
        "requests",
        "shutil",
        "socket",
        "sqlite3",
        "subprocess",
        "tempfile",
        "urllib",
        "websockets",
    }
)
_FORBIDDEN_DYNAMIC_OR_CONTROL_CALLS = frozenset(
    {
        "__import__",
        "approve",
        "cancel",
        "cancel_order",
        "create_execution_plan",
        "deploy",
        "enable_live_trading",
        "eval",
        "exec",
        "getattr",
        "import_module",
        "open",
        "recover",
        "restore",
        "resume",
        "route",
        "submit",
        "submit_order",
        "trade",
    }
)
_FORBIDDEN_AUDIT_FIELD_FRAGMENTS = (
    "broker",
    "command",
    "exception",
    "live",
    "order",
    "payload",
    "portfolio",
    "prompt",
    "query",
    "rationale",
    "risk",
    "submit",
    "text",
    "tool",
    "trade",
)
_SAFE_FIELD_NAMES = frozenset({"eligible_for_trading"})


def _has_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def _is_hash_only_field_name(field_name: str) -> bool:
    return field_name.casefold().endswith(("_hash", "_hashes"))


def _is_type_checking_guard(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute)
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
        and test.attr == "TYPE_CHECKING"
    )


class _RuntimeSourceVisitor(ast.NodeVisitor):
    """Collect runtime imports and capability-shaped calls, excluding type-only imports."""

    def __init__(self) -> None:
        self.imports: set[str] = set()
        self.calls: set[str] = set()

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        if _is_type_checking_guard(node.test):
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self.imports.update(alias.name for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.module is not None:
            self.imports.add(node.module)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if isinstance(node.func, ast.Name):
            self.calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            self.calls.add(node.func.attr)
        self.generic_visit(node)


def _source_dependencies() -> _RuntimeSourceVisitor:
    visitor = _RuntimeSourceVisitor()
    visitor.visit(ast.parse(PATH.read_text(encoding="utf-8"), filename=str(PATH)))
    return visitor


def _reachable_edges(module: str, edges: Iterable[ImportEdge]) -> tuple[ImportEdge, ...]:
    by_source: dict[str, list[ImportEdge]] = defaultdict(list)
    for edge in edges:
        by_source[edge.source_module].append(edge)

    pending = [module]
    visited: set[str] = set()
    reachable: list[ImportEdge] = []
    while pending:
        source = pending.pop()
        if source in visited:
            continue
        visited.add(source)
        for edge in by_source.get(source, ()):
            reachable.append(edge)
            if edge.target_module not in visited:
                pending.append(edge.target_module)
    return tuple(reachable)


def test_durable_research_agent_audit_is_an_explicit_application_boundary() -> None:
    assert PATH.is_file(), "P10-WP06 must define application/research_agent_evidence_audit.py"


def test_audit_runner_directly_composes_only_research_agent_and_foundation_db_contracts() -> None:
    visitor = _source_dependencies()
    unexpected_internal = sorted(
        imported
        for imported in visitor.imports
        if imported.startswith("northstar_quant.")
        and not any(_has_prefix(imported, prefix) for prefix in _ALLOWED_INTERNAL_IMPORT_PREFIXES)
    )
    forbidden_internal = sorted(
        imported
        for imported in visitor.imports
        if any(_has_prefix(imported, prefix) for prefix in _FORBIDDEN_DIRECT_INTERNAL_PREFIXES)
    )
    forbidden_imports = sorted(
        imported
        for imported in visitor.imports
        if imported.split(".", maxsplit=1)[0] in _FORBIDDEN_IMPORT_ROOTS
    )
    forbidden_calls = sorted(visitor.calls.intersection(_FORBIDDEN_DYNAMIC_OR_CONTROL_CALLS))

    assert not unexpected_internal, (
        "durable audit may compose only ResearchAgent plus Platform common/database contracts: "
        f"{unexpected_internal}"
    )
    assert not forbidden_internal, (
        "durable audit cannot add Agent tools, CLI, scheduler, live, broker, portfolio/risk, "
        f"or domain capability: {forbidden_internal}"
    )
    assert not forbidden_imports, (
        "durable audit cannot open process, network, filesystem, or alternate database capability: "
        f"{forbidden_imports}"
    )
    assert not forbidden_calls, (
        "durable audit must not dynamically dispatch an agent/tool or control trading/runtime state: "
        f"{forbidden_calls}"
    )


def test_durable_audit_is_not_a_typed_agent_tool_route_and_does_not_inject_the_agent() -> None:
    tool_reachable = _reachable_edges(TOOL_API_MODULE, runtime_import_edges())
    research_agent_source = RESEARCH_AGENT_MODULE.replace(".", "/")
    research_agent_path = PACKAGE_ROOT.parent / f"{research_agent_source}.py"
    research_agent_tree = ast.parse(
        research_agent_path.read_text(encoding="utf-8"),
        filename=str(research_agent_path),
    )
    research_agent_imports = {
        node.module
        for node in ast.walk(research_agent_tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert not any(edge.target_module == MODULE for edge in tool_reachable), (
        "TypedResearchToolApi must not gain durable-audit database or replay capability"
    )
    assert MODULE not in research_agent_imports, (
        "ResearchAgent must remain independent of durable persistence; only the application "
        "audit wrapper may compose it"
    )


def test_durable_audit_preserves_the_original_single_capability_research_agent() -> None:
    signature = inspect.signature(research_agent.ResearchAgent)
    parameters = tuple(signature.parameters.values())

    assert len(parameters) == 1
    assert parameters[0].annotation in {TypedResearchToolApi, "TypedResearchToolApi"}
    assert set(evidence_audit.__all__) == {
        "DurableResearchAgentAuditReceipt",
        "DurableResearchAgentResult",
        "DurableResearchAgentRunner",
        "ResearchAgentEvidenceAuditError",
    }


def test_durable_audit_public_records_have_no_raw_or_control_persistence_surface() -> None:
    records = (
        evidence_audit.DurableResearchAgentAuditReceipt,
        evidence_audit.DurableResearchAgentResult,
    )
    violations = {
        record.__name__: sorted(
            field.name
            for field in fields(record)
            if field.name not in _SAFE_FIELD_NAMES
            and not _is_hash_only_field_name(field.name)
            and any(fragment in field.name.casefold() for fragment in _FORBIDDEN_AUDIT_FIELD_FRAGMENTS)
        )
        for record in records
    }
    violations = {name: names for name, names in violations.items() if names}

    assert not violations, (
        "durable audit records may retain only hash/identifier/time evidence, never raw "
        "prompt/query/text/rationale/exception payloads or trading control fields: "
        f"{violations}"
    )
