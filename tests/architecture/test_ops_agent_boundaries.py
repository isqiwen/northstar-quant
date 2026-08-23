"""P7 Ops Agent least-privilege architecture contracts.

``OpsAgent`` may consume one diagnostic snapshot through the closed
``TypedOpsToolApi``.  It must not reach health, logs, deployment, backup, SSH,
configuration, a broker, or any remediation capability directly.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterable
import sys

from tests.architecture._imports import ImportEdge, PACKAGE_ROOT, format_diagnostics, runtime_import_edges


OPS_AGENT_MODULE = "northstar_quant.application.ops_agent"
OPS_AGENT_PATH = PACKAGE_ROOT / "application" / "ops_agent.py"
OPS_TOOL_API_MODULE = "northstar_quant.application.ops_tools"

_EXPECTED_TOOL_NAMES = frozenset({"INSPECT_OPS_SNAPSHOT"})
_DIRECT_TOOL_METHOD_NAMES = frozenset({"inspect_ops_snapshot"})
_FORBIDDEN_EXTERNAL_IMPORT_ROOTS = frozenset(
    {
        "aiohttp",
        "argparse",
        "boto3",
        "dotenv",
        "httpx",
        "openai",
        "os",
        "paramiko",
        "pathlib",
        "psycopg",
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
_FORBIDDEN_PRIVILEGED_OR_MUTATING_CALLS = frozenset(
    {
        "approve",
        "approve_production",
        "cancel_order",
        "create_execution_plan",
        "create_target",
        "delete",
        "deploy",
        "disable_kill_switch",
        "enable_live_trading",
        "publish",
        "repair",
        "restart",
        "restore",
        "resume",
        "resume_risk",
        "rollback",
        "save",
        "submit_order",
        "trade",
        "transition",
        "write",
    }
)


def _reachable_edges(edges: Iterable[ImportEdge]) -> tuple[ImportEdge, ...]:
    """Return the runtime import closure starting at the Ops agent."""

    by_source: dict[str, list[ImportEdge]] = defaultdict(list)
    for edge in edges:
        by_source[edge.source_module].append(edge)

    pending = [OPS_AGENT_MODULE]
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
    """Collect runtime imports, calls, and closed tool names without type-only imports."""

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
        if isinstance(node.value, ast.Name) and node.value.id == "OpsToolName":
            self.tool_name_members.add(node.attr)
        self.generic_visit(node)


def _runtime_source_dependencies() -> _RuntimeSourceVisitor:
    tree = ast.parse(OPS_AGENT_PATH.read_text(encoding="utf-8"), filename=str(OPS_AGENT_PATH))
    visitor = _RuntimeSourceVisitor()
    visitor.visit(tree)
    return visitor


def _is_allowed_direct_import(module: str) -> bool:
    if module == OPS_TOOL_API_MODULE:
        return True
    return module.split(".", maxsplit=1)[0] in sys.stdlib_module_names


def test_ops_agent_is_a_declared_application_composition_entrypoint() -> None:
    assert OPS_AGENT_PATH.is_file(), "P7 Ops Agent must live in application/ops_agent.py"


def test_ops_agent_directly_imports_only_the_ops_tool_api_or_standard_library() -> None:
    visitor = _runtime_source_dependencies()
    violations = sorted(imported for imported in visitor.imports if not _is_allowed_direct_import(imported))

    assert not violations, (
        "OpsAgent may directly import only application.ops_tools and the standard library; "
        f"unexpected runtime imports: {violations}"
    )


def test_ops_agent_reaches_northstar_only_through_the_typed_ops_tool_api() -> None:
    reachable = _reachable_edges(runtime_import_edges())
    internal_edges = [
        edge
        for edge in reachable
        if edge.target_module.startswith("northstar_quant.") and edge.target_module != OPS_TOOL_API_MODULE
    ]

    assert not internal_edges, (
        "OpsAgent must use only TypedOpsToolApi; it cannot compose direct health, log, deployment, "
        "backup, config, database, broker, trading, recovery, or SSH dependencies:\n"
        f"{format_diagnostics(internal_edges)}"
    )
    assert any(edge.target_module == OPS_TOOL_API_MODULE for edge in reachable), (
        "OpsAgent must explicitly depend on TypedOpsToolApi rather than a parallel operational "
        "capability path"
    )


def test_ops_agent_does_not_import_process_storage_or_network_capabilities() -> None:
    visitor = _runtime_source_dependencies()
    violations = sorted(
        imported
        for imported in visitor.imports
        if imported.split(".", maxsplit=1)[0] in _FORBIDDEN_EXTERNAL_IMPORT_ROOTS
    )

    assert not violations, (
        "OpsAgent must receive its only capability through TypedOpsToolApi and must not open process, "
        f"storage, database, credential, network, SSH, or CLI capabilities: {violations}"
    )


def test_ops_agent_dispatches_exactly_one_snapshot_tool_only_through_invoke() -> None:
    visitor = _runtime_source_dependencies()
    direct_tool_calls = sorted(visitor.calls.intersection(_DIRECT_TOOL_METHOD_NAMES))

    assert visitor.tool_name_members == _EXPECTED_TOOL_NAMES, (
        "OpsAgent may use exactly INSPECT_OPS_SNAPSHOT; "
        f"actual OpsToolName members: {sorted(visitor.tool_name_members)}"
    )
    assert "invoke" in visitor.calls, (
        "OpsAgent must call TypedOpsToolApi.invoke rather than gaining a second tool path"
    )
    assert not direct_tool_calls, (
        "OpsAgent must dispatch through the closed invoke entrypoint, not call individual tool "
        f"methods directly: {direct_tool_calls}"
    )


def test_ops_agent_does_not_use_reflective_or_privileged_mutating_dispatch() -> None:
    visitor = _runtime_source_dependencies()
    violations = sorted(
        visitor.calls.intersection(
            _FORBIDDEN_REFLECTIVE_OR_FILESYSTEM_CALLS | _FORBIDDEN_PRIVILEGED_OR_MUTATING_CALLS
        )
    )

    assert not violations, (
        "OpsAgent is diagnostic-only; reflective, deploy, recovery, resume, restore, rollback, "
        f"kill-switch, or trading operations are forbidden: {violations}"
    )
