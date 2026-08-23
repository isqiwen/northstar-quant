"""P8 Intelligence-to-Research feature-projection architecture contracts.

The Intelligence-side projection is an evidence-only, P4-owned boundary.  It
may prepare a typed projection for P1/P2 consumption, but must never import
Research or any execution/control domain.  P2 canonical feature definitions
remain schema-only consumers and must not pull raw P4 Event/Document state or
the application composition root into Research.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from tests.architecture._imports import ImportEdge, PACKAGE_ROOT, format_diagnostics, runtime_import_edges


P4_FEATURE_PROJECTION_PACKAGE_MODULE = "northstar_quant.intelligence.feature_projection"
P4_FEATURE_PROJECTION_MODULE = "northstar_quant.intelligence.feature_projection.projection"
P4_FEATURE_PROJECTION_PACKAGE_PATH = (
    PACKAGE_ROOT / "intelligence" / "feature_projection" / "__init__.py"
)
P4_FEATURE_PROJECTION_PATH = (
    PACKAGE_ROOT / "intelligence" / "feature_projection" / "projection.py"
)
P4_FEATURE_PROJECTION_SOURCE_PATHS = (
    P4_FEATURE_PROJECTION_PACKAGE_PATH,
    P4_FEATURE_PROJECTION_PATH,
)
P2_INTELLIGENCE_CANONICAL_MODULE = "northstar_quant.research.features.intelligence.canonical"
P2_INTELLIGENCE_CANONICAL_PATH = (
    PACKAGE_ROOT / "research" / "features" / "intelligence" / "canonical.py"
)
APPLICATION_PROJECTION_MODULE = "northstar_quant.application.intelligence_feature_projection"
APPLICATION_PROJECTION_EVIDENCE_MODULE = (
    "northstar_quant.application.intelligence_feature_projection_evidence"
)
APPLICATION_PROJECTION_PATH = (
    PACKAGE_ROOT / "application" / "intelligence_feature_projection.py"
)
APPLICATION_PROJECTION_EVIDENCE_PATH = (
    PACKAGE_ROOT / "application" / "intelligence_feature_projection_evidence.py"
)
APPLICATION_PROJECTION_SOURCE_PATHS = (
    APPLICATION_PROJECTION_PATH,
    APPLICATION_PROJECTION_EVIDENCE_PATH,
)

_FORBIDDEN_P4_DOMAIN_PREFIXES = (
    "northstar_quant.application",
    "northstar_quant.portfolio_risk",
    "northstar_quant.research",
    "northstar_quant.trading_execution",
)
_P4_ALLOWED_INTERNAL_IMPORT_PREFIXES = (
    "northstar_quant.data_platform",
    "northstar_quant.intelligence",
)
_FORBIDDEN_P2_IMPORT_PREFIXES = (
    "northstar_quant.application",
    "northstar_quant.portfolio_risk",
    "northstar_quant.trading_execution",
)
_FORBIDDEN_APPLICATION_IMPORT_PREFIXES = (
    "northstar_quant.portfolio_risk",
    "northstar_quant.trading_execution",
)
_FORBIDDEN_IO_IMPORT_ROOTS = frozenset(
    {
        "aiohttp",
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
_FORBIDDEN_IO_OR_CONTROL_CALLS = frozenset(
    {
        "__import__",
        "approve",
        "cancel_order",
        "delete",
        "deploy",
        "eval",
        "exec",
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
_FORBIDDEN_PUBLIC_SURFACE_FRAGMENTS = (
    "action",
    "approve",
    "broker",
    "command",
    "control",
    "deploy",
    "execution",
    "order",
    "portfolio",
    "promot",
    "recover",
    "restore",
    "strategy",
    "submit",
    "target",
    "trade",
    "trading",
)
_SAFE_PUBLIC_FIELD_NAMES = frozenset({"eligible_for_trading"})
_APPLICATION_FORBIDDEN_PUBLIC_SURFACE_FRAGMENTS = (
    "action",
    "approve",
    "broker",
    "command",
    "control",
    "deploy",
    "execution",
    "order",
    "portfolio",
    "promot",
    "recover",
    "restore",
    "strategy",
    "submit",
    "target",
    "trade",
    "trading",
)


def _is_type_checking_guard(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute)
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
        and test.attr == "TYPE_CHECKING"
    )


class _RuntimeSourceVisitor(ast.NodeVisitor):
    """Collect direct runtime imports and operations, excluding type-only code."""

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


@dataclass(frozen=True)
class _PublicSurface:
    fields: tuple[str, ...]
    behavior_names: tuple[str, ...]


def _source_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _source_dependencies(paths: Iterable[Path]) -> _RuntimeSourceVisitor:
    visitor = _RuntimeSourceVisitor()
    for path in paths:
        visitor.visit(_source_tree(path))
    return visitor


def _public_surface(path: Path) -> _PublicSurface:
    fields: list[str] = []
    behavior_names: list[str] = []
    for node in _source_tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            behavior_names.append(node.name)
        if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
            continue
        for member in node.body:
            if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
                fields.append(member.target.id)
            elif isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and not member.name.startswith("_"):
                behavior_names.append(member.name)
    return _PublicSurface(tuple(fields), tuple(behavior_names))


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


def _has_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def _public_surface_violations(surface: _PublicSurface) -> list[str]:
    return sorted(
        name
        for name in (*surface.fields, *surface.behavior_names)
        if name not in _SAFE_PUBLIC_FIELD_NAMES
        and any(fragment in name.casefold() for fragment in _FORBIDDEN_PUBLIC_SURFACE_FRAGMENTS)
    )


def test_feature_projection_boundary_modules_are_declared() -> None:
    missing = [path for path in P4_FEATURE_PROJECTION_SOURCE_PATHS if not path.is_file()]
    assert not missing, (
        "P8 must provide the P4-owned intelligence.feature_projection public package and "
        f"implementation: {missing}"
    )
    assert P2_INTELLIGENCE_CANONICAL_PATH.is_file(), (
        "P2 intelligence canonical feature definitions must remain declared"
    )
    missing_application = [
        path for path in APPLICATION_PROJECTION_SOURCE_PATHS if not path.is_file()
    ]
    assert not missing_application, (
        "P8 must provide the application-owned immutable evidence verification and "
        f"P4-to-P1 publisher boundary: {missing_application}"
    )


def test_p4_feature_projection_has_no_research_application_risk_or_trading_dependency() -> None:
    reachable = _reachable_edges(P4_FEATURE_PROJECTION_PACKAGE_MODULE, runtime_import_edges())
    violations = [
        edge
        for edge in reachable
        if any(_has_prefix(edge.target_module, prefix) for prefix in _FORBIDDEN_P4_DOMAIN_PREFIXES)
    ]

    assert not violations, (
        "P4 feature projection is evidence-only and cannot reach Research, application, "
        "portfolio/risk, or trading/execution:\n"
        f"{format_diagnostics(violations)}"
    )


def test_p4_feature_projection_has_no_io_dynamic_or_control_capability() -> None:
    visitor = _source_dependencies(P4_FEATURE_PROJECTION_SOURCE_PATHS)
    forbidden_internal_imports = sorted(
        imported
        for imported in visitor.imports
        if imported.startswith("northstar_quant.")
        and not any(
            _has_prefix(imported, prefix) for prefix in _P4_ALLOWED_INTERNAL_IMPORT_PREFIXES
        )
    )
    forbidden_imports = sorted(
        imported
        for imported in visitor.imports
        if imported.split(".", maxsplit=1)[0] in _FORBIDDEN_IO_IMPORT_ROOTS
    )
    forbidden_calls = sorted(visitor.calls.intersection(_FORBIDDEN_IO_OR_CONTROL_CALLS))

    assert not forbidden_internal_imports, (
        "P4 feature projection can use only Intelligence and stable Data Platform contracts; "
        "it cannot acquire an internal platform/control capability: "
        f"{forbidden_internal_imports}"
    )
    assert not forbidden_imports, (
        "P4 feature projection cannot open filesystem, process, database, or network "
        f"capabilities: {forbidden_imports}"
    )
    assert not forbidden_calls, (
        "P4 feature projection is not an I/O, publish, approval, order, trading, or "
        f"recovery boundary: {forbidden_calls}"
    )


def test_p2_intelligence_canonical_does_not_import_raw_p4_or_application_state() -> None:
    visitor = _source_dependencies((P2_INTELLIGENCE_CANONICAL_PATH,))
    violations = sorted(
        imported
        for imported in visitor.imports
        if (
            any(_has_prefix(imported, prefix) for prefix in _FORBIDDEN_P2_IMPORT_PREFIXES)
            or (
                _has_prefix(imported, "northstar_quant.intelligence")
                and imported != P4_FEATURE_PROJECTION_PACKAGE_MODULE
            )
        )
    )

    assert not violations, (
        "P2 intelligence canonical definitions may consume only the public P4 feature_projection "
        "schema; they cannot import Event/Document/raw state, application, risk, or trading: "
        f"{violations}"
    )
    assert P4_FEATURE_PROJECTION_PACKAGE_MODULE in visitor.imports, (
        "P2 intelligence canonical definitions must consume the shared public P4 "
        "feature_projection schema rather than duplicate it"
    )


def test_application_projection_cannot_reach_risk_or_trading_domains() -> None:
    reachable = _reachable_edges(APPLICATION_PROJECTION_MODULE, runtime_import_edges())
    violations = [
        edge
        for edge in reachable
        if any(
            _has_prefix(edge.target_module, prefix)
            for prefix in _FORBIDDEN_APPLICATION_IMPORT_PREFIXES
        )
    ]

    assert not violations, (
        "The P4-to-P1 application seam may validate and publish research evidence only; "
        "it cannot reach Portfolio/Risk or Trading/Execution:\n"
        f"{format_diagnostics(violations)}"
    )


def test_application_projection_public_surface_exposes_no_order_or_risk_capability() -> None:
    violations = {
        str(path.relative_to(PACKAGE_ROOT)): sorted(
            name
            for name in (*_public_surface(path).fields, *_public_surface(path).behavior_names)
            if name not in _SAFE_PUBLIC_FIELD_NAMES
            and any(
                fragment in name.casefold()
                for fragment in _APPLICATION_FORBIDDEN_PUBLIC_SURFACE_FRAGMENTS
            )
        )
        for path in APPLICATION_PROJECTION_SOURCE_PATHS
    }
    violations = {label: names for label, names in violations.items() if names}

    assert not violations, (
        "The P4-to-P1 application seam cannot expose target/order/broker/risk/trading "
        f"capability: {violations}"
    )


def test_projection_and_canonical_public_surfaces_expose_no_control_or_trading_fields() -> None:
    violations = {
        label: _public_surface_violations(_public_surface(path))
        for label, path in (
            ("P4 feature projection", P4_FEATURE_PROJECTION_PATH),
            ("P2 intelligence canonical", P2_INTELLIGENCE_CANONICAL_PATH),
        )
    }
    violations = {label: names for label, names in violations.items() if names}

    assert not violations, (
        "Feature projection is research evidence only.  Public DTO fields and behavior names "
        "cannot provide target/order/broker/trading/approval/promotion/control capability: "
        f"{violations}"
    )
