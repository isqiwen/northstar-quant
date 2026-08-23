"""P7 data-quality-agent least-privilege architecture contracts.

``DataQualityAgent`` is deliberately a diagnostic reader over the closed
``TypedResearchToolApi``.  It can search an already-authorized immutable
dataset summary and inspect that exact version's quality projection, but it
must never gain a second capability path to the data domain, a repair path,
or any trading/runtime privilege.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterable
import sys

from tests.architecture._imports import ImportEdge, PACKAGE_ROOT, format_diagnostics, runtime_import_edges


DATA_QUALITY_AGENT_MODULE = "northstar_quant.application.data_quality_agent"
DATA_QUALITY_AGENT_PATH = PACKAGE_ROOT / "application" / "data_quality_agent.py"
TOOL_API_MODULE = "northstar_quant.application.agent_tools"

_EXPECTED_TOOL_NAMES = frozenset({"SEARCH_DATASETS", "INSPECT_DATASET_QUALITY"})
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
_FORBIDDEN_PRIVILEGED_OR_MUTATING_CALLS = frozenset(
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
        "save",
        "submit_order",
        "trade",
        "trading",
        "write",
    }
)


def _reachable_edges(edges: Iterable[ImportEdge]) -> tuple[ImportEdge, ...]:
    """Return the runtime import closure starting at the data-quality agent."""

    by_source: dict[str, list[ImportEdge]] = defaultdict(list)
    for edge in edges:
        by_source[edge.source_module].append(edge)

    pending = [DATA_QUALITY_AGENT_MODULE]
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
    """Collect runtime imports, calls, and closed-tool names without type-only imports."""

    def __init__(self) -> None:
        self.imports: set[str] = set()
        self.calls: set[str] = set()
        self.tool_name_members: set[str] = set()

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

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if isinstance(node.value, ast.Name) and node.value.id == "ToolName":
            self.tool_name_members.add(node.attr)
        self.generic_visit(node)


def _runtime_source_dependencies() -> _RuntimeSourceVisitor:
    tree = ast.parse(
        DATA_QUALITY_AGENT_PATH.read_text(encoding="utf-8"),
        filename=str(DATA_QUALITY_AGENT_PATH),
    )
    visitor = _RuntimeSourceVisitor()
    visitor.visit(tree)
    return visitor


def _is_allowed_direct_import(module: str) -> bool:
    if module == TOOL_API_MODULE:
        return True
    return module.split(".", maxsplit=1)[0] in sys.stdlib_module_names


def test_data_quality_agent_is_a_declared_application_composition_entrypoint() -> None:
    assert DATA_QUALITY_AGENT_PATH.is_file(), (
        "P7 Data Quality Agent must live in application/data_quality_agent.py"
    )


def test_data_quality_agent_directly_imports_only_the_tool_api_or_standard_library() -> None:
    visitor = _runtime_source_dependencies()
    violations = sorted(imported for imported in visitor.imports if not _is_allowed_direct_import(imported))

    assert not violations, (
        "DataQualityAgent may directly import only application.agent_tools and the standard library; "
        f"unexpected runtime imports: {violations}"
    )


def test_data_quality_agent_reaches_northstar_only_through_the_typed_tool_api() -> None:
    reachable = _reachable_edges(runtime_import_edges())
    internal_edges = [
        edge
        for edge in reachable
        if edge.target_module.startswith("northstar_quant.") and edge.target_module != TOOL_API_MODULE
    ]

    assert not internal_edges, (
        "DataQualityAgent must use only TypedResearchToolApi; it cannot compose direct data-domain, "
        "artifact, source, database, configuration, broker, trading, repair, or publication dependencies:\n"
        f"{format_diagnostics(internal_edges)}"
    )
    assert any(edge.target_module == TOOL_API_MODULE for edge in reachable), (
        "DataQualityAgent must explicitly depend on TypedResearchToolApi rather than a parallel "
        "data-quality capability path"
    )


def test_data_quality_agent_does_not_import_process_storage_or_network_capabilities() -> None:
    visitor = _runtime_source_dependencies()
    violations = sorted(
        imported
        for imported in visitor.imports
        if imported.split(".", maxsplit=1)[0] in _FORBIDDEN_EXTERNAL_IMPORT_ROOTS
    )

    assert not violations, (
        "DataQualityAgent must receive its only capability through TypedResearchToolApi and must not "
        f"open process, storage, database, credential, or network capabilities: {violations}"
    )


def test_data_quality_agent_dispatches_the_exact_two_tools_only_through_invoke() -> None:
    visitor = _runtime_source_dependencies()
    direct_tool_calls = sorted(visitor.calls.intersection(_DIRECT_TOOL_METHOD_NAMES))

    assert visitor.tool_name_members == _EXPECTED_TOOL_NAMES, (
        "DataQualityAgent may use exactly search_datasets followed by inspect_dataset_quality; "
        f"actual ToolName members: {sorted(visitor.tool_name_members)}"
    )
    assert "invoke" in visitor.calls, (
        "DataQualityAgent must call TypedResearchToolApi.invoke rather than gaining a second tool path"
    )
    assert not direct_tool_calls, (
        "DataQualityAgent must dispatch through the closed invoke entrypoint, not call individual tool "
        f"methods directly: {direct_tool_calls}"
    )


def test_data_quality_agent_does_not_use_privileged_or_mutating_dispatch() -> None:
    visitor = _runtime_source_dependencies()
    violations = sorted(visitor.calls.intersection(_FORBIDDEN_PRIVILEGED_OR_MUTATING_CALLS))

    assert not violations, (
        "DataQualityAgent is diagnostic-only; privileged/write/repair/publish/"
        f"delete/broker/trading operations are forbidden: {violations}"
    )
