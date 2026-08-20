"""运行输出清理配置测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from northstar_quant.platform.config.output_retention import (
    OutputRetentionConfigError,
    load_output_retention_policy,
)


def test_output_retention_policy_is_explicitly_disabled_by_default():
    policy = load_output_retention_policy()

    assert policy.enabled is False
    assert policy.download_cache_retention_days == 30
    assert policy.temporary_file_retention_days == 7


def test_output_retention_policy_rejects_unknown_or_unsafe_values(tmp_path: Path):
    path = tmp_path / "output_retention.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "enabled": True,
                "download_cache_retention_days": 0,
                "temporary_file_retention_days": 7,
                "unexpected": True,
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(OutputRetentionConfigError, match="未知字段"):
        load_output_retention_policy(path)

    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "enabled": True,
                "download_cache_retention_days": 0,
                "temporary_file_retention_days": 7,
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    with pytest.raises(OutputRetentionConfigError, match="大于等于 1"):
        load_output_retention_policy(path)
