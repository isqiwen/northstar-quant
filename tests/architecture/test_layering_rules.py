"""逐领域验证数据到执行的单向分层。"""

from __future__ import annotations

import pytest

from tests.architecture._imports import ROOT_SCOPE, format_diagnostics, runtime_import_edges
from tests.architecture.test_dependency_boundaries import ALLOWED_RUNTIME_TARGETS


@pytest.mark.parametrize(
    "domain",
    ["data_platform", "intelligence", "research", "portfolio_risk", "trading_execution"],
)
def test_business_domain_obeys_its_allowed_lower_layers(domain: str) -> None:
    """每个领域只能依赖自身、许可的下游事实层和技术平台。"""

    allowed = ALLOWED_RUNTIME_TARGETS[domain]
    violations = [
        edge
        for edge in runtime_import_edges()
        if edge.source_scope == domain and edge.target_scope not in allowed
    ]

    assert not violations, (
        f"{domain} 违反分层规则（允许目标：{', '.join(sorted(allowed))}）：\n"
        f"{format_diagnostics(violations)}"
    )


def test_no_business_domain_imports_application_composition_layer() -> None:
    """组合层只能向下调用领域，领域不可反向导入 application。"""

    violations = [
        edge
        for edge in runtime_import_edges()
        if edge.source_scope in ALLOWED_RUNTIME_TARGETS
        and edge.source_scope not in {"application", ROOT_SCOPE}
        and edge.target_scope == "application"
    ]

    assert not violations, f"业务领域反向导入 application：\n{format_diagnostics(violations)}"
