"""Architecture contracts for the durable local factor-mining composition.

P11-WP05 deliberately adds a PostgreSQL-backed *application* composition
boundary.  It may compose the existing local research path and Foundation DB
contracts, but it must never turn the AI tool or standalone research CLI into
a privileged database/trading capability.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterable
from importlib import import_module

from tests.architecture._imports import ImportEdge, PACKAGE_ROOT, format_diagnostics, runtime_import_edges


DURABLE_MODULE = "northstar_quant.application.durable_factor_mining_campaign"
DURABLE_PATH = PACKAGE_ROOT / "application" / "durable_factor_mining_campaign.py"
CAMPAIGN_CLI_MODULE = "northstar_quant.application.factor_mining_campaign_cli"
CAMPAIGN_CLI_PATH = PACKAGE_ROOT / "application" / "factor_mining_campaign_cli.py"
WORKER_SUPERVISOR_MODULE = "northstar_quant.application.factor_mining_worker_supervisor"
WORKER_SUPERVISOR_PATH = PACKAGE_ROOT / "application" / "factor_mining_worker_supervisor.py"
FOUNDATION_REPLAY_AUTHORIZATION_BRIDGE_MODULE = "northstar_quant.foundation.db.repositories"
AI_AGENT_MODULE = "northstar_quant.application.ai_factor_mining"
TOOL_API_MODULE = "northstar_quant.application.factor_mining_tools"
LOCAL_CLI_MODULE = "northstar_quant.application.research_cli"
LOCAL_SERVICE_MODULE = "northstar_quant.application.local_factor_research"

_FORBIDDEN_RUNTIME_PREFIXES = (
    "northstar_quant.application.cli",
    "northstar_quant.application.live_service",
    "northstar_quant.application.scheduler",
    "northstar_quant.application.target_service",
    "northstar_quant.portfolio_risk",
    "northstar_quant.trading_execution",
)
_FORBIDDEN_EXTERNAL_IMPORT_ROOTS = frozenset(
    {
        "aiohttp",
        "anthropic",
        "boto3",
        "httpx",
        "openai",
        "paramiko",
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
_FORBIDDEN_REFLECTIVE_OR_TRADING_CALLS = frozenset(
    {
        "__import__",
        "approve",
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
        "submit_order",
        "trade",
    }
)
_PRIVATE_REPLAY_AUTHORIZATION_BRIDGE_SYMBOLS = frozenset(
    {
        "_FactorMiningCampaignReplayAuthorizationInput",
        "_factor_mining_campaign_authorize_replay",
    }
)


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def _reachable_edges(start_module: str, edges: Iterable[ImportEdge]) -> tuple[ImportEdge, ...]:
    by_source: dict[str, list[ImportEdge]] = defaultdict(list)
    for edge in edges:
        by_source[edge.source_module].append(edge)

    pending = [start_module]
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


class _RuntimeSourceVisitor(ast.NodeVisitor):
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


def _visitor(path) -> _RuntimeSourceVisitor:
    visitor = _RuntimeSourceVisitor()
    visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return visitor


def test_durable_factor_mining_runner_and_cli_are_explicit_application_surfaces() -> None:
    assert DURABLE_PATH.is_file(), "P11-WP05 must define durable_factor_mining_campaign.py"
    assert CAMPAIGN_CLI_PATH.is_file(), "P11-WP05 must define factor_mining_campaign_cli.py"
    assert WORKER_SUPERVISOR_PATH.is_file(), (
        "P11-WP05 must keep hard local worker limits in an explicit application seam"
    )


def test_durable_factor_mining_composition_may_use_postgresql_but_not_trading_capabilities() -> None:
    all_edges = runtime_import_edges()
    violations = [
        edge
        for module in (
            DURABLE_MODULE,
            CAMPAIGN_CLI_MODULE,
            WORKER_SUPERVISOR_MODULE,
        )
        for edge in _reachable_edges(module, all_edges)
        if any(_matches_prefix(edge.target_module, prefix) for prefix in _FORBIDDEN_RUNTIME_PREFIXES)
    ]

    assert not violations, (
        "The durable factor-mining path is local research composition only; it cannot reach broad "
        "CLI, live services, scheduler, portfolio/risk, or execution:\n"
        f"{format_diagnostics(violations)}"
    )


def test_durable_factor_mining_runner_has_an_explicit_foundation_database_dependency() -> None:
    imports = _visitor(DURABLE_PATH).imports

    assert any(_matches_prefix(module, "northstar_quant.foundation.db") for module in imports), (
        "The durable runner must use the Foundation PostgreSQL contract rather than an in-memory "
        "or SQLite fallback."
    )


def test_only_the_durable_verifier_bridge_can_import_private_foundation_replay_writes() -> None:
    """No normal application surface may mint a replay authorization fact."""

    imports: list[str] = []
    durable_imports: set[str] = set()
    for path in sorted((PACKAGE_ROOT / "application").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module != FOUNDATION_REPLAY_AUTHORIZATION_BRIDGE_MODULE:
                continue
            imported = {
                alias.name
                for alias in node.names
                if alias.name in _PRIVATE_REPLAY_AUTHORIZATION_BRIDGE_SYMBOLS
            }
            if not imported:
                continue
            if path == DURABLE_PATH:
                durable_imports.update(imported)
                continue
            imports.append(f"{path.relative_to(PACKAGE_ROOT)}:{sorted(imported)}")

    assert durable_imports == _PRIVATE_REPLAY_AUTHORIZATION_BRIDGE_SYMBOLS
    assert not imports, (
        "Only durable_factor_mining_campaign's verifier-backed bridge may import private "
        f"Foundation replay authorization writers: {imports}"
    )


def test_foundation_exposes_no_public_replay_self_attestation_api() -> None:
    repositories = import_module(FOUNDATION_REPLAY_AUTHORIZATION_BRIDGE_MODULE)

    assert not hasattr(repositories, "FactorMiningCampaignReplayAuthorizationInput")
    assert not hasattr(repositories, "factor_mining_campaign_authorize_replay")


def test_durable_factor_mining_surfaces_do_not_open_network_process_or_trading_dispatch() -> None:
    visitors = (
        _visitor(DURABLE_PATH),
        _visitor(CAMPAIGN_CLI_PATH),
        _visitor(WORKER_SUPERVISOR_PATH),
    )
    imports = {
        imported
        for visitor in visitors
        for imported in visitor.imports
        if imported.split(".", maxsplit=1)[0] in _FORBIDDEN_EXTERNAL_IMPORT_ROOTS
    }
    calls = {call for visitor in visitors for call in visitor.calls}

    assert not imports, (
        "Durable local factor mining must receive only its typed local dependencies and cannot open "
        f"network, process, or alternate database capabilities: {sorted(imports)}"
    )
    assert not calls.intersection(_FORBIDDEN_REFLECTIVE_OR_TRADING_CALLS), (
        "Durable factor mining cannot dynamically dispatch or control trading/runtime state: "
        f"{sorted(calls.intersection(_FORBIDDEN_REFLECTIVE_OR_TRADING_CALLS))}"
    )


def test_durable_runner_does_not_grant_database_access_to_ai_or_standalone_research_surfaces() -> None:
    all_edges = runtime_import_edges()
    for module in (AI_AGENT_MODULE, TOOL_API_MODULE, LOCAL_CLI_MODULE, LOCAL_SERVICE_MODULE):
        reachable = _reachable_edges(module, all_edges)
        leaked_durable_runner = [
            edge for edge in reachable if _matches_prefix(edge.target_module, DURABLE_MODULE)
        ]
        leaked_database = [
            edge
            for edge in reachable
            if _matches_prefix(edge.target_module, "northstar_quant.foundation.db")
        ]

        assert not leaked_durable_runner, (
            f"{module} must not gain durable factor-mining reservation capability:\n"
            f"{format_diagnostics(leaked_durable_runner)}"
        )
        assert not leaked_database, (
            f"{module} must remain outside the PostgreSQL campaign ledger closure:\n"
            f"{format_diagnostics(leaked_database)}"
        )
