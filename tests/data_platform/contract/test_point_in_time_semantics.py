"""P1-WP07 的公开 PIT 边界契约。"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from northstar_quant.data_platform.market.pit import MarketDataPITSelector
from tests.helpers.paths import PROJECT_ROOT


PIT_MODULE = PROJECT_ROOT / "src" / "northstar_quant" / "data_platform" / "market" / "pit.py"


def test_pit_selector_requires_explicit_as_of_without_clock_or_latest_fallback() -> None:
    """消费者必须传 simulation/as-of 时点，不能回退到系统时钟或全局 latest。"""

    signature = inspect.signature(MarketDataPITSelector.select)
    assert signature.parameters["as_of"].default is inspect.Signature.empty

    source = PIT_MODULE.read_text(encoding="utf-8")
    forbidden = (
        "datetime.now(",
        "datetime.utcnow(",
        "date.today(",
        "time.time(",
        "load_profile_market_data",
        "profile_market_data_path",
        "data_manifest_v3",
    )
    assert not [token for token in forbidden if token in source]


def test_pit_module_only_depends_on_data_platform_and_standard_library() -> None:
    """PIT 选择器不得反向接入 Research、Trading、Application 或 legacy storage 投影。"""

    tree = ast.parse(PIT_MODULE.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    forbidden_prefixes = (
        "northstar_quant.application",
        "northstar_quant.research",
        "northstar_quant.trading_execution",
        "northstar_quant.data_platform.artifacts.storage",
        "northstar_quant.data_platform.sources.downloader",
    )
    assert not [
        imported
        for imported in imports
        if imported.startswith(forbidden_prefixes)
    ]


def test_pit_source_file_is_a_real_module_under_market_boundary() -> None:
    """防止实现被迁回泛化 data/storage 路径而绕开行级时间契约。"""

    assert PIT_MODULE.is_file()
    assert Path(MarketDataPITSelector.__module__.replace(".", "/")).name == "pit"
