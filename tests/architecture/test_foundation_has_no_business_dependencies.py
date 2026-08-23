"""Foundation 必须保持技术中立。"""

from __future__ import annotations

from tests.architecture._imports import (
    APPLICATION_SCOPE,
    ARCHITECTURE_SCOPES,
    dynamic_imports,
    format_diagnostics,
    runtime_import_edges,
)


def test_foundation_has_no_runtime_business_or_application_dependencies() -> None:
    """Foundation 不能反向依赖业务领域，也不能把应用组合层当作快捷入口。"""

    forbidden_scopes = set(ARCHITECTURE_SCOPES - {"foundation"}) | {APPLICATION_SCOPE}
    violations = [
        edge
        for edge in runtime_import_edges()
        if edge.source_scope == "foundation" and edge.target_scope in forbidden_scopes
    ]

    assert not violations, (
        "Foundation 出现业务/组合层运行时依赖：\n"
        f"{format_diagnostics(violations)}"
    )


def test_source_tree_uses_no_dynamic_imports() -> None:
    """动态导入会绕过静态架构审查，因此在源码中一律禁止。"""

    imports = dynamic_imports()
    assert not imports, f"禁止使用动态导入：\n{format_diagnostics(imports)}"
