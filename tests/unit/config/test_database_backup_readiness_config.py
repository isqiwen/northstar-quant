"""PostgreSQL 备份/恢复就绪策略测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from northstar_quant.config.database_backup_readiness import (
    DatabaseBackupReadinessConfigError,
    load_database_backup_readiness_policy,
)


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": 1,
        "enabled": False,
        "evidence_relative_path": "operations/database-backup/readiness.json",
        "max_backup_age_hours": 26,
        "max_restore_verification_age_days": 31,
    }
    payload.update(overrides)
    return payload


def _write_policy(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_database_backup_readiness_policy_is_explicitly_disabled_by_default():
    policy = load_database_backup_readiness_policy()

    assert policy.enabled is False
    assert policy.evidence_relative_path.as_posix() == (
        "operations/database-backup/readiness.json"
    )
    assert policy.max_backup_age_hours == 26
    assert policy.max_restore_verification_age_days == 31


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("evidence_relative_path", "../outside.json", "不能使用"),
        ("evidence_relative_path", "/tmp/readiness.json", "不能使用"),
        ("evidence_relative_path", "C:\\backup\\readiness.json", "Windows"),
        ("evidence_relative_path", "operations/readiness.txt", ".json"),
        ("max_backup_age_hours", 0, "大于等于 1"),
        ("max_restore_verification_age_days", False, "大于等于 1"),
    ],
)
def test_database_backup_readiness_policy_rejects_unsafe_values(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
):
    path = tmp_path / "database_backup_readiness.yaml"
    _write_policy(path, _payload(**{field: value}))

    with pytest.raises(DatabaseBackupReadinessConfigError, match=message):
        load_database_backup_readiness_policy(path)


def test_database_backup_readiness_policy_rejects_unknown_fields(tmp_path: Path):
    path = tmp_path / "database_backup_readiness.yaml"
    _write_policy(path, _payload(unexpected=True))

    with pytest.raises(DatabaseBackupReadinessConfigError, match="未知字段"):
        load_database_backup_readiness_policy(path)
