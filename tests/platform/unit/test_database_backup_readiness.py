"""PostgreSQL 备份/恢复证据只读评估测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path, PurePosixPath

import pytest

from northstar_quant.platform.config.database_backup_readiness import (
    DatabaseBackupReadinessPolicy,
)
from northstar_quant.platform.observability.monitoring.database_backup_readiness import (
    evaluate_database_backup_readiness,
)


_NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
_ARTIFACT_ID = "123e4567-e89b-12d3-a456-426614174000"


def _policy(*, enabled: bool = True) -> DatabaseBackupReadinessPolicy:
    return DatabaseBackupReadinessPolicy(
        enabled=enabled,
        evidence_relative_path=PurePosixPath("operations/database-backup/readiness.json"),
        max_backup_age_hours=26,
        max_restore_verification_age_days=31,
    )


def _evidence(
    *,
    backup_at: datetime | None = None,
    restore_at: datetime | None = None,
) -> dict[str, object]:
    backup_completed_at = backup_at or _NOW - timedelta(hours=1)
    restore_completed_at = restore_at or _NOW - timedelta(minutes=30)
    return {
        "version": 1,
        "backup": {
            "completed_at": _timestamp(backup_completed_at),
            "artifact_id": _ARTIFACT_ID,
            "artifact_sha256": "a" * 64,
            "artifact_size_bytes": 1024,
        },
        "restore_drill": {
            "completed_at": _timestamp(restore_completed_at),
            "artifact_id": _ARTIFACT_ID,
            "status": "passed",
            "method": "isolated_postgresql_restore",
        },
    }


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_evidence(storage_dir: Path, payload: dict[str, object]) -> Path:
    path = storage_dir / "operations" / "database-backup" / "readiness.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_disabled_policy_is_skipped_and_never_claims_backup(tmp_path: Path):
    assessment = evaluate_database_backup_readiness(
        _policy(enabled=False),
        storage_dir=tmp_path / "storage",
        now=_NOW,
    )

    assert assessment.status == "skipped"
    assert "不代表系统已备份" in assessment.message


def test_enabled_policy_without_evidence_fails_closed(tmp_path: Path):
    assessment = evaluate_database_backup_readiness(
        _policy(),
        storage_dir=tmp_path / "storage",
        now=_NOW,
    )

    assert assessment.status == "fail"
    assert "未找到" in assessment.message


def test_valid_evidence_is_warn_not_pass(tmp_path: Path):
    storage_dir = tmp_path / "storage"
    _write_evidence(storage_dir, _evidence())

    assessment = evaluate_database_backup_readiness(
        _policy(),
        storage_dir=storage_dir,
        now=_NOW,
    )

    assert assessment.status == "warn"
    assert "不能标记为 pass" in assessment.message
    assert assessment.details["artifact_id"] == _ARTIFACT_ID


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload["backup"].update(
                {"completed_at": _timestamp(_NOW - timedelta(hours=27))}
            ),
            "超过策略允许时效",
        ),
        (
            lambda payload: (
                payload["backup"].update(
                    {"completed_at": _timestamp(_NOW - timedelta(days=33))}
                ),
                payload["restore_drill"].update(
                    {"completed_at": _timestamp(_NOW - timedelta(days=32))}
                ),
            ),
            "超过策略允许时效",
        ),
        (
            lambda payload: payload["restore_drill"].update(
                {"artifact_id": "123e4567-e89b-12d3-a456-426614174001"}
            ),
            "不一致",
        ),
        (
            lambda payload: payload["restore_drill"].update({"status": "failed"}),
            "不是 passed",
        ),
        (
            lambda payload: payload["backup"].update({"artifact_sha256": "bad"}),
            "SHA-256",
        ),
        (
            lambda payload: payload["backup"].update(
                {"completed_at": _timestamp(_NOW + timedelta(seconds=1))}
            ),
            "不能晚于",
        ),
    ],
)
def test_invalid_or_stale_evidence_fails_closed(
    tmp_path: Path,
    mutate,
    message: str,
):
    storage_dir = tmp_path / "storage"
    payload = _evidence()
    mutate(payload)
    _write_evidence(storage_dir, payload)

    assessment = evaluate_database_backup_readiness(
        _policy(),
        storage_dir=storage_dir,
        now=_NOW,
    )

    assert assessment.status == "fail"
    assert message in assessment.message


def test_evidence_symbolic_link_is_never_followed(tmp_path: Path):
    storage_dir = tmp_path / "storage"
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_evidence()), encoding="utf-8")
    evidence_path = storage_dir / "operations" / "database-backup" / "readiness.json"
    evidence_path.parent.mkdir(parents=True)
    try:
        evidence_path.symlink_to(outside)
    except OSError:
        pytest.skip("当前文件系统不允许创建符号链接")

    assessment = evaluate_database_backup_readiness(
        _policy(),
        storage_dir=storage_dir,
        now=_NOW,
    )

    assert assessment.status == "fail"
    assert "符号链接" in assessment.message
