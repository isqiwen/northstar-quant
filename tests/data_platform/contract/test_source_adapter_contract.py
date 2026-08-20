"""P1-WP06 数据源协议的公共 API 与隔离边界契约。"""

from __future__ import annotations

import ast
from dataclasses import is_dataclass

import northstar_quant.data_platform.sources as sources

from tests.helpers.paths import PROJECT_ROOT


PROTOCOL_PATH = (
    PROJECT_ROOT
    / "src"
    / "northstar_quant"
    / "data_platform"
    / "sources"
    / "protocol.py"
)


def test_source_protocol_exports_only_explicit_public_values_and_operations() -> None:
    expected = {
        "AdapterMetadata",
        "CANONICAL_NORMALIZED_FORMAT",
        "DataSourceAdapter",
        "DataSourceProtocolError",
        "NormalizedTable",
        "PublicationAuthorization",
        "PublicationPurpose",
        "PublicationScope",
        "RawCapture",
        "SourceFetchRequest",
        "build_publication_authorization",
        "validate_publication_authorization",
    }

    assert expected <= set(sources.__all__)
    for name in (
        "AdapterMetadata",
        "NormalizedTable",
        "PublicationAuthorization",
        "PublicationScope",
        "RawCapture",
        "SourceFetchRequest",
    ):
        model = getattr(sources, name)
        assert is_dataclass(model)
        assert model.__dataclass_params__.frozen


def test_protocol_stays_pure_and_does_not_import_legacy_publisher_or_trading_layers() -> None:
    tree = ast.parse(PROTOCOL_PATH.read_text(encoding="utf-8"))
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
        "northstar_quant.data_platform.artifacts.immutable_store",
        "northstar_quant.data_platform.artifacts.storage",
        "northstar_quant.data_platform.sources.downloader",
        "northstar_quant.trading_execution",
        "northstar_quant.platform.db",
    )
    assert not [
        module
        for module in imported_modules
        if module.startswith(forbidden_prefixes)
    ]

    source = PROTOCOL_PATH.read_text(encoding="utf-8")
    for forbidden in ("datetime.now", "date.today", "os.environ", "requests", "httpx"):
        assert forbidden not in source
    assert "NormalizedArtifact.from_deterministic_transform" in source
