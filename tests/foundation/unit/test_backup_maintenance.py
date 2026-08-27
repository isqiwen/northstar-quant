"""显式备份维护入口的本地安全前置条件测试。"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from tests.helpers.paths import PROJECT_ROOT


_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "maintenance" / "backup_bundle.py"
_SPEC = importlib.util.spec_from_file_location("backup_bundle_maintenance", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
backup_maintenance = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(backup_maintenance)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _private_directory(path: Path) -> Path:
    """创建不依赖调用进程 umask 的私有目录。"""

    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        path.chmod(0o700)
    return path


def _private_output_parent(root: Path, name: str) -> Path:
    """创建不依赖调用进程 umask 的私有备份输出目录。"""

    return _private_directory(root / name)


def test_release_metadata_snapshot_is_secret_free_and_contains_identity(tmp_path: Path):
    release = tmp_path / "release-20260823"
    _write(release / "DEPLOY_ARTIFACT_META.txt", "revision=abc123\n")
    _write(release / ".northstar" / "systemd" / "northstar-quant.service", "[Service]\n")
    _write(release / ".env", "NORTHSTAR_DATABASE_URL=must-not-copy\n")
    destination = tmp_path / "metadata"

    result = backup_maintenance._snapshot_release_metadata(release, destination)

    assert result == destination
    assert sorted(path.relative_to(destination).as_posix() for path in destination.rglob("*")) == [
        "DEPLOY_ARTIFACT_META.txt",
        "current-release.json",
        "systemd",
        "systemd/northstar-quant.service",
    ]
    metadata = json.loads((destination / "current-release.json").read_text(encoding="utf-8"))
    assert metadata["active_release_id"] == "release-20260823"
    assert ".env" not in json.dumps(metadata)


def test_backup_parent_must_not_overlap_any_input(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    output_parent = _private_output_parent(source, "backups")

    with pytest.raises(backup_maintenance.MaintenanceBackupError, match="不能与任何备份输入目录重叠"):
        backup_maintenance._assert_external_output_parent(output_parent, (source,))


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits require Linux validation")
def test_backup_parent_must_be_private_before_staging_a_database_dump(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    output_parent = _private_output_parent(tmp_path, "backup-output")
    output_parent.chmod(0o777)

    with pytest.raises(backup_maintenance.MaintenanceBackupError, match="group 或 other 写入"):
        backup_maintenance._assert_external_output_parent(output_parent, (source,))


def test_recovery_bundle_requires_fixed_service_to_be_inactive(monkeypatch):
    monkeypatch.setattr(
        backup_maintenance.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="active\n"),
    )

    with pytest.raises(backup_maintenance.MaintenanceBackupError, match="inactive"):
        backup_maintenance._assert_service_is_inactive()

    monkeypatch.setattr(
        backup_maintenance.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="inactive\n"),
    )
    backup_maintenance._assert_service_is_inactive()


def test_explicit_create_orchestrates_all_five_backup_categories_without_reading_env(
    tmp_path: Path,
    monkeypatch,
):
    release = tmp_path / "release-20260823"
    _write(release / "configs" / "app.yaml", "runtime:\n  storage_dir: /state\n")
    ontology_dir = _private_directory(release / "ontology")
    _write(ontology_dir / "events.yaml", "version: v1\n")
    _write(release / "DEPLOY_ARTIFACT_META.txt", "revision=abc123\n")
    _write(release / ".northstar" / "systemd" / "northstar-quant.service", "[Service]\n")
    reports = _private_directory(tmp_path / "reports")
    backtest_dir = _private_directory(reports / "backtest")
    _write(backtest_dir / "run-1" / "manifest.json", '{"run":"one"}\n')
    storage = tmp_path / "storage"
    _write(storage / "brokers" / "paper" / "paper-account" / "state.json", '{"version":1}\n')
    output_parent = _private_output_parent(tmp_path, "backup-output")
    monkeypatch.setattr(backup_maintenance, "_release_root", lambda: release)
    inactive_checks: list[str] = []
    monkeypatch.setattr(
        backup_maintenance,
        "_assert_service_is_inactive",
        lambda: inactive_checks.append("checked"),
    )
    def fake_load_settings(*, project_root: Path):
        assert project_root == release
        return SimpleNamespace(
            database_url="postgresql+psycopg://ignored:ignored@127.0.0.1:5432/northstar",  # secret-scan: allow; reason: disposable test fixture
            reports_dir=reports,
            storage_dir=storage,
        )

    monkeypatch.setattr(backup_maintenance, "load_settings", fake_load_settings)

    def fake_dump(database_url: str, *, output_path: Path):
        assert database_url.startswith("postgresql+psycopg://")
        output_path.write_bytes(b"PGDMP")
        return SimpleNamespace(path=output_path, size_bytes=5)

    monkeypatch.setattr(backup_maintenance, "create_postgresql_dump", fake_dump)
    args = SimpleNamespace(
        confirm_create="YES",
        confirm_runtime_quiesced="YES",
        output_parent=output_parent,
    )

    payload = backup_maintenance._create(args)

    assert payload["status"] == "created"
    bundle_dir = Path(str(payload["path"]))
    assert (bundle_dir / "postgresql" / "database.dump").is_file()
    assert (bundle_dir / "config" / "app.yaml").is_file()
    assert (bundle_dir / "ontology" / "events.yaml").is_file()
    assert (bundle_dir / "run-manifests" / "run-1" / "manifest.json").is_file()
    assert not (bundle_dir / "runtime-state").exists()
    assert (bundle_dir / "release-metadata" / "current-release.json").is_file()
    assert inactive_checks == ["checked", "checked"]
