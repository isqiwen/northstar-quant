"""P1-WP02 不可变制品库的边界契约。"""

from __future__ import annotations

import ast

from tests.helpers.paths import PROJECT_ROOT


STORE_PATH = (
    PROJECT_ROOT / "src" / "northstar_quant" / "data" / "artifacts" / "immutable_store.py"
)
ARTIFACTS_INIT = STORE_PATH.parent / "__init__.py"


def test_immutable_store_isolated_from_legacy_mutable_storage_and_database() -> None:
    """不可变库不能偷偷复用可覆盖 cache/market 或数据库基础设施。"""

    source = STORE_PATH.read_text(encoding="utf-8")
    forbidden_fragments = (
        "os.replace",
        "save_parquet",
        "save_json",
        "sqlalchemy",
        "alembic",
        "foundation.db",
    )

    assert not [fragment for fragment in forbidden_fragments if fragment in source]
    assert "os.link(" in source
    assert "src_dir_fd=" in source
    assert "dst_dir_fd=" in source
    assert "O_NOFOLLOW" in source


def test_immutable_store_permanent_paths_are_only_hash_addressed() -> None:
    source = STORE_PATH.read_text(encoding="utf-8")

    for method_name in (
        "blob_path",
        "snapshot_path",
        "dataset_manifest_path",
        "normalization_binding_path",
        "lineage_path",
    ):
        assert f"def {method_name}(" in source
    assert 'get_settings().storage_dir / "artifacts"' in source
    assert "dataset_id /" not in source
    assert "artifact_id /" not in source


def test_artifacts_package_does_not_reexport_store_and_create_domain_import_cycle() -> None:
    """data_domain 导入 fingerprints 时会先执行 package init，store 必须显式导入。"""

    module = ast.parse(ARTIFACTS_INIT.read_text(encoding="utf-8"), filename=str(ARTIFACTS_INIT))
    imported_modules = {
        node.module
        for node in ast.walk(module)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "northstar_quant.data.artifacts.immutable_store" not in imported_modules
    assert "ArtifactStore" not in ARTIFACTS_INIT.read_text(encoding="utf-8")
