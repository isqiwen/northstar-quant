"""P1-WP06 发布器的导入边界与时钟契约。"""

from __future__ import annotations

import ast

from tests.helpers.paths import PROJECT_ROOT


PUBLISHER_PATH = (
    PROJECT_ROOT
    / "src"
    / "northstar_quant"
    / "data"
    / "sources"
    / "publisher.py"
)


def test_publisher_is_the_explicit_controlled_adapter_to_immutable_store() -> None:
    source = PUBLISHER_PATH.read_text(encoding="utf-8")

    assert "class DataSourcePublisher" in source
    assert "NormalizedArtifact.from_deterministic_transform" in source
    assert "put_raw(" in source
    assert "put_normalized(" in source
    assert "put_dataset_version(" in source
    assert "require_quality_assessments=True" in source


def test_publisher_does_not_import_legacy_data_database_or_trading_layers() -> None:
    tree = ast.parse(PUBLISHER_PATH.read_text(encoding="utf-8"), filename=str(PUBLISHER_PATH))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    forbidden_prefixes = (
        "northstar_quant.application",
        "northstar_quant.data.artifacts.storage",
        "northstar_quant.data.sources.downloader",
        "northstar_quant.foundation.db",
        "northstar_quant.trading_execution",
    )
    assert not [
        module
        for module in imported_modules
        if module.startswith(forbidden_prefixes)
    ]


def test_publisher_requires_explicit_times_and_never_reads_wall_clock() -> None:
    source = PUBLISHER_PATH.read_text(encoding="utf-8")

    for forbidden in (
        "datetime.now",
        "datetime.utcnow",
        "date.today",
        "time.time",
        "time.monotonic",
    ):
        assert forbidden not in source
    assert "released_at" in source
    assert "authorized_at" in source
    assert "checked_at" in source
    assert "decision_at" in source
