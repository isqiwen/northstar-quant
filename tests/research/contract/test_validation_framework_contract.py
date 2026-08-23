"""P2-WP06 验证框架必须保持纯研究边界。"""

from __future__ import annotations

import ast
from pathlib import Path


MODULE = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "northstar_quant"
    / "research"
    / "validation"
    / "framework.py"
)


def test_validation_framework_does_not_import_application_broker_or_database() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    forbidden = (
        "northstar_quant.application",
        "northstar_quant.trading_execution",
        "northstar_quant.platform.db",
    )
    assert not any(
        imported == prefix or imported.startswith(prefix + ".")
        for imported in imports
        for prefix in forbidden
    )
