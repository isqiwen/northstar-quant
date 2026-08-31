"""Linux-only runtime storage and backup fail-closed boundaries."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from northstar_quant.data.artifacts.immutable_store import ArtifactStore
from northstar_quant.data.artifacts.output_cleanup import cleanup_output_files
from northstar_quant.data.lake.local_index import LakeManifestLocalIndex
from northstar_quant.data.lake.store import ParquetLakeStore
from northstar_quant.foundation.backup.bundle import BackupBundleSources, create_backup_bundle
from northstar_quant.foundation.backup.postgresql import create_postgresql_dump
from northstar_quant.foundation.backup.restore_drill import run_test_postgresql_restore_drill
from northstar_quant.foundation.config.output_retention import OutputRetentionPolicy
from northstar_quant.foundation import platform_support
from northstar_quant.foundation.platform_support import PlatformSupportError


def _unsupported_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform_support.platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform_support.platform, "machine", lambda: "AMD64")


def test_storage_roots_fail_before_they_can_be_created_on_an_unsupported_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _unsupported_runtime(monkeypatch)
    artifact_root = tmp_path / "artifacts"
    lake_root = tmp_path / "lake"
    index_root = tmp_path / "local-tools"

    with pytest.raises(PlatformSupportError, match="Linux x86_64"):
        ArtifactStore(artifact_root)
    with pytest.raises(PlatformSupportError, match="Linux x86_64"):
        ParquetLakeStore(lake_root)
    with pytest.raises(PlatformSupportError, match="Linux x86_64"):
        LakeManifestLocalIndex(index_root)

    assert not artifact_root.exists()
    assert not lake_root.exists()
    assert not index_root.exists()


def test_output_cleanup_does_not_delete_files_on_an_unsupported_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _unsupported_runtime(monkeypatch)
    downloads_dir = tmp_path / "downloads"
    expired = downloads_dir / "expired.parquet"
    expired.parent.mkdir()
    expired.write_bytes(b"unchanged")
    policy = OutputRetentionPolicy(
        enabled=True,
        download_cache_retention_days=1,
        temporary_file_retention_days=1,
    )

    with pytest.raises(PlatformSupportError, match="Linux x86_64"):
        cleanup_output_files(
            policy,
            apply=True,
            downloads_dir=downloads_dir,
            protected_roots=(),
            now=datetime(2026, 8, 30, tzinfo=timezone.utc),
        )

    assert expired.read_bytes() == b"unchanged"


def test_backup_and_restore_operations_fail_before_creating_outputs_on_an_unsupported_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _unsupported_runtime(monkeypatch)
    bundle_parent = tmp_path / "backup-output"
    dump_path = tmp_path / "database.dump"
    drill_workspace = tmp_path / "restore-workspace"
    sources = BackupBundleSources(
        database_dump=tmp_path / "input.dump",
        config_file=tmp_path / "app.yaml",
        ontology_dir=tmp_path / "ontology",
        reports_dir=tmp_path / "reports",
        release_metadata_dir=tmp_path / "release-metadata",
    )

    with pytest.raises(PlatformSupportError, match="Linux x86_64"):
        create_backup_bundle(sources, output_parent=bundle_parent)
    with pytest.raises(PlatformSupportError, match="Linux x86_64"):
        create_postgresql_dump(
            "postgresql+psycopg://user:password@127.0.0.1:5432/northstar",  # secret-scan: allow; reason: unsupported-platform boundary never submits this disposable DSN.
            output_path=dump_path,
        )
    with pytest.raises(PlatformSupportError, match="Linux x86_64"):
        run_test_postgresql_restore_drill(
            "postgresql+psycopg://user@127.0.0.1:5432/northstar_test",
            workspace_dir=drill_workspace,
        )

    assert not bundle_parent.exists()
    assert not dump_path.exists()
    assert not drill_workspace.exists()
