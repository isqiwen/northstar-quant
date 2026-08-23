"""P7 intelligence-agent least-privilege architecture contracts.

The intelligence agent is a read-only consumer of the closed
``TypedResearchToolApi``.  It must not acquire a parallel intelligence facade,
direct domain access, privileged runtime composition, storage, configuration,
or external capabilities while preparing evidence-linked research findings.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterable

from tests.architecture._imports import ImportEdge, PACKAGE_ROOT, format_diagnostics, runtime_import_edges


INTELLIGENCE_AGENT_MODULE = "northstar_quant.application.intelligence_agent"
INTELLIGENCE_AGENT_PATH = PACKAGE_ROOT / "application" / "intelligence_agent.py"
TOOL_API_MODULE = "northstar_quant.application.agent_tools"

_FORBIDDEN_EXTERNAL_IMPORT_ROOTS = frozenset(
    {
        "aiohttp",
        "anthropic",
        "boto3",
        "dotenv",
        "httpx",
        "openai",
        "os",
        "paramiko",
        "pathlib",
        "psycopg",
        "requests",
        "shutil",
        "socket",
        "sqlalchemy",
        "sqlite3",
        "subprocess",
        "tempfile",
        "urllib",
        "websockets",
    }
)
_FORBIDDEN_REFLECTIVE_CALLS = frozenset(
    {
        "__import__",
        "eval",
        "exec",
        "getattr",
        "import_module",
        "open",
        "setattr",
    }
)
_DIRECT_TOOL_METHOD_NAMES = frozenset(
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


def _reachable_edges(edges: Iterable[ImportEdge]) -> tuple[ImportEdge, ...]:
    """Return the runtime import closure starting at the intelligence agent."""

    by_source: dict[str, list[ImportEdge]] = defaultdict(list)
    for edge in edges:
        by_source[edge.source_module].append(edge)

    pending = [INTELLIGENCE_AGENT_MODULE]
    visited_modules: set[str] = set()
    reachable: list[ImportEdge] = []
    while pending:
        source = pending.pop()
        if source in visited_modules:
            continue
        visited_modules.add(source)
        for edge in by_source.get(source, ()):
            reachable.append(edge)
            if edge.target_module not in visited_modules:
                pending.append(edge.target_module)
    return tuple(reachable)


def _is_type_checking_guard(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute)
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
        and test.attr == "TYPE_CHECKING"
    )


class _RuntimeSourceVisitor(ast.NodeVisitor):
    """Collect runtime import roots and calls, excluding type-only imports."""

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


def _runtime_source_dependencies() -> _RuntimeSourceVisitor:
    tree = ast.parse(
        INTELLIGENCE_AGENT_PATH.read_text(encoding="utf-8"),
        filename=str(INTELLIGENCE_AGENT_PATH),
    )
    visitor = _RuntimeSourceVisitor()
    visitor.visit(tree)
    return visitor


def test_intelligence_agent_is_a_declared_application_composition_entrypoint() -> None:
    assert INTELLIGENCE_AGENT_PATH.is_file(), (
        "P7 Intelligence Agent must live in application/intelligence_agent.py"
    )


def test_intelligence_agent_reaches_northstar_only_through_the_typed_tool_api() -> None:
    reachable = _reachable_edges(runtime_import_edges())
    internal_edges = [
        edge
        for edge in reachable
        if edge.target_module.startswith("northstar_quant.") and edge.target_module != TOOL_API_MODULE
    ]

    assert not internal_edges, (
        "IntelligenceAgent must use only TypedResearchToolApi; it cannot compose direct intelligence/domain, "
        "foundation, live, broker, risk, storage, or configuration dependencies:\n"
        f"{format_diagnostics(internal_edges)}"
    )
    assert any(edge.target_module == TOOL_API_MODULE for edge in reachable), (
        "IntelligenceAgent must explicitly depend on TypedResearchToolApi rather than a parallel "
        "intelligence capability path"
    )


def test_intelligence_agent_does_not_import_process_storage_or_network_capabilities() -> None:
    visitor = _runtime_source_dependencies()
    violations = sorted(
        imported
        for imported in visitor.imports
        if imported.split(".", maxsplit=1)[0] in _FORBIDDEN_EXTERNAL_IMPORT_ROOTS
    )

    assert not violations, (
        "IntelligenceAgent must receive its only capability through TypedResearchToolApi and must not "
        f"open process, storage, database, credential, or network capabilities: {violations}"
    )


def test_intelligence_agent_dispatches_only_through_the_closed_invoke_entrypoint() -> None:
    visitor = _runtime_source_dependencies()
    direct_tool_calls = sorted(visitor.calls.intersection(_DIRECT_TOOL_METHOD_NAMES))

    assert "invoke" in visitor.calls, (
        "IntelligenceAgent must call TypedResearchToolApi.invoke rather than gaining a second tool path"
    )
    assert not direct_tool_calls, (
        "IntelligenceAgent must dispatch through the closed invoke entrypoint, not call individual tool "
        f"methods directly: {direct_tool_calls}"
    )


def test_intelligence_agent_does_not_use_reflective_or_filesystem_dispatch() -> None:
    visitor = _runtime_source_dependencies()
    violations = sorted(visitor.calls.intersection(_FORBIDDEN_REFLECTIVE_CALLS))

    assert not violations, (
        "IntelligenceAgent planning must use the closed typed tool surface; dynamic dispatch and direct "
        f"filesystem access are forbidden: {violations}"
    )
