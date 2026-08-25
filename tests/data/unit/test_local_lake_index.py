"""SQLite Local-tools Lake manifest index 的隔离、故障和并发测试。"""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from pathlib import Path
import sqlite3

import polars as pl
import pytest

from northstar_quant.data.lake import (
    LakeLocalIndexCorruptionError,
    LakeLocalIndexError,
    LakeManifestLocalIndex,
)
from northstar_quant.foundation.config import settings as settings_module
from tests.helpers.historical_lake import build_materialized_bars_lake


def test_local_sqlite_index_rebuilds_and_lists_only_verified_lake_manifest_metadata(tmp_path: Path):
    fixture = build_materialized_bars_lake(tmp_path)
    index = LakeManifestLocalIndex(tmp_path / "storage" / "local-tools")

    rebuild = index.rebuild(fixture.lake_store)
    entries = index.list_entries()

    assert rebuild.entry_count == 1
    assert rebuild.database_path == tmp_path / "storage" / "local-tools" / "lake-manifest-index.sqlite3"
    assert rebuild.database_path.is_file()
    assert entries[0].reference == fixture.materialized.verified.manifest.reference
    assert entries[0].manifest_relative_path.startswith("datasets/bars/")
    assert not entries[0].manifest_relative_path.startswith("/")
    assert entries[0].upstream_dataset_version_hash == fixture.dataset_version.version_hash
    assert index.list_entries(kind=entries[0].reference.kind) == entries
    assert index.list_entries(dataset_id=entries[0].reference.dataset_id) == entries


def test_local_sqlite_index_from_settings_uses_only_the_tool_owned_storage_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    storage_dir = tmp_path / "runtime-storage"
    monkeypatch.setattr(
        settings_module,
        "get_settings",
        lambda: SimpleNamespace(storage_dir=storage_dir, local_tools_dir=storage_dir / "local-tools"),
    )

    index = LakeManifestLocalIndex.from_settings()

    assert index.root == storage_dir / "local-tools"
    assert index.database_path == storage_dir / "local-tools" / "lake-manifest-index.sqlite3"


def test_local_sqlite_index_keeps_previous_generation_when_lake_verification_fails(tmp_path: Path):
    fixture = build_materialized_bars_lake(tmp_path)
    index = LakeManifestLocalIndex(tmp_path / "local-tools")
    index.rebuild(fixture.lake_store)
    before = index.list_entries()
    partition = fixture.materialized.verified.parquet_paths[0]
    pl.read_parquet(partition).with_columns((pl.col("price") + 1).alias("price")).write_parquet(
        partition
    )

    with pytest.raises(LakeLocalIndexError, match="无法验证"):
        index.rebuild(fixture.lake_store)

    assert index.list_entries() == before


def test_local_sqlite_index_serializes_concurrent_rebuilds_without_duplicate_latest_entries(
    tmp_path: Path,
):
    fixture = build_materialized_bars_lake(tmp_path)
    root = tmp_path / "local-tools"

    def rebuild_once(_: int) -> int:
        return LakeManifestLocalIndex(root).rebuild(fixture.lake_store).generation_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        generations = tuple(executor.map(rebuild_once, range(2)))

    entries = LakeManifestLocalIndex(root).list_entries()
    assert len(set(generations)) == 2
    assert len(entries) == 1
    assert entries[0].reference == fixture.materialized.verified.manifest.reference


def test_explicit_rebuild_quarantines_only_corrupt_tool_owned_sqlite_file(tmp_path: Path):
    fixture = build_materialized_bars_lake(tmp_path)
    index = LakeManifestLocalIndex(tmp_path / "local-tools")
    index.database_path.write_bytes(b"this is not a sqlite database")

    with pytest.raises(LakeLocalIndexCorruptionError, match="显式执行"):
        index.list_entries()
    assert index.database_path.read_bytes() == b"this is not a sqlite database"

    rebuild = index.rebuild(fixture.lake_store)
    quarantined = tuple(index.root.glob("lake-manifest-index.sqlite3.corrupt-*"))

    assert rebuild.entry_count == 1
    assert index.list_entries()[0].reference == fixture.materialized.verified.manifest.reference
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"this is not a sqlite database"


def test_explicit_rebuild_quarantines_an_incompatible_tool_owned_sqlite_schema(tmp_path: Path):
    fixture = build_materialized_bars_lake(tmp_path)
    index = LakeManifestLocalIndex(tmp_path / "local-tools")
    connection = sqlite3.connect(index.database_path)
    try:
        connection.execute("CREATE TABLE lake_manifest_index_rebuilds (wrong_column TEXT)")
        connection.commit()
    finally:
        connection.close()

    rebuild = index.rebuild(fixture.lake_store)
    quarantined = tuple(index.root.glob("lake-manifest-index.sqlite3.corrupt-*"))

    assert rebuild.entry_count == 1
    assert len(quarantined) == 1
    assert index.list_entries()[0].reference == fixture.materialized.verified.manifest.reference


def test_local_sqlite_index_rejects_symlinked_root_or_database_file(tmp_path: Path):
    target_root = tmp_path / "target-root"
    target_root.mkdir()
    root_link = tmp_path / "local-tools-link"
    try:
        root_link.symlink_to(target_root, target_is_directory=True)
    except OSError:
        pytest.skip("当前环境不允许创建符号链接")

    with pytest.raises(LakeLocalIndexError, match="符号链接"):
        LakeManifestLocalIndex(root_link)

    index = LakeManifestLocalIndex(tmp_path / "local-tools")
    external_database = tmp_path / "external.sqlite3"
    external_database.write_bytes(b"outside")
    index.database_path.symlink_to(external_database)

    with pytest.raises(LakeLocalIndexError, match="普通 tool-owned 文件"):
        index.list_entries()
