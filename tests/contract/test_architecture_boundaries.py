"""核心包依赖边界的回归测试。"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from tests.support.paths import PROJECT_ROOT

PACKAGE_ROOT = PROJECT_ROOT / "src" / "northstar_quant"

ALLOWED_CORE_DEPENDENCIES = {
    "common": set(),
    "config": {"common"},
    "indicators": set(),
    "risk": {"common"},
    "execution": {"common", "config", "logging_", "risk"},
    "data": {"common", "config", "indicators", "logging_"},
    "db": {"common", "config", "execution", "logging_"},
}


def _source_package(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT)
    return relative.parts[0].removesuffix(".py")


def _internal_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)

        for module in modules:
            if not module.startswith("northstar_quant."):
                continue
            imports.add(module.split(".", maxsplit=2)[1])
    return imports


def _package_dependency_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for path in PACKAGE_ROOT.rglob("*.py"):
        source = _source_package(path)
        graph.setdefault(source, set())
        graph[source].update(
            target
            for target in _internal_imports(path)
            if target != source
        )
    return dict(graph)


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    visiting: list[str] = []
    active: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> list[str] | None:
        if node in active:
            start = visiting.index(node)
            return [*visiting[start:], node]
        if node in visited:
            return None

        active.add(node)
        visiting.append(node)
        for target in sorted(graph.get(node, set())):
            cycle = visit(target)
            if cycle is not None:
                return cycle
        visiting.pop()
        active.remove(node)
        visited.add(node)
        return None

    for package in sorted(graph):
        cycle = visit(package)
        if cycle is not None:
            return cycle
    return None


def test_core_package_dependencies_follow_boundaries():
    graph = _package_dependency_graph()

    violations = {
        package: sorted(graph.get(package, set()) - allowed)
        for package, allowed in ALLOWED_CORE_DEPENDENCIES.items()
        if graph.get(package, set()) - allowed
    }

    assert violations == {}, f"核心包存在反向依赖：{violations}"


def test_internal_package_dependency_graph_is_acyclic():
    graph = _package_dependency_graph()

    cycle = _find_cycle(graph)

    assert cycle is None, f"检测到包级循环依赖：{' -> '.join(cycle or [])}"
