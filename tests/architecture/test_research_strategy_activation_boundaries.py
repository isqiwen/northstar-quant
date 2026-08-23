"""P8-WP03 architecture contracts for manual research-target activation.

The activation seam may compose stable P2 and P3 contracts, but it must remain
a pure, application-owned evidence boundary.  It cannot become a route to
portfolio approval, execution, broker, runtime, database, or AI-agent control.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import fields
import inspect

import northstar_quant.application.research_strategy_activation as activation
from tests.architecture._imports import ImportEdge, PACKAGE_ROOT, format_diagnostics, runtime_import_edges


MODULE = "northstar_quant.application.research_strategy_activation"
PATH = PACKAGE_ROOT / "application" / "research_strategy_activation.py"
P3_TARGETS_MODULE = "northstar_quant.portfolio_risk.portfolio.targets"
P3_TARGETS_PATH = PACKAGE_ROOT / "portfolio_risk" / "portfolio" / "targets.py"
TOOL_API_MODULE = "northstar_quant.application.agent_tools"

_FORBIDDEN_REACHABLE_PREFIXES = (
    "northstar_quant.application.agent_tools",
    "northstar_quant.application.cli",
    "northstar_quant.application.decision_replay_backtest",
    "northstar_quant.application.live_service",
    "northstar_quant.application.scheduler",
    "northstar_quant.application.target_service",
    "northstar_quant.platform.db",
    "northstar_quant.platform.messaging",
    "northstar_quant.platform.scheduling",
    "northstar_quant.trading_execution",
)
_FORBIDDEN_DIRECT_IMPORT_ROOTS = frozenset(
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
        "sqlalchemy",
        "sqlite3",
        "subprocess",
        "tempfile",
        "urllib",
        "websockets",
    }
)
_FORBIDDEN_CALLS = frozenset(
    {
        "__import__",
        "approve",
        "cancel_order",
        "deploy",
        "eval",
        "exec",
        "getattr",
        "import_module",
        "open",
        "publish",
        "recover",
        "restore",
        "save",
        "submit",
        "submit_order",
        "trade",
        "write",
    }
)
_FORBIDDEN_FIELD_FRAGMENTS = (
    "broker",
    "command",
    "execution",
    "order",
    "portfolio_approval",
    "recover",
    "trade",
)
_SAFE_FIELD_NAMES = frozenset({"eligible_for_trading"})


def _has_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def _is_type_checking_guard(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute)
        and isinstance(test.value, ast.Name)
        and test.attr == "TYPE_CHECKING"
    )


class _RuntimeSourceVisitor(ast.NodeVisitor):
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


def test_manual_activation_is_a_declared_application_composition_boundary() -> None:
    assert PATH.is_file(), "P8-WP03 must define application/research_strategy_activation.py"


def test_manual_activation_directly_uses_only_p2_p3_pure_data_and_standard_library() -> None:
    visitor = _source_dependencies()
    unexpected_internal = sorted(
        imported
        for imported in visitor.imports
        if imported.startswith("northstar_quant.")
        and not (
            _has_prefix(imported, "northstar_quant.data_platform.artifacts")
            or _has_prefix(imported, "northstar_quant.portfolio_risk.portfolio")
            or _has_prefix(imported, "northstar_quant.research")
        )
    )
    forbidden_imports = sorted(
        imported
        for imported in visitor.imports
        if imported.split(".", maxsplit=1)[0] in _FORBIDDEN_DIRECT_IMPORT_ROOTS
    )
    forbidden_calls = sorted(visitor.calls.intersection(_FORBIDDEN_CALLS))

    assert not unexpected_internal, (
        "manual activation can compose only stable P2/P3/data fingerprint contracts: "
        f"{unexpected_internal}"
    )
    assert not forbidden_imports, (
        "manual activation cannot open process, network, filesystem, database, or runtime "
        f"capabilities: {forbidden_imports}"
    )
    assert not forbidden_calls, (
        "manual activation must issue pure evidence only, without privileged control behavior: "
        f"{forbidden_calls}"
    )


def test_manual_activation_cannot_reach_runtime_or_trading_capabilities() -> None:
    violations = [
        edge
        for edge in _reachable_edges(MODULE, runtime_import_edges())
        if any(_has_prefix(edge.target_module, prefix) for prefix in _FORBIDDEN_REACHABLE_PREFIXES)
    ]
    assert not violations, (
        "manual Research-to-StrategyTarget activation cannot reach agents, runtime control, "
        "portfolio approval, execution, broker, live, or database:\n"
        f"{format_diagnostics(violations)}"
    )


def test_p3_target_contract_remains_independent_from_application() -> None:
    tree = ast.parse(P3_TARGETS_PATH.read_text(encoding="utf-8"), filename=str(P3_TARGETS_PATH))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any(_has_prefix(module, "northstar_quant.application") for module in imports)


def test_activation_is_not_reachable_through_the_ai_tool_api() -> None:
    reachable = _reachable_edges(TOOL_API_MODULE, runtime_import_edges())
    assert not any(edge.target_module == MODULE for edge in reachable), (
        "P7 typed research tools must not gain the P8 manual target-activation capability"
    )


def test_activation_has_one_explicit_behavior_and_no_control_surface() -> None:
    module_functions = {
        name
        for name, value in vars(activation).items()
        if inspect.isfunction(value) and value.__module__ == MODULE and not name.startswith("_")
    }
    activator_methods = {
        name
        for name, value in inspect.getmembers(
            activation.ResearchStrategyTargetActivator,
            predicate=inspect.isfunction,
        )
        if not name.startswith("_")
    }
    records = (
        activation.StrategyTargetProposal,
        activation.HumanStrategyTargetActivationApproval,
        activation.ResearchStrategyActivationRequest,
        activation.ResearchStrategyActivationReceipt,
    )
    violations = {
        record.__name__: sorted(
            field.name
            for field in fields(record)
            if field.name not in _SAFE_FIELD_NAMES
            and any(fragment in field.name.casefold() for fragment in _FORBIDDEN_FIELD_FRAGMENTS)
        )
        for record in records
    }
    violations = {name: names for name, names in violations.items() if names}

    assert module_functions == set()
    assert activator_methods == {"activate"}
    assert activation.ResearchStrategyTargetActivator.__slots__ == ()
    assert not violations, (
        "manual activation records cannot expose broker/order/execution/trading/portfolio-approval "
        f"surface: {violations}"
    )
