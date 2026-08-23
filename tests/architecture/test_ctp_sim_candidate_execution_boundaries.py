"""P8-WP05 architecture constraints for guarded CTP-sim candidate execution."""

from __future__ import annotations

import ast
import inspect
from collections import defaultdict
from collections.abc import Iterable

from northstar_quant.application.ctp_sim_candidate_execution import (
    CtpSimCandidateExecutor,
)
from tests.architecture._imports import (
    ImportEdge,
    PACKAGE_ROOT,
    format_diagnostics,
    runtime_import_edges,
)


MODULE = "northstar_quant.application.ctp_sim_candidate_execution"
PATH = PACKAGE_ROOT / "application" / "ctp_sim_candidate_execution.py"
TOOL_API_MODULE = "northstar_quant.application.agent_tools"

_ALLOWED_INTERNAL_PREFIXES = (
    "northstar_quant.application.execution_provenance_preflight",
    "northstar_quant.application.portfolio_risk_authority",
    "northstar_quant.application.portfolio_risk_manual_approval",
    "northstar_quant.data_platform.artifacts",
    "northstar_quant.platform.common",
    "northstar_quant.platform.config",
    "northstar_quant.platform.db",
    "northstar_quant.portfolio_risk.limits",
    "northstar_quant.portfolio_risk.risk",
    "northstar_quant.trading_execution.broker.ctp_contract_mapping",
    "northstar_quant.trading_execution.broker.ctp_sim_broker",
    "northstar_quant.trading_execution.execution",
    "northstar_quant.trading_execution.orders",
    "northstar_quant.trading_execution.reconciliation",
)
_FORBIDDEN_INTERNAL_PREFIXES = (
    "northstar_quant.application.agent_tools",
    "northstar_quant.application.cli",
    "northstar_quant.application.live_service",
    "northstar_quant.application.scheduler",
    "northstar_quant.trading_execution.broker.ctp_broker",
    "northstar_quant.trading_execution.broker.ctp_front",
    "northstar_quant.trading_execution.broker.paper_broker",
)
_FORBIDDEN_DIRECT_IMPORT_ROOTS = frozenset(
    {
        "argparse",
        "asyncio",
        "boto3",
        "httpx",
        "os",
        "paramiko",
        "psycopg",
        "requests",
        "shutil",
        "socket",
        "subprocess",
        "urllib",
        "websockets",
    }
)


def _has_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


class _Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: set[str] = set()
        self.calls: set[str] = set()

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


def _source_dependencies() -> _Visitor:
    visitor = _Visitor()
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


def test_ctp_sim_candidate_executor_is_a_declared_application_composition_boundary() -> None:
    assert PATH.is_file(), "P8-WP05 must define application/ctp_sim_candidate_execution.py"


def test_candidate_executor_public_constructor_has_no_caller_controlled_runtime_injection() -> None:
    """Production candidate execution owns its settings and clock sources."""

    parameters = inspect.signature(CtpSimCandidateExecutor).parameters
    forbidden = {"settings_provider", "clock"}.intersection(parameters)
    assert not forbidden, (
        "candidate execution must not expose caller-controlled runtime safety inputs: "
        f"{sorted(forbidden)}"
    )
    source = PATH.read_text(encoding="utf-8")
    assert "tests.helpers.ctp_sim_candidate_execution" not in source, (
        "production candidate execution must not import the test-only runtime-injection seam"
    )


def test_candidate_executor_uses_only_the_ctp_sim_submission_chain() -> None:
    visitor = _source_dependencies()
    unexpected = sorted(
        item
        for item in visitor.imports
        if item.startswith("northstar_quant.")
        and not any(_has_prefix(item, prefix) for prefix in _ALLOWED_INTERNAL_PREFIXES)
    )
    forbidden = sorted(
        item
        for item in visitor.imports
        if item.split(".", maxsplit=1)[0] in _FORBIDDEN_DIRECT_IMPORT_ROOTS
    )
    assert not unexpected, (
        "P8-WP05 may compose only provenance, PostgreSQL durability, risk, reconciliation, "
        f"and the isolated CTP-sim adapter: {unexpected}"
    )
    assert not forbidden, (
        "candidate CTP-sim execution cannot open external network/process/deployment "
        f"capabilities: {forbidden}"
    )


def test_candidate_executor_cannot_reach_live_ctp_or_ai_control_surfaces() -> None:
    violations = [
        edge
        for edge in _reachable_edges(MODULE, runtime_import_edges())
        if any(_has_prefix(edge.target_module, prefix) for prefix in _FORBIDDEN_INTERNAL_PREFIXES)
    ]
    assert not violations, (
        "P8-WP05 must remain CTP-sim-only and cannot reach AI tools, CLI, scheduler, legacy "
        "live service, real CTP, CTP front, or paper trading:\n"
        f"{format_diagnostics(violations)}"
    )


def test_ai_tool_api_cannot_reach_candidate_ctp_sim_execution() -> None:
    reachable = _reachable_edges(TOOL_API_MODULE, runtime_import_edges())
    assert not any(edge.target_module == MODULE for edge in reachable), (
        "P7 typed agent tools must not gain candidate CTP-sim submission capability"
    )


def test_candidate_executor_is_the_only_production_authority_issuer() -> None:
    private_grant_markers = (
        "_issue_ctp_sim_submission_authority",
        "_AUTHORITY_ISSUER",
        "CtpSimSubmissionAuthority(",
    )
    allowed = {
        PACKAGE_ROOT
        / "trading_execution"
        / "orders"
        / "ctp_sim_submission_guard.py",
        PATH,
    }
    violations = [
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if path not in allowed
        and any(marker in path.read_text(encoding="utf-8") for marker in private_grant_markers)
    ]
    assert not violations, (
        "only the P8 candidate composition boundary may issue a CTP-sim submission "
        f"authority: {violations}"
    )


def test_candidate_executor_keeps_a_final_consumption_and_reconciliation_boundary() -> None:
    source = PATH.read_text(encoding="utf-8")
    required_markers = (
        "record_execution_provenance_consumption(",
        "find_execution_provenance_consumption(",
        "ctp_sim_submission_authority=authority",
        "CTP_SIM_CANDIDATE_RECONCILIATION_REQUIRED",
        "halt_for_reconciliation(",
        "resolved_snapshot = self.broker.sync_state_checked(",
    )
    missing = [item for item in required_markers if item not in source]
    assert not missing, (
        "candidate execution must retain one-time durable consumption, adapter final guard, "
        f"and provenance-aware reconciliation: {missing}"
    )
