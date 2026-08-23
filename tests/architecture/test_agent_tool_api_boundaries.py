"""P7 agent-tool composition boundary contracts.

The generic application-layer rule permits application to compose every business
domain.  The AI research tool facade is deliberately narrower: its dependency
closure must remain entirely outside portfolio/risk, trading, live runtime
configuration, and database capabilities.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterable

from tests.architecture._imports import ImportEdge, PACKAGE_ROOT, format_diagnostics, runtime_import_edges


TOOL_API_MODULE = "northstar_quant.application.agent_tools"
TOOL_API_PATH = PACKAGE_ROOT / "application" / "agent_tools.py"

_FORBIDDEN_INTERNAL_PREFIXES = (
    "northstar_quant.application.backtest",
    "northstar_quant.application.cli",
    "northstar_quant.application.live_service",
    "northstar_quant.application.scheduler",
    "northstar_quant.application.target_service",
    "northstar_quant.foundation.backup",
    "northstar_quant.foundation.config",
    "northstar_quant.foundation.db",
    "northstar_quant.foundation.messaging",
    "northstar_quant.foundation.scheduling",
    "northstar_quant.portfolio_risk",
    "northstar_quant.trading_execution",
)
_FORBIDDEN_EXTERNAL_IMPORT_ROOTS = frozenset(
    {
        "dotenv",
        "httpx",
        "os",
        "psycopg",
        "pydantic_settings",
        "requests",
        "socket",
        "sqlalchemy",
        "subprocess",
        "urllib",
    }
)
_FORBIDDEN_REFLECTIVE_CALLS = frozenset({"__import__", "eval", "exec", "getattr", "import_module"})


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def _reachable_edges(edges: Iterable[ImportEdge]) -> tuple[ImportEdge, ...]:
    """Return every runtime import edge reachable from the tool facade.

    A direct-import check is not sufficient: importing application.backtest, for
    example, would silently reintroduce portfolio composition through a helper.
    """

    by_source: dict[str, list[ImportEdge]] = defaultdict(list)
    for edge in edges:
        by_source[edge.source_module].append(edge)

    pending = [TOOL_API_MODULE]
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


class _RuntimeImportAndCallVisitor(ast.NodeVisitor):
    """Collect only imports and calls that can occur at runtime."""

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


def _runtime_source_dependencies() -> _RuntimeImportAndCallVisitor:
    tree = ast.parse(TOOL_API_PATH.read_text(encoding="utf-8"), filename=str(TOOL_API_PATH))
    visitor = _RuntimeImportAndCallVisitor()
    visitor.visit(tree)
    return visitor


def test_agent_tool_api_is_a_declared_application_composition_entrypoint() -> None:
    assert TOOL_API_PATH.is_file(), "P7 Typed Tool API must live in application/agent_tools.py"


def test_agent_tool_api_transitively_cannot_reach_trading_or_privileged_runtime() -> None:
    violations = [
        edge
        for edge in _reachable_edges(runtime_import_edges())
        if any(_matches_prefix(edge.target_module, prefix) for prefix in _FORBIDDEN_INTERNAL_PREFIXES)
    ]

    assert not violations, (
        "AI research tools must not reach portfolio/risk, broker/execution, live services, "
        "settings, database, or other privileged runtime paths:\n"
        f"{format_diagnostics(violations)}"
    )


def test_agent_tool_api_does_not_import_process_network_or_database_capabilities() -> None:
    visitor = _runtime_source_dependencies()
    violations = sorted(
        imported
        for imported in visitor.imports
        if imported.split(".", maxsplit=1)[0] in _FORBIDDEN_EXTERNAL_IMPORT_ROOTS
    )

    assert not violations, (
        "AI research tools receive explicit safe dependencies and must not open environment, "
        f"network, process, or database capabilities themselves: {violations}"
    )


def test_agent_tool_api_does_not_use_reflective_dispatch() -> None:
    visitor = _runtime_source_dependencies()
    violations = sorted(visitor.calls.intersection(_FORBIDDEN_REFLECTIVE_CALLS))

    assert not violations, (
        "AI tool dispatch must stay closed and typed; reflective or dynamic import calls are forbidden: "
        f"{violations}"
    )
