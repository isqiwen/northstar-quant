"""用 AST 采集 Northstar Quant 的运行时内部导入关系。

架构边界约束的是运行时依赖，而不是静态类型提示。仓库启用了
``from __future__ import annotations``，因此 ``if TYPE_CHECKING:`` 中的导入不会在
运行时加载，也不会形成领域依赖；采集器会明确跳过该分支，同时继续遍历 ``else``。
这让 Platform 可以保留精确的领域模型注解，而不反向依赖业务领域。
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from tests.helpers.paths import PROJECT_ROOT

PACKAGE_NAME = "northstar_quant"
PACKAGE_ROOT = PROJECT_ROOT / "src" / PACKAGE_NAME

BUSINESS_DOMAINS = frozenset(
    {
        "data_platform",
        "intelligence",
        "research",
        "portfolio_risk",
        "trading_execution",
        "platform",
    }
)
APPLICATION_SCOPE = "application"
ROOT_SCOPE = "root"


@dataclass(frozen=True)
class ImportEdge:
    """一个可定位到源码行的运行时内部导入。"""

    source_module: str
    source_path: Path
    line: int
    target_module: str

    @property
    def source_scope(self) -> str:
        return scope_for_module(self.source_module)

    @property
    def target_scope(self) -> str:
        return scope_for_module(self.target_module)

    def diagnostic(self) -> str:
        path = self.source_path.relative_to(PROJECT_ROOT)
        return (
            f"{path}:{self.line}: {self.source_scope} -> {self.target_scope} "
            f"({self.target_module})"
        )


@dataclass(frozen=True)
class DynamicImport:
    """动态导入调用；架构层禁止它用来绕开静态边界。"""

    source_path: Path
    line: int
    expression: str

    def diagnostic(self) -> str:
        return f"{self.source_path.relative_to(PROJECT_ROOT)}:{self.line}: {self.expression}"


def module_name_for_path(path: Path) -> str:
    """返回一个包内 Python 文件的可导入模块名。"""

    parts = list(path.relative_to(PACKAGE_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join((PACKAGE_NAME, *parts))


def scope_for_module(module: str) -> str:
    """将内部模块归类为六领域、应用组合层或包根。"""

    parts = module.split(".")
    if parts[0] != PACKAGE_NAME or len(parts) == 1:
        return ROOT_SCOPE
    first_child = parts[1]
    if first_child in BUSINESS_DOMAINS:
        return first_child
    if first_child == APPLICATION_SCOPE:
        return APPLICATION_SCOPE
    return ROOT_SCOPE


def _is_type_checking_guard(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute)
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
        and test.attr == "TYPE_CHECKING"
    )


def _package_for_path(path: Path) -> str:
    module = module_name_for_path(path)
    return module if path.name == "__init__.py" else module.rpartition(".")[0]


def _resolve_import_from(source_package: str, node: ast.ImportFrom) -> str | None:
    """解析绝对或相对 ``from ... import ...`` 的基础模块。"""

    if node.level == 0:
        return node.module

    package_parts = source_package.split(".")
    parent_levels = node.level - 1
    if parent_levels >= len(package_parts):
        return None
    base_parts = package_parts[: len(package_parts) - parent_levels]
    if node.module:
        base_parts.extend(node.module.split("."))
    return ".".join(base_parts)


def _is_internal_module(module: str | None) -> bool:
    return module == PACKAGE_NAME or bool(module and module.startswith(f"{PACKAGE_NAME}."))


class _RuntimeImportVisitor(ast.NodeVisitor):
    """跳过 TYPE_CHECKING 分支并收集运行时模块导入。"""

    def __init__(self, source_package: str, known_modules: frozenset[str]) -> None:
        self._source_package = source_package
        self._known_modules = known_modules
        self.modules: list[tuple[int, str]] = []
        self.dynamic_imports: list[tuple[int, str]] = []

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        if _is_type_checking_guard(node.test):
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            self._add_module(node.lineno, alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        base_module = _resolve_import_from(self._source_package, node)
        self._add_module(node.lineno, base_module)
        if base_module is None:
            return

        # ``from northstar_quant import research`` 和 ``from . import models``
        # 都可能导入一个子模块；仅当候选确实存在时才避免将普通符号误判为模块。
        for alias in node.names:
            candidate = f"{base_module}.{alias.name}"
            if alias.name != "*" and candidate in self._known_modules:
                self._add_module(node.lineno, candidate)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if isinstance(node.func, ast.Name) and node.func.id in {"__import__", "import_module"}:
            self.dynamic_imports.append((node.lineno, node.func.id))
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "import_module":
            self.dynamic_imports.append((node.lineno, "import_module"))
        self.generic_visit(node)

    def _add_module(self, line: int, module: str | None) -> None:
        if _is_internal_module(module):
            self.modules.append((line, module))


def _python_files() -> tuple[Path, ...]:
    return tuple(sorted(PACKAGE_ROOT.rglob("*.py")))


def _known_modules(paths: Iterable[Path]) -> frozenset[str]:
    return frozenset(module_name_for_path(path) for path in paths)


def runtime_import_edges() -> tuple[ImportEdge, ...]:
    """返回所有运行时内部导入，包含相对导入和可定位行号。"""

    paths = _python_files()
    known_modules = _known_modules(paths)
    edges: set[ImportEdge] = set()
    for path in paths:
        source_module = module_name_for_path(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _RuntimeImportVisitor(_package_for_path(path), known_modules)
        visitor.visit(tree)
        edges.update(
            ImportEdge(source_module, path, line, target_module)
            for line, target_module in visitor.modules
        )
    return tuple(sorted(edges, key=lambda edge: (str(edge.source_path), edge.line, edge.target_module)))


def dynamic_imports() -> tuple[DynamicImport, ...]:
    """返回所有运行时代码中的动态导入调用。"""

    paths = _python_files()
    known_modules = _known_modules(paths)
    imports: set[DynamicImport] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _RuntimeImportVisitor(_package_for_path(path), known_modules)
        visitor.visit(tree)
        imports.update(DynamicImport(path, line, expression) for line, expression in visitor.dynamic_imports)
    return tuple(sorted(imports, key=lambda item: (str(item.source_path), item.line, item.expression)))


def format_diagnostics(items: Iterable[ImportEdge | DynamicImport]) -> str:
    """形成 pytest 失败信息可直接定位的多行诊断。"""

    diagnostics = [item.diagnostic() for item in items]
    return "\n".join(f"- {diagnostic}" for diagnostic in diagnostics)
