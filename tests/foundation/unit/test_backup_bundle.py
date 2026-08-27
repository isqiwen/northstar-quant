"""受限备份包的完整性与秘密边界测试。"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pytest

import northstar_quant.foundation.backup.bundle as backup_bundle
from northstar_quant.foundation.backup.bundle import (
    BackupBundleError,
    BackupBundleSources,
    create_backup_bundle,
    verify_backup_bundle,
)


_BUNDLE_ID = "123e4567-e89b-12d3-a456-426614174000"
_NOW = datetime(2026, 8, 22, 8, 0, tzinfo=timezone.utc)


def _write(path: Path, content: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def _private_directory(path: Path) -> Path:
    """创建不依赖调用进程 umask 的私有目录。"""

    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        path.chmod(0o700)
    return path


def _sources(root: Path) -> BackupBundleSources:
    database_dump = _write(root / "database.dump", b"PGDMP\x01\x00\x00\x00")
    config_file = _write(
        root / "release" / "configs" / "app.yaml",
        "runtime:\n  storage_dir: /var/lib/northstar/storage\n",
    )
    ontology_dir = _private_directory(root / "release" / "ontology")
    _write(ontology_dir / "commodities.yaml", "version: v1\n")
    _write(ontology_dir / "nested" / "events.yaml", "version: v1\n")
    reports_dir = _private_directory(root / "reports")
    backtest_dir = _private_directory(reports_dir / "backtest")
    _write(
        backtest_dir / "run-1" / "manifest.json",
        '{"run_id":"run-1"}\n',
    )
    _write(backtest_dir / "run-1" / "report.pdf", b"not-backed-up")
    metadata_dir = _private_directory(root / "metadata")
    _write(
        metadata_dir / "current-release.json",
        '{"release_id":"release-20260822","artifact_sha256":"' + "a" * 64 + '"}\n',
    )
    _write(metadata_dir / "DEPLOY_ARTIFACT_META.txt", "revision=abc123\n")
    _write(metadata_dir / "systemd" / "northstar-quant.service", "[Service]\n")
    return BackupBundleSources(
        database_dump=database_dump,
        config_file=config_file,
        ontology_dir=ontology_dir,
        reports_dir=reports_dir,
        release_metadata_dir=metadata_dir,
    )


def _private_output_parent(root: Path) -> Path:
    """创建不依赖调用进程 umask 的私有备份输出目录。"""

    return _private_directory(root / "backups")


def _create(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    output_parent = _private_output_parent(root)
    return create_backup_bundle(
        _sources(root),
        output_parent=output_parent,
        bundle_id=_BUNDLE_ID,
        now=_NOW,
    )


def test_create_and_verify_backup_bundle_covers_only_allowlisted_assets(tmp_path: Path):
    bundle = _create(tmp_path)

    assert bundle.path.name == f"northstar-backup-{_BUNDLE_ID}"
    assert bundle.created_at == "2026-08-22T08:00:00Z"
    verified = verify_backup_bundle(bundle.path)
    assert verified == bundle

    manifest = json.loads((bundle.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format_version"] == 2
    archive_paths = {entry["archive_path"] for entry in manifest["entries"]}
    assert archive_paths == {
        "config/app.yaml",
        "ontology/commodities.yaml",
        "ontology/nested/events.yaml",
        "postgresql/database.dump",
        "release-metadata/DEPLOY_ARTIFACT_META.txt",
        "release-metadata/current-release.json",
        "release-metadata/systemd/northstar-quant.service",
        "run-manifests/run-1/manifest.json",
    }
    assert manifest["categories"] == {
        "config": 1,
        "ontology": 2,
        "postgresql": 1,
        "release_metadata": 3,
        "run_manifest": 1,
    }
    serialized = json.dumps(manifest, ensure_ascii=False)
    assert str(tmp_path) not in serialized
    assert ".env" not in serialized
    assert not (bundle.path / "runtime-state").exists()


@pytest.mark.skipif(os.name != "posix", reason="umask 仅在 POSIX 上影响目录权限")
def test_backup_fixture_is_independent_of_a_group_writable_umask(tmp_path: Path):
    previous_umask = os.umask(0o002)
    try:
        bundle = _create(tmp_path)
    finally:
        os.umask(previous_umask)

    assert verify_backup_bundle(bundle.path) == bundle


def test_legacy_simulated_broker_state_files_are_not_backup_inputs(tmp_path: Path):
    sources = _sources(tmp_path)
    _write(
        tmp_path / "storage" / "brokers" / "paper" / "paper-account" / "state.json",
        '{"legacy_note":"not-recoverable"}\n',
    )
    _write(
        tmp_path / "storage" / "brokers" / "ctp_sim" / "ctp-sim-account" / "state.json",
        '{"version":1,"positions":[]}\n',
    )

    output_parent = _private_output_parent(tmp_path)
    bundle = create_backup_bundle(
        sources,
        output_parent=output_parent,
        bundle_id=_BUNDLE_ID,
        now=_NOW,
    )

    manifest = json.loads((bundle.path / "manifest.json").read_text(encoding="utf-8"))
    assert all(not str(entry["archive_path"]).startswith("runtime-state/") for entry in manifest["entries"])


def test_bundle_target_is_never_overwritten(tmp_path: Path):
    first = _create(tmp_path)

    with pytest.raises(BackupBundleError, match="已存在"):
        create_backup_bundle(
            _sources(tmp_path),
            output_parent=tmp_path / "backups",
            bundle_id=_BUNDLE_ID,
            now=_NOW,
        )

    assert verify_backup_bundle(first.path) == first


def test_publish_primitive_never_replaces_a_late_existing_target(tmp_path: Path):
    stage = tmp_path / "stage"
    target = tmp_path / "target"
    stage.mkdir()
    _write(stage / "payload.txt", "new backup")
    target.mkdir()
    marker = _write(target / "keep.txt", "existing backup")

    with pytest.raises(BackupBundleError):
        backup_bundle._publish_stage_no_replace(stage, target)

    assert stage.is_dir()
    assert marker.read_text(encoding="utf-8") == "existing backup"
    assert not (target / "payload.txt").exists()


def test_failed_pre_publish_check_never_leaves_a_bundle(tmp_path: Path):
    sources = _sources(tmp_path)
    output_parent = _private_output_parent(tmp_path)
    checks: list[str] = []

    def reject_publication() -> None:
        checks.append("checked")
        raise BackupBundleError("runtime is no longer quiescent")

    with pytest.raises(BackupBundleError, match="quiescent"):
        create_backup_bundle(
            sources,
            output_parent=output_parent,
            bundle_id=_BUNDLE_ID,
            now=_NOW,
            pre_publish_check=reject_publication,
        )

    assert checks == ["checked"]
    assert not list(output_parent.iterdir())


def test_secret_like_config_is_rejected_without_publishing_partial_bundle(tmp_path: Path):
    sources = _sources(tmp_path)
    sources.config_file.write_text("database_password: not-for-backup\n", encoding="utf-8")  # secret-scan: allow; reason: disposable test fixture
    output_parent = _private_output_parent(tmp_path)

    with pytest.raises(BackupBundleError, match="疑似包含秘密"):
        create_backup_bundle(
            sources,
            output_parent=output_parent,
            bundle_id=_BUNDLE_ID,
            now=_NOW,
        )

    assert not list(output_parent.iterdir())


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("metadata/current-release.json", '{"database_password":"not-for-backup"}\n'),
    ],
)
def test_json_secret_like_assets_are_rejected_without_publishing_partial_bundle(
    tmp_path: Path,
    path: str,
    content: str,
):
    sources = _sources(tmp_path)
    _write(tmp_path / path, content)
    output_parent = _private_output_parent(tmp_path)

    with pytest.raises(BackupBundleError, match="疑似包含秘密"):
        create_backup_bundle(
            sources,
            output_parent=output_parent,
            bundle_id=_BUNDLE_ID,
            now=_NOW,
        )

    assert not list(output_parent.iterdir())


def test_verifier_detects_tampered_entry_and_unexpected_file(tmp_path: Path):
    bundle = _create(tmp_path)
    entry = bundle.path / "ontology" / "commodities.yaml"
    entry.write_text("version: v2\n", encoding="utf-8")

    with pytest.raises(BackupBundleError, match="哈希"):
        verify_backup_bundle(bundle.path)

    _create(tmp_path / "second")
    second_bundle = tmp_path / "second" / "backups" / f"northstar-backup-{_BUNDLE_ID}"
    _write(second_bundle / "unexpected.txt", "unexpected")

    with pytest.raises(BackupBundleError, match="文件集合"):
        verify_backup_bundle(second_bundle)

    _create(tmp_path / "third")
    third_bundle = tmp_path / "third" / "backups" / f"northstar-backup-{_BUNDLE_ID}"
    (third_bundle / "unexpected-empty-directory").mkdir()

    with pytest.raises(BackupBundleError, match="文件集合"):
        verify_backup_bundle(third_bundle)


def test_symlinked_input_is_rejected(tmp_path: Path):
    sources = _sources(tmp_path)
    outside = _write(tmp_path / "outside.yaml", "version: external\n")
    link = sources.ontology_dir / "linked.yaml"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("当前文件系统不允许创建符号链接。")
    output_parent = _private_output_parent(tmp_path)

    with pytest.raises(BackupBundleError, match="符号链接"):
        create_backup_bundle(
            sources,
            output_parent=output_parent,
            bundle_id=_BUNDLE_ID,
            now=_NOW,
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX 权限位只在 Linux/macOS 上可验证")
def test_group_or_other_writable_output_parent_is_rejected(tmp_path: Path):
    sources = _sources(tmp_path)
    output_parent = _private_output_parent(tmp_path)
    output_parent.chmod(0o777)

    with pytest.raises(BackupBundleError, match="group 或 other 写入"):
        create_backup_bundle(
            sources,
            output_parent=output_parent,
            bundle_id=_BUNDLE_ID,
            now=_NOW,
        )
