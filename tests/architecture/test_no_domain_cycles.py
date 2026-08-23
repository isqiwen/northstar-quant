"""领域级依赖图必须是 DAG。"""

from __future__ import annotations

from collections import defaultdict

from tests.architecture._imports import ARCHITECTURE_SCOPES, ImportEdge, format_diagnostics, runtime_import_edges


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    active: list[str] = []
    active_set: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> list[str] | None:
        if node in active_set:
            return [*active[active.index(node) :], node]
        if node in visited:
            return None
        active.append(node)
        active_set.add(node)
        for target in sorted(graph[node]):
            cycle = visit(target)
            if cycle is not None:
                return cycle
        active.pop()
        active_set.remove(node)
        visited.add(node)
        return None

    for domain in sorted(graph):
        cycle = visit(domain)
        if cycle is not None:
            return cycle
    return None


def test_business_domain_dependency_graph_is_acyclic() -> None:
    """同领域内部导入不计入图；领域之间绝不能形成环。"""

    edges = [
        edge
        for edge in runtime_import_edges()
        if edge.source_scope in ARCHITECTURE_SCOPES
        and edge.target_scope in ARCHITECTURE_SCOPES
        and edge.source_scope != edge.target_scope
    ]
    graph: dict[str, set[str]] = {domain: set() for domain in ARCHITECTURE_SCOPES}
    evidence: dict[tuple[str, str], list[ImportEdge]] = defaultdict(list)
    for edge in edges:
        graph[edge.source_scope].add(edge.target_scope)
        evidence[(edge.source_scope, edge.target_scope)].append(edge)

    cycle = _find_cycle(graph)
    if cycle is None:
        return

    cycle_edges = [
        evidence[(source, target)][0]
        for source, target in zip(cycle, cycle[1:], strict=True)
    ]
    rendered_cycle = " -> ".join(cycle)
    raise AssertionError(
        f"领域依赖图出现循环：{rendered_cycle}\n{format_diagnostics(cycle_edges)}"
    )
