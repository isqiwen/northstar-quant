"""Architecture contracts for the standalone local factor-research surface."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from tests.architecture._imports import ImportEdge, format_diagnostics, runtime_import_edges


LOCAL_CLI_MODULE = "northstar_quant.application.research_cli"
LOCAL_SERVICE_MODULE = "northstar_quant.application.local_factor_research"
_FORBIDDEN_PREFIXES = (
    "northstar_quant.application.cli",
    "northstar_quant.application.live_service",
    "northstar_quant.application.scheduler",
    "northstar_quant.application.target_service",
    "northstar_quant.application.trading",
    "northstar_quant.foundation.db",
    "northstar_quant.foundation.messaging",
    "northstar_quant.foundation.scheduling",
    "northstar_quant.portfolio_risk",
    "northstar_quant.trading_execution",
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


def test_local_factor_research_cli_and_service_cannot_reach_live_or_execution_boundaries() -> None:
    all_edges = runtime_import_edges()
    violations = [
        edge
        for module in (LOCAL_CLI_MODULE, LOCAL_SERVICE_MODULE)
        for edge in _reachable_edges(module, all_edges)
        if any(_matches_prefix(edge.target_module, prefix) for prefix in _FORBIDDEN_PREFIXES)
    ]

    assert not violations, (
        "The standalone local factor-research path must not reach broad CLI, live runtime, "
        "scheduler, database, portfolio/risk, or execution components:\n"
        f"{format_diagnostics(violations)}"
    )
