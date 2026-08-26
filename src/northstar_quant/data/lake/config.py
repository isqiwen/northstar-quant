"""历史 Parquet Lake 的受版本控制配置加载器。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from northstar_quant.data.artifacts.fingerprints import canonical_json_sha256
from northstar_quant.data.lake.models import LakeContractError, LakeDatasetKind, LakeDatasetPolicy
from northstar_quant.foundation.config.settings import get_settings
from northstar_quant.foundation.config.yaml_loader import load_yaml


class HistoricalLakeConfigError(ValueError):
    """历史 Lake 配置缺失、字段不完整或试图启用自动删除。"""


@dataclass(frozen=True, slots=True)
class HistoricalLakeConfig:
    """固定 Lake policy；自动清理由设计永久关闭。"""

    config_sha256: str
    policies: tuple[LakeDatasetPolicy, ...]

    def __post_init__(self) -> None:
        if len(self.config_sha256) != 64:
            raise HistoricalLakeConfigError("historical lake config hash 无效")
        if len(self.policies) != len(LakeDatasetKind):
            raise HistoricalLakeConfigError(
                "historical lake config 必须覆盖所有受支持 dataset kind"
            )
        kinds = {policy.kind for policy in self.policies}
        if kinds != set(LakeDatasetKind):
            raise HistoricalLakeConfigError("historical lake config dataset kind 不完整或重复")

    def policy_for(self, kind: LakeDatasetKind) -> LakeDatasetPolicy:
        if not isinstance(kind, LakeDatasetKind):
            raise HistoricalLakeConfigError("kind 必须是 LakeDatasetKind")
        return next(policy for policy in self.policies if policy.kind is kind)


def get_historical_lake_config_path(path: str | Path | None = None) -> Path:
    if path is None:
        return get_settings().project_root / "configs" / "data" / "historical_lake.yaml"
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = get_settings().project_root / candidate
    return candidate.resolve()


def load_historical_lake_config(path: str | Path | None = None) -> HistoricalLakeConfig:
    config_path = get_historical_lake_config_path(path)
    if not config_path.is_file():
        raise HistoricalLakeConfigError(f"historical lake 配置不存在：{config_path}")
    payload = load_yaml(config_path)
    if not isinstance(payload, dict) or set(payload) != {"version", "retention", "dataset_kinds"}:
        raise HistoricalLakeConfigError(
            "historical lake 配置字段必须为 version、retention、dataset_kinds"
        )
    if payload["version"] != 1:
        raise HistoricalLakeConfigError("historical lake 配置 version 当前必须为 1")
    retention = payload["retention"]
    if not isinstance(retention, dict) or set(retention) != {"automatic_cleanup"}:
        raise HistoricalLakeConfigError("historical lake retention 配置不完整")
    if retention["automatic_cleanup"] is not False:
        raise HistoricalLakeConfigError("historical lake 禁止自动删除或清空历史数据")
    raw_kinds = payload["dataset_kinds"]
    if not isinstance(raw_kinds, dict) or set(raw_kinds) != {
        kind.value for kind in LakeDatasetKind
    }:
        raise HistoricalLakeConfigError("historical lake dataset_kinds 必须精确覆盖受支持类别")
    policies: list[LakeDatasetPolicy] = []
    for kind in LakeDatasetKind:
        raw_policy = raw_kinds[kind.value]
        if not isinstance(raw_policy, dict) or set(raw_policy) != {
            "available_at_column",
            "partition_columns",
        }:
            raise HistoricalLakeConfigError(f"dataset kind {kind.value} 的 policy 字段不完整")
        try:
            policies.append(
                LakeDatasetPolicy(
                    kind=kind,
                    partition_columns=tuple(_string_list(raw_policy["partition_columns"])),
                    available_at_column=raw_policy["available_at_column"],
                )
            )
        except LakeContractError as exc:
            raise HistoricalLakeConfigError(
                f"dataset kind {kind.value} 的 policy 无效：{exc}"
            ) from exc
    return HistoricalLakeConfig(
        config_sha256=canonical_json_sha256(payload),
        policies=tuple(policies),
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise HistoricalLakeConfigError("partition_columns 必须是非空文本列表")
    return value
