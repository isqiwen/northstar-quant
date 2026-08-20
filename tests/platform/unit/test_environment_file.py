"""活动 `.env` 的完整字段结构测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from northstar_quant.platform.config.environment_file import (
    ActiveEnvironmentFileError,
    declared_environment_key_counts,
    validate_active_environment_file,
)


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_declared_environment_key_counts_ignores_comments_and_blank_lines(tmp_path):
    env_file = tmp_path / ".env"
    _write(
        env_file,
        "# 注释中的 OLD_KEY=ignored\n\nFIRST=one\n SECOND = two\n",
    )

    assert declared_environment_key_counts(env_file) == {"FIRST": 1, "SECOND": 1}


def test_validate_active_environment_file_requires_exact_key_set(tmp_path):
    env_file = tmp_path / ".env"
    _write(env_file, "FIRST=one\nRETIRED=value\nUNKNOWN=value\n")

    with pytest.raises(ActiveEnvironmentFileError) as exc_info:
        validate_active_environment_file(
            env_file,
            expected_keys={"FIRST", "SECOND"},
            retired_keys={"RETIRED"},
        )

    message = str(exc_info.value)
    assert "缺少字段：SECOND" in message
    assert "已废弃字段：RETIRED" in message
    assert "未知字段：UNKNOWN" in message
    assert "value" not in message


def test_validate_active_environment_file_rejects_duplicates_and_malformed_lines(tmp_path):
    env_file = tmp_path / ".env"
    _write(env_file, "FIRST=one\nFIRST=two\n")

    with pytest.raises(ActiveEnvironmentFileError, match="重复字段：FIRST"):
        validate_active_environment_file(env_file, expected_keys={"FIRST"})

    _write(env_file, "not a declaration\n")
    with pytest.raises(ActiveEnvironmentFileError, match="不是 KEY=value 声明"):
        validate_active_environment_file(env_file, expected_keys={"FIRST"})


def test_validate_active_environment_file_rejects_missing_file(tmp_path):
    with pytest.raises(ActiveEnvironmentFileError, match="请先复制 .env.example 为 .env"):
        validate_active_environment_file(
            tmp_path / ".env",
            expected_keys={"FIRST"},
        )
