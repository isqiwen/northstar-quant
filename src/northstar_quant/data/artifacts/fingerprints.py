"""数据制品的确定性身份与内容指纹。

本模块刻意不访问文件系统或读取时钟。标准化身份不包含路径、运行 ID 或运行时的时间戳；
制品快照身份则显式纳入其声明的 PIT 时间和来源证据，避免修订数据与旧版本混淆。实际文件的
SHA-256 仍由 ``storage`` 负责计算。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from northstar_quant.foundation.common.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
    content_sha256,
    require_sha256,
)


__all__ = [
    "FingerprintError",
    "artifact_snapshot_hash",
    "canonical_json_sha256",
    "content_sha256",
    "dataset_version_hash",
    "derived_identity_hash",
    "lineage_hash",
    "normalization_binding_hash",
    "normalization_identity_hash",
    "require_sha256",
    "snapshot_lineage_hash",
]


def normalization_identity_hash(
    raw_content_hash: str,
    normalized_content_hash: str,
    transform_version: str,
    schema_version: str,
) -> str:
    """返回标准化转换的稳定身份。

    它表达“哪份 raw 内容经哪个版本转换到哪个 schema，并得到哪份标准化内容”。因此时间、
    文件路径、机器名和随机 ID 均不会改变该身份，但错误或非确定性转换产生的不同内容不会与
    原输出碰撞。
    """

    return _domain_hash(
        "normalization_identity_v1",
        {
            "raw_content_hash": require_sha256(raw_content_hash, field_name="raw_content_hash"),
            "normalized_content_hash": require_sha256(
                normalized_content_hash,
                field_name="normalized_content_hash",
            ),
            "schema_version": _required_text(schema_version, "schema_version"),
            "transform_version": _required_text(transform_version, "transform_version"),
        },
    )


def normalization_binding_hash(
    raw_snapshot_hash: str,
    transform_version: str,
    schema_version: str,
) -> str:
    """返回不可变存储使用的标准化唯一绑定键。

    ``normalization_identity_hash`` 同时包含输出内容，适合描述一次完整转换；本键刻意不包含
    输出，用于约束同一 raw 快照和同一转换/schema 版本只能发布一种标准化内容。
    """

    return _domain_hash(
        "normalization_binding_v1",
        {
            "raw_snapshot_hash": require_sha256(
                raw_snapshot_hash,
                field_name="raw_snapshot_hash",
            ),
            "schema_version": _required_text(schema_version, "schema_version"),
            "transform_version": _required_text(transform_version, "transform_version"),
        },
    )


def derived_identity_hash(
    input_content_hashes: Iterable[str],
    transform_version: str,
    schema_version: str,
) -> str:
    """返回派生制品的稳定身份，并保留有业务含义的输入顺序。"""

    return _domain_hash(
        "derived_identity_v1",
        {
            "input_content_hashes": _ordered_hashes(
                input_content_hashes,
                field_name="input_content_hashes",
            ),
            "schema_version": _required_text(schema_version, "schema_version"),
            "transform_version": _required_text(transform_version, "transform_version"),
        },
    )


def artifact_snapshot_hash(
    *,
    artifact_id: str,
    kind: str,
    source_id: str,
    content_hash: str,
    acquired_at: str,
    available_at: str,
    schema_version: str,
    transform_version: str,
    quality_status: str,
    provenance_hash: str,
) -> str:
    """返回一个不可变制品快照的身份。

    相同字节在不同来源、PIT 可用时间或 provenance 下不能被当成同一研究输入；这些语义字段
    必须进入 dataset version 的输入引用，而原始 ``content_hash`` 仍可用于 blob 去重。
    """

    return _domain_hash(
        "artifact_snapshot_v1",
        {
            "artifact_id": _required_text(artifact_id, "artifact_id"),
            "acquired_at": _required_text(acquired_at, "acquired_at"),
            "available_at": _required_text(available_at, "available_at"),
            "content_hash": require_sha256(content_hash, field_name="content_hash"),
            "kind": _required_text(kind, "kind"),
            "provenance_hash": require_sha256(provenance_hash, field_name="provenance_hash"),
            "quality_status": _required_text(quality_status, "quality_status"),
            "schema_version": _required_text(schema_version, "schema_version"),
            "source_id": _required_text(source_id, "source_id"),
            "transform_version": _required_text(transform_version, "transform_version"),
        },
    )


def dataset_version_hash(
    dataset_id: str,
    artifact_snapshot_hashes: Iterable[str],
    schema_version: str,
    transform_version: str,
    source_ids: Iterable[str],
) -> str:
    """返回数据集版本身份。

    数据集的制品快照集合和来源集合均按稳定顺序写入，因而不同调用方的枚举顺序不会改变
    版本。重复输入被拒绝，避免相同制品因意外重复而产生伪新版本。
    """

    return _domain_hash(
        "dataset_version_v1",
        {
            "artifact_snapshot_hashes": _canonical_hashes(
                artifact_snapshot_hashes,
                field_name="artifact_snapshot_hashes",
            ),
            "dataset_id": _required_text(dataset_id, "dataset_id"),
            "schema_version": _required_text(schema_version, "schema_version"),
            "source_ids": _canonical_texts(source_ids, field_name="source_ids"),
            "transform_version": _required_text(transform_version, "transform_version"),
        },
    )


def lineage_hash(
    output_content_hash: str,
    input_content_hashes: Iterable[str],
    transform_version: str,
) -> str:
    """返回一条有向血缘边的稳定身份。"""

    return _domain_hash(
        "lineage_v1",
        {
            "input_content_hashes": _ordered_hashes(
                input_content_hashes,
                field_name="input_content_hashes",
            ),
            "output_content_hash": require_sha256(
                output_content_hash,
                field_name="output_content_hash",
            ),
            "transform_version": _required_text(transform_version, "transform_version"),
        },
    )


def snapshot_lineage_hash(
    output_snapshot_hash: str,
    input_snapshot_hashes: Iterable[str],
    transform_version: str,
) -> str:
    """返回持久化血缘使用的 snapshot 级身份。

    content-level lineage 不能区分同字节但不同来源、PIT 时间或 provenance 的制品；该身份把
    不可变 snapshot 引用按输入顺序写入，供制品库回放时校验。
    """

    return _domain_hash(
        "snapshot_lineage_v1",
        {
            "input_snapshot_hashes": _ordered_hashes(
                input_snapshot_hashes,
                field_name="input_snapshot_hashes",
            ),
            "output_snapshot_hash": require_sha256(
                output_snapshot_hash,
                field_name="output_snapshot_hash",
            ),
            "transform_version": _required_text(transform_version, "transform_version"),
        },
    )


def _domain_hash(namespace: str, payload: Mapping[str, Any]) -> str:
    return canonical_json_sha256({"namespace": namespace, "payload": dict(payload)})


def _canonical_hashes(values: Iterable[str], *, field_name: str) -> tuple[str, ...]:
    canonical = tuple(sorted(require_sha256(value, field_name=field_name) for value in values))
    if not canonical:
        raise FingerprintError(f"{field_name} 不能为空")
    if len(canonical) != len(set(canonical)):
        raise FingerprintError(f"{field_name} 不能包含重复内容哈希")
    return canonical


def _ordered_hashes(values: Iterable[str], *, field_name: str) -> tuple[str, ...]:
    ordered = tuple(require_sha256(value, field_name=field_name) for value in values)
    if not ordered:
        raise FingerprintError(f"{field_name} 不能为空")
    if len(ordered) != len(set(ordered)):
        raise FingerprintError(f"{field_name} 不能包含重复内容哈希")
    return ordered


def _canonical_texts(values: Iterable[str], *, field_name: str) -> tuple[str, ...]:
    canonical = tuple(sorted(_required_text(value, field_name) for value in values))
    if not canonical:
        raise FingerprintError(f"{field_name} 不能为空")
    if len(canonical) != len(set(canonical)):
        raise FingerprintError(f"{field_name} 不能包含重复值")
    return canonical


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FingerprintError(f"{field_name} 不能为空")
    return value.strip()
