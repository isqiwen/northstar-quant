"""六领域运行时依赖的总边界测试。"""

from __future__ import annotations

import ast

from tests.architecture._imports import (
    PACKAGE_ROOT,
    ROOT_SCOPE,
    ImportEdge,
    _resolve_import_from,
    format_diagnostics,
    runtime_import_edges,
)


ALLOWED_RUNTIME_TARGETS = {
    "data": {"data", "foundation", ROOT_SCOPE},
    "intelligence": {"intelligence", "data", "foundation", ROOT_SCOPE},
    "research": {"research", "data", "intelligence", "foundation", ROOT_SCOPE},
    "portfolio_risk": {"portfolio_risk", "research", "foundation", ROOT_SCOPE},
    "trading_execution": {"trading_execution", "portfolio_risk", "foundation", ROOT_SCOPE},
    "foundation": {"foundation", ROOT_SCOPE},
    "application": {
        "application",
        "data",
        "intelligence",
        "research",
        "portfolio_risk",
        "trading_execution",
        "foundation",
        ROOT_SCOPE,
    },
    ROOT_SCOPE: {"application", ROOT_SCOPE},
}


def boundary_violations(edges: tuple[ImportEdge, ...] | list[ImportEdge]) -> list[ImportEdge]:
    """返回违反六领域运行时边界的边，供回归和自证明测试复用。"""

    return [
        edge
        for edge in edges
        if edge.target_scope not in ALLOWED_RUNTIME_TARGETS[edge.source_scope]
    ]


def test_runtime_imports_follow_six_domain_policy() -> None:
    """任何运行时导入都必须遵循单向业务闭环。"""

    violations = boundary_violations(runtime_import_edges())

    assert not violations, (
        "发现违反六领域依赖边界的运行时导入：\n"
        f"{format_diagnostics(violations)}"
    )


def test_foundation_to_data_is_reported_as_a_violation() -> None:
    """防止测试意外弱化为只观察当前恰好干净的源码树。"""

    forbidden = ImportEdge(
        "northstar_quant.foundation.db.synthetic",
        PACKAGE_ROOT / "foundation" / "db" / "synthetic.py",
        17,
        "northstar_quant.data.artifacts.storage",
    )

    assert boundary_violations([forbidden]) == [forbidden]


def test_relative_import_fixture_resolves_and_is_rejected_across_boundary() -> None:
    """相对导入不能成为 Foundation 绕过 Data 边界的途径。"""

    node = ast.parse("from ...data import artifacts").body[0]
    assert isinstance(node, ast.ImportFrom)
    target = _resolve_import_from("northstar_quant.foundation.db", node)
    assert target == "northstar_quant.data"

    forbidden = ImportEdge(
        "northstar_quant.foundation.db.synthetic",
        PACKAGE_ROOT / "foundation" / "db" / "synthetic.py",
        node.lineno,
        target,
    )
    assert boundary_violations([forbidden]) == [forbidden]
