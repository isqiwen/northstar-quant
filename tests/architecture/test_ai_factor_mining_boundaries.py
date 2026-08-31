"""Least-privilege contracts for AI-assisted factor-mining composition."""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterable

from tests.architecture._imports import ImportEdge, PACKAGE_ROOT, format_diagnostics, runtime_import_edges


AI_AGENT_MODULE = "northstar_quant.application.ai_factor_mining"
AI_AGENT_PATH = PACKAGE_ROOT / "application" / "ai_factor_mining.py"
TOOL_API_MODULE = "northstar_quant.application.factor_mining_tools"
TOOL_API_PATH = PACKAGE_ROOT / "application" / "factor_mining_tools.py"
TRUSTED_CAMPAIGN_MODULE = "northstar_quant.application.factor_mining_campaign"
FACTOR_MINING_ROOT = PACKAGE_ROOT / "research" / "factor_mining"

_FORBIDDEN_INTERNAL_PREFIXES = (
    "northstar_quant.application.backtest",
    "northstar_quant.application.factor_mining_campaign",
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
    {"__import__", "eval", "exec", "getattr", "import_module", "open", "setattr"}
)


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def _reachable_edges(start_module: str, edges: Iterable[ImportEdge]) -> tuple[ImportEdge, ...]:
    by_source: dict[str, list[ImportEdge]] = defaultdict(list)
    for edge in edges:
        by_source[edge.source_module].append(edge)
    pending = [start_module]
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


def _class_method_names(path, class_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                member.name
                for member in node.body
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    raise AssertionError(f"expected {class_name} in {path}")


def test_ai_factor_mining_agent_and_tool_facade_have_no_privileged_runtime_closure() -> None:
    all_edges = runtime_import_edges()
    violations = [
        edge
        for module in (AI_AGENT_MODULE, TOOL_API_MODULE)
        for edge in _reachable_edges(module, all_edges)
        if any(_matches_prefix(edge.target_module, prefix) for prefix in _FORBIDDEN_INTERNAL_PREFIXES)
    ]

    assert not violations, (
        "The AI factor-mining path must not reach trusted campaign composition, portfolio/risk, "
        "trading, live runtime, database, scheduling, or configuration capabilities:\n"
        f"{format_diagnostics(violations)}"
    )


def test_ai_factor_mining_agent_reaches_the_trusted_campaign_only_through_the_closed_tool_api() -> None:
    direct_edges = [
        edge
        for edge in runtime_import_edges()
        if edge.source_module == AI_AGENT_MODULE and edge.target_module.startswith("northstar_quant.")
    ]

    assert TOOL_API_MODULE in {edge.target_module for edge in direct_edges}
    assert TRUSTED_CAMPAIGN_MODULE not in {edge.target_module for edge in direct_edges}


def test_ai_factor_mining_agent_and_tool_facade_do_not_open_external_capabilities_or_reflect() -> None:
    visitors = (_visitor(AI_AGENT_PATH), _visitor(TOOL_API_PATH))
    imports = {
        imported
        for visitor in visitors
        for imported in visitor.imports
        if imported.split(".", maxsplit=1)[0] in _FORBIDDEN_EXTERNAL_IMPORT_ROOTS
    }
    calls = {call for visitor in visitors for call in visitor.calls}

    assert not imports, f"AI factor-mining components cannot open external capabilities: {imports}"
    assert not calls.intersection(_FORBIDDEN_REFLECTIVE_CALLS), (
        "AI factor-mining components must use a closed typed seam, not reflective dispatch: "
        f"{sorted(calls.intersection(_FORBIDDEN_REFLECTIVE_CALLS))}"
    )


def test_ai_factor_mining_surface_exposes_discovery_only_not_selection_or_oos_release() -> None:
    """The trusted runner owns selection/OOS; AI can only ask for discovery."""

    assert _class_method_names(AI_AGENT_PATH, "AIFactorMiningAgent") == {"__init__", "run"}
    assert _class_method_names(TOOL_API_PATH, "FactorMiningToolApi") == {
        "__init__",
        "evaluate_discovery_candidate_batch",
        "invoke",
    }
    assert _class_method_names(TOOL_API_PATH, "FactorMiningCampaignPort") == {
        "evaluate_discovery_candidate_batch"
    }


def test_factor_mining_domain_never_imports_application_or_trading_boundaries() -> None:
    violations: list[str] = []
    for path in sorted(FACTOR_MINING_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = node.module if isinstance(node, ast.ImportFrom) else None
            if module is None:
                continue
            if module.startswith(
                (
                    "northstar_quant.application",
                    "northstar_quant.portfolio_risk",
                    "northstar_quant.trading_execution",
                )
            ):
                violations.append(f"{path.relative_to(PACKAGE_ROOT)}:{node.lineno}: {module}")

    assert not violations, (
        "research.factor_mining must stay a research-domain contract and cannot import application, "
        f"portfolio/risk, or execution modules: {violations}"
    )
