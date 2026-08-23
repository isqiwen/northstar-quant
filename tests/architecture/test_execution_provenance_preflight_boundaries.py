"""P8-WP04 architecture constraints for non-submitting provenance replay."""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import fields
import inspect

import northstar_quant.application.execution_provenance_preflight as provenance
from tests.architecture._imports import ImportEdge, PACKAGE_ROOT, format_diagnostics, runtime_import_edges


MODULE = "northstar_quant.application.execution_provenance_preflight"
PATH = PACKAGE_ROOT / "application" / "execution_provenance_preflight.py"
TOOL_API_MODULE = "northstar_quant.application.agent_tools"

_ALLOWED_INTERNAL_PREFIXES = (
    "northstar_quant.application.portfolio_risk_authority",
    "northstar_quant.application.research_strategy_activation",
    "northstar_quant.data.artifacts",
    "northstar_quant.foundation.config",
    "northstar_quant.portfolio_risk.limits",
    "northstar_quant.portfolio_risk.portfolio",
    "northstar_quant.trading_execution.broker.ctp_contract_mapping",
    "northstar_quant.trading_execution.execution",
    "northstar_quant.trading_execution.live.preflight",
    "northstar_quant.trading_execution.live.runtime_risk",
)
_FORBIDDEN_REACHABLE_PREFIXES = (
    "northstar_quant.application.agent_tools",
    "northstar_quant.application.cli",
    "northstar_quant.application.live_service",
    "northstar_quant.application.scheduler",
    "northstar_quant.application.target_service",
    "northstar_quant.foundation.db",
    "northstar_quant.foundation.messaging",
    "northstar_quant.foundation.scheduling",
    "northstar_quant.trading_execution.broker.broker_base",
    "northstar_quant.trading_execution.broker.ctp_broker",
    "northstar_quant.trading_execution.broker.ctp_sim_broker",
    "northstar_quant.trading_execution.broker.paper_broker",
    "northstar_quant.trading_execution.execution.router",
    "northstar_quant.trading_execution.orders.durable_submission",
    "northstar_quant.trading_execution.orders.order_management",
    "northstar_quant.trading_execution.reconciliation",
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
        "approve",
        "cancel",
        "cancel_order",
        "connect",
        "deploy",
        "disconnect",
        "eval",
        "exec",
        "open",
        "recover",
        "restore",
        "route",
        "submit",
        "submit_order",
        "trade",
        "write",
    }
)
_FORBIDDEN_FIELD_FRAGMENTS = ("broker_order", "cancel", "command", "submit", "trade")
_SAFE_FIELD_NAMES = frozenset(
    {"eligible_for_ctp_sim", "eligible_for_live", "eligible_for_trading"}
)


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


def test_execution_provenance_preflight_is_a_declared_application_composition_boundary() -> None:
    assert PATH.is_file(), "P8-WP04 must define application/execution_provenance_preflight.py"


def test_provenance_preflight_directly_uses_only_declared_p2_p3_p5_contracts() -> None:
    visitor = _source_dependencies()
    unexpected_internal = sorted(
        imported
        for imported in visitor.imports
        if imported.startswith("northstar_quant.")
        and not any(_has_prefix(imported, prefix) for prefix in _ALLOWED_INTERNAL_PREFIXES)
    )
    forbidden_imports = sorted(
        imported
        for imported in visitor.imports
        if imported.split(".", maxsplit=1)[0] in _FORBIDDEN_DIRECT_IMPORT_ROOTS
    )
    forbidden_calls = sorted(visitor.calls.intersection(_FORBIDDEN_CALLS))

    assert not unexpected_internal, (
        "provenance replay may compose only P2/P3/P5 contracts, config, and fingerprints: "
        f"{unexpected_internal}"
    )
    assert not forbidden_imports, (
        "provenance replay cannot open process, network, filesystem, database, or runtime "
        f"capabilities: {forbidden_imports}"
    )
    assert not forbidden_calls, (
        "provenance replay must not control a broker, account, order, or deployment: "
        f"{forbidden_calls}"
    )


def test_provenance_preflight_cannot_reach_broker_submission_or_runtime_control() -> None:
    violations = [
        edge
        for edge in _reachable_edges(MODULE, runtime_import_edges())
        if any(_has_prefix(edge.target_module, prefix) for prefix in _FORBIDDEN_REACHABLE_PREFIXES)
    ]
    assert not violations, (
        "P8-WP04 must remain a non-submitting evidence boundary and cannot reach agent, "
        "database, broker adapter, router, durable-order, reconciliation, or runtime control:\n"
        f"{format_diagnostics(violations)}"
    )


def test_provenance_preflight_is_not_reachable_through_the_ai_tool_api() -> None:
    reachable = _reachable_edges(TOOL_API_MODULE, runtime_import_edges())
    assert not any(edge.target_module == MODULE for edge in reachable), (
        "P7 typed agent tools must not gain CTP-sim provenance or execution-preflight capability"
    )


def test_provenance_preflight_has_one_explicit_behavior_and_no_trade_control_surface() -> None:
    module_functions = {
        name
        for name, value in vars(provenance).items()
        if inspect.isfunction(value) and value.__module__ == MODULE and not name.startswith("_")
    }
    verifier_methods = {
        name
        for name, value in inspect.getmembers(
            provenance.ExecutionProvenancePreflight,
            predicate=inspect.isfunction,
        )
        if not name.startswith("_")
    }
    records = (
        provenance.ExecutionDataEvidence,
        provenance.AccountAttributionAlert,
        provenance.AccountAttributionEvidence,
        provenance.ExecutionContractRuleEvidence,
        provenance.ExecutionProvenanceRequest,
        provenance.ExecutionOrderCommitment,
        provenance.ExecutionProvenancePreflightReceipt,
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
    assert verifier_methods == {"verify"}
    assert provenance.ExecutionProvenancePreflight.__slots__ == ()
    assert not violations, (
        "provenance evidence records cannot expose broker-order, submit, cancel, or trade control "
        f"surface: {violations}"
    )
