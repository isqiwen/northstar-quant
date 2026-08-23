"""P7 Ops Tool API least-privilege architecture contracts.

``ops_tools`` is a closed, typed diagnostic boundary.  It may validate a
projection supplied by an explicit port, but it must not become an operational
runtime that opens configuration, storage, database, network, process, SSH,
CLI, health, backup, or deployment capabilities itself.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterable

from tests.architecture._imports import ImportEdge, PACKAGE_ROOT, format_diagnostics, runtime_import_edges


OPS_TOOL_API_MODULE = "northstar_quant.application.ops_tools"
OPS_TOOL_API_PATH = PACKAGE_ROOT / "application" / "ops_tools.py"

_FORBIDDEN_EXTERNAL_IMPORT_ROOTS = frozenset(
    {
        "aiohttp",
        "argparse",
        "boto3",
        "dotenv",
        "httpx",
        "os",
        "paramiko",
        "pathlib",
        "psycopg",
        "pydantic_settings",
        "requests",
        "scripts",
        "shutil",
        "socket",
        "sqlalchemy",
        "sqlite3",
        "subprocess",
        "sys",
        "tempfile",
        "urllib",
        "websockets",
    }
)
_FORBIDDEN_REFLECTIVE_OR_FILESYSTEM_CALLS = frozenset(
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
_FORBIDDEN_RUNTIME_PRODUCER_CALLS = frozenset(
    {
        "evaluate_database_backup_readiness",
        "load_deployment_inventory",
        "run_healthcheck",
        "run_linux_operation",
    }
)


def _reachable_edges(edges: Iterable[ImportEdge]) -> tuple[ImportEdge, ...]:
    """Return the runtime import closure starting at the Ops facade."""

    by_source: dict[str, list[ImportEdge]] = defaultdict(list)
    for edge in edges:
        by_source[edge.source_module].append(edge)

    pending = [OPS_TOOL_API_MODULE]
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
    """Collect runtime imports and calls while excluding type-only imports."""

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
    tree = ast.parse(OPS_TOOL_API_PATH.read_text(encoding="utf-8"), filename=str(OPS_TOOL_API_PATH))
    visitor = _RuntimeSourceVisitor()
    visitor.visit(tree)
    return visitor


def test_ops_tool_api_is_a_declared_application_composition_entrypoint() -> None:
    assert OPS_TOOL_API_PATH.is_file(), "P7 Ops Tool API must live in application/ops_tools.py"


def test_ops_tool_api_has_no_runtime_northstar_dependency() -> None:
    internal_edges = [
        edge
        for edge in _reachable_edges(runtime_import_edges())
        if edge.target_module.startswith("northstar_quant.")
    ]

    assert not internal_edges, (
        "TypedOpsToolApi must receive its single typed diagnostic port through explicit injection, "
        "not import application health/CLI, platform backup/config/db, deployment, trading, or another "
        f"Northstar runtime module:\n{format_diagnostics(internal_edges)}"
    )


def test_ops_tool_api_does_not_import_privileged_runtime_capabilities() -> None:
    visitor = _runtime_source_dependencies()
    violations = sorted(
        imported
        for imported in visitor.imports
        if imported.split(".", maxsplit=1)[0] in _FORBIDDEN_EXTERNAL_IMPORT_ROOTS
    )

    assert not violations, (
        "TypedOpsToolApi must not open configuration, filesystem, database, network, process, SSH, "
        f"or CLI capabilities: {violations}"
    )


def test_ops_tool_api_does_not_call_runtime_diagnostic_producers_or_reflective_io() -> None:
    visitor = _runtime_source_dependencies()
    violations = sorted(
        visitor.calls.intersection(
            _FORBIDDEN_REFLECTIVE_OR_FILESYSTEM_CALLS | _FORBIDDEN_RUNTIME_PRODUCER_CALLS
        )
    )

    assert not violations, (
        "TypedOpsToolApi may only validate the injected typed port result; direct health, backup, "
        f"deployment, remote-operation, reflective, or filesystem calls are forbidden: {violations}"
    )
