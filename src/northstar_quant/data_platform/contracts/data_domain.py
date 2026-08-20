"""Data Platform 的不可变领域契约。

这些对象描述一次数据获取、标准化与派生的证据，而不是当前文件系统中的可变路径。它们不负责
下载、写入或数据库持久化；P1-WP02 才会把该契约接入不可变制品库。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
import re
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlsplit

from northstar_quant.data_platform.artifacts.fingerprints import (
    FingerprintError,
    artifact_snapshot_hash,
    canonical_json_sha256,
    content_sha256,
    dataset_version_hash,
    derived_identity_hash,
    lineage_hash,
    normalization_identity_hash,
    require_sha256,
)
from northstar_quant.platform.config.data_sources import data_source_config_sha256

if TYPE_CHECKING:
    from northstar_quant.platform.config.data_sources import DataSourceConfig


class DataDomainError(ValueError):
    """数据领域证据缺失、时间倒流或身份不一致。"""


class QualityStatus(str, Enum):
    """质量检查结论；UNKNOWN 不可被静默视为 PASS。"""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"


class ArtifactKind(str, Enum):
    """原始、标准化与派生数据制品层级。"""

    RAW = "raw"
    NORMALIZED = "normalized"
    DERIVED = "derived"


_QUALITY_STATUS_PRECEDENCE = {
    QualityStatus.PASS: 0,
    QualityStatus.WARN: 1,
    QualityStatus.UNKNOWN: 2,
    QualityStatus.FAIL: 3,
}


_SECRET_TEXT_PATTERN = re.compile(
    r"(?i)(?:authorization|bearer|api[ _-]?key|credential|token|secret|password|passwd|cookie)"
)


@dataclass(frozen=True, slots=True)
class LicenseMetadata:
    """不含凭据的授权快照。

    当前配置中的 ``credential_env_var``、账号和合同原件不会进入该对象；制品只保存可审计的
    合同引用和文件摘要。
    """

    status: str
    contract_reference: str | None
    effective_from: str | None
    expires_on: str | None
    terms_sha256: str | None
    permitted_purposes: tuple[str, ...]
    allows_internal_storage: bool
    allows_derived_data_storage: bool
    allows_live_trading: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _required_text(self.status, "license.status"))
        object.__setattr__(
            self,
            "contract_reference",
            _optional_safe_reference(self.contract_reference, "license.contract_reference"),
        )
        effective_from = _optional_date(self.effective_from, "license.effective_from")
        expires_on = _optional_date(self.expires_on, "license.expires_on")
        if effective_from is not None and expires_on is not None:
            if date.fromisoformat(effective_from) > date.fromisoformat(expires_on):
                raise DataDomainError("license.effective_from 不能晚于 license.expires_on")
        object.__setattr__(self, "effective_from", effective_from)
        object.__setattr__(self, "expires_on", expires_on)
        if self.terms_sha256 is not None:
            object.__setattr__(
                self,
                "terms_sha256",
                _checked_hash(self.terms_sha256, "license.terms_sha256"),
            )
        purposes = tuple(
            sorted(
                _required_text(value, "license.permitted_purposes")
                for value in self.permitted_purposes
            )
        )
        if len(purposes) != len(set(purposes)):
            raise DataDomainError("license.permitted_purposes 不能包含重复用途")
        object.__setattr__(self, "permitted_purposes", purposes)
        for field_name in (
            "allows_internal_storage",
            "allows_derived_data_storage",
            "allows_live_trading",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise DataDomainError(f"license.{field_name} 必须是 bool")


@dataclass(frozen=True, slots=True)
class DataSource:
    """一次获取所引用的数据来源快照，而非可变的运行时配置。"""

    source_id: str
    adapter_id: str
    name: str
    tier: str
    status: str
    config_sha256: str
    official_references: tuple[str, ...]
    license: LicenseMetadata

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _required_text(self.source_id, "source_id"))
        object.__setattr__(self, "adapter_id", _required_text(self.adapter_id, "adapter_id"))
        object.__setattr__(self, "name", _required_text(self.name, "name"))
        object.__setattr__(self, "tier", _required_text(self.tier, "tier"))
        object.__setattr__(self, "status", _required_text(self.status, "status"))
        object.__setattr__(
            self, "config_sha256", _checked_hash(self.config_sha256, "config_sha256")
        )
        references = tuple(
            sorted(
                _safe_reference(value, "official_references") for value in self.official_references
            )
        )
        if not references:
            raise DataDomainError("official_references 不能为空")
        if len(references) != len(set(references)):
            raise DataDomainError("official_references 不能包含重复引用")
        object.__setattr__(self, "official_references", references)
        if not isinstance(self.license, LicenseMetadata):
            raise DataDomainError("license 必须是 LicenseMetadata")

    @classmethod
    def from_config(cls, config: DataSourceConfig) -> "DataSource":
        """从当前受管配置冻结一个不含凭据的来源快照。"""

        license_config = config.license
        license_metadata = LicenseMetadata(
            status=license_config.status,
            contract_reference=license_config.contract_ref,
            effective_from=license_config.effective_from,
            expires_on=license_config.expires_on,
            terms_sha256=license_config.contract_document_sha256,
            permitted_purposes=license_config.permitted_purposes,
            allows_internal_storage=license_config.allows_internal_storage,
            allows_derived_data_storage=license_config.allows_derived_data_storage,
            allows_live_trading=license_config.allows_live_trading,
        )
        return cls(
            source_id=config.source_id,
            adapter_id=config.adapter_id,
            name=config.name,
            tier=config.tier,
            status=config.status,
            config_sha256=data_source_config_sha256(config),
            official_references=config.official_references,
            license=license_metadata,
        )


@dataclass(frozen=True, slots=True)
class ArtifactProvenance:
    """可审计但不含本机绝对路径或凭据的获取证据。"""

    source_id: str
    source_reference: str
    collection_method: str
    attributes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_id", _required_text(self.source_id, "provenance.source_id")
        )
        object.__setattr__(
            self,
            "source_reference",
            _safe_reference(self.source_reference, "provenance.source_reference"),
        )
        object.__setattr__(
            self,
            "collection_method",
            _safe_provenance_attribute(self.collection_method, "provenance.collection_method"),
        )
        attributes: list[tuple[str, str]] = []
        for item in self.attributes:
            if not isinstance(item, tuple) or len(item) != 2:
                raise DataDomainError("provenance.attributes 必须是 (key, value) 元组")
            key, value = item
            attributes.append(
                (
                    _safe_provenance_attribute(key, "provenance.attributes.key"),
                    _safe_provenance_attribute(value, "provenance.attributes.value"),
                )
            )
        attributes.sort(key=lambda item: item[0])
        if len({key for key, _ in attributes}) != len(attributes):
            raise DataDomainError("provenance.attributes 不能包含重复键")
        object.__setattr__(self, "attributes", tuple(attributes))

    @property
    def identity_hash(self) -> str:
        """返回可公开保存的来源证据身份，不含路径或敏感值。"""

        return canonical_json_sha256(
            {
                "attributes": self.attributes,
                "collection_method": self.collection_method,
                "source_id": self.source_id,
                "source_reference": self.source_reference,
            }
        )


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    """三类可发布制品共享的不可变身份与 PIT 元数据。"""

    artifact_id: str
    source_id: str
    acquired_at: datetime
    available_at: datetime
    schema_version: str
    content_hash: str
    transform_version: str
    quality_status: QualityStatus
    provenance: ArtifactProvenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_id", _required_text(self.artifact_id, "artifact_id"))
        object.__setattr__(self, "source_id", _required_text(self.source_id, "source_id"))
        acquired_at = _utc_datetime(self.acquired_at, "acquired_at")
        available_at = _utc_datetime(self.available_at, "available_at")
        if available_at < acquired_at:
            raise DataDomainError("available_at 不能早于已取得制品的 acquired_at")
        object.__setattr__(self, "acquired_at", acquired_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(
            self, "schema_version", _required_text(self.schema_version, "schema_version")
        )
        object.__setattr__(self, "content_hash", _checked_hash(self.content_hash, "content_hash"))
        object.__setattr__(
            self,
            "transform_version",
            _required_text(self.transform_version, "transform_version"),
        )
        if not isinstance(self.quality_status, QualityStatus):
            raise DataDomainError("quality_status 必须是 QualityStatus")
        if not isinstance(self.provenance, ArtifactProvenance):
            raise DataDomainError("provenance 必须是 ArtifactProvenance")
        if self.provenance.source_id != self.source_id:
            raise DataDomainError("provenance.source_id 必须与制品 source_id 一致")


class Artifact(Protocol):
    """供数据集、血缘与质量结果消费的只读制品接口。"""

    metadata: ArtifactMetadata
    kind: ArtifactKind


class _ArtifactView:
    """让具体制品直接暴露公共 metadata 字段，而无需重复存储。"""

    metadata: ArtifactMetadata

    @property
    def artifact_id(self) -> str:
        return self.metadata.artifact_id

    @property
    def source_id(self) -> str:
        return self.metadata.source_id

    @property
    def acquired_at(self) -> datetime:
        return self.metadata.acquired_at

    @property
    def available_at(self) -> datetime:
        return self.metadata.available_at

    @property
    def schema_version(self) -> str:
        return self.metadata.schema_version

    @property
    def content_hash(self) -> str:
        return self.metadata.content_hash

    @property
    def transform_version(self) -> str:
        return self.metadata.transform_version

    @property
    def quality_status(self) -> QualityStatus:
        return self.metadata.quality_status

    @property
    def provenance(self) -> ArtifactProvenance:
        return self.metadata.provenance


@dataclass(frozen=True, slots=True)
class RawArtifact(_ArtifactView):
    """未经过业务标准化的来源内容。"""

    metadata: ArtifactMetadata
    raw_format: str
    kind: ArtifactKind = ArtifactKind.RAW

    def __post_init__(self) -> None:
        _validate_artifact_kind(self.kind, ArtifactKind.RAW)
        _validate_metadata(self.metadata)
        object.__setattr__(self, "raw_format", _required_text(self.raw_format, "raw_format"))


@dataclass(frozen=True, slots=True)
class NormalizedArtifact(_ArtifactView):
    """由一份 raw 制品经明确版本转换得到的标准化内容。"""

    metadata: ArtifactMetadata
    raw_artifact: RawArtifact
    normalization_identity: str
    kind: ArtifactKind = ArtifactKind.NORMALIZED

    def __post_init__(self) -> None:
        _validate_artifact_kind(self.kind, ArtifactKind.NORMALIZED)
        _validate_metadata(self.metadata)
        if not isinstance(self.raw_artifact, RawArtifact):
            raise DataDomainError("raw_artifact 必须是 RawArtifact")
        if self.source_id != self.raw_artifact.source_id:
            raise DataDomainError("标准化制品必须保留 raw_artifact 的 source_id")
        if self.acquired_at < self.raw_artifact.acquired_at:
            raise DataDomainError("标准化制品的 acquired_at 不能早于 raw_artifact")
        if self.available_at < self.raw_artifact.available_at:
            raise DataDomainError("标准化制品的 available_at 不能早于 raw_artifact")
        expected_identity = normalization_identity_hash(
            self.raw_artifact.content_hash,
            self.content_hash,
            self.transform_version,
            self.schema_version,
        )
        if self.normalization_identity != expected_identity:
            raise DataDomainError("normalization_identity 与 raw 内容、schema 或转换版本不一致")

    @classmethod
    def from_deterministic_transform(
        cls,
        *,
        artifact_id: str,
        raw_artifact: RawArtifact,
        raw_payload: bytes,
        normalize: Callable[[bytes], bytes],
        acquired_at: datetime,
        available_at: datetime,
        schema_version: str,
        transform_version: str,
        quality_status: QualityStatus,
        provenance: ArtifactProvenance,
    ) -> "NormalizedArtifact":
        """通过一次受验证的确定性转换创建标准化制品。

        工厂先确认 ``raw_payload`` 与 ``raw_artifact`` 的内容哈希一致，再对同一输入连续执行两次
        纯转换。两次输出的内容哈希不一致时拒绝发布，避免非确定性转换把同一 raw/版本悄悄变成
        不同的研究输入。适配器协议会在 P1-WP06 扩展，但新适配器应复用该失败关闭入口。
        """

        if not isinstance(raw_artifact, RawArtifact):
            raise DataDomainError("raw_artifact 必须是 RawArtifact")
        if not isinstance(raw_payload, bytes):
            raise DataDomainError("raw_payload 必须是 bytes")
        if not callable(normalize):
            raise DataDomainError("normalize 必须是接收 bytes 并返回 bytes 的可调用对象")
        if content_sha256(raw_payload, field_name="raw_payload") != raw_artifact.content_hash:
            raise DataDomainError("raw_payload 的内容哈希必须与 raw_artifact 一致")

        first_output = normalize(raw_payload)
        second_output = normalize(raw_payload)
        if not isinstance(first_output, bytes) or not isinstance(second_output, bytes):
            raise DataDomainError("normalize 必须返回 bytes")
        content_hash = content_sha256(first_output, field_name="normalized_payload")
        if content_hash != content_sha256(second_output, field_name="normalized_payload"):
            raise DataDomainError("同一 raw 输入与转换版本产生了不一致的 normalized 内容")

        metadata = ArtifactMetadata(
            artifact_id=artifact_id,
            source_id=raw_artifact.source_id,
            acquired_at=acquired_at,
            available_at=available_at,
            schema_version=schema_version,
            content_hash=content_hash,
            transform_version=transform_version,
            quality_status=quality_status,
            provenance=provenance,
        )
        return cls(
            metadata=metadata,
            raw_artifact=raw_artifact,
            normalization_identity=normalization_identity_hash(
                raw_artifact.content_hash,
                content_hash,
                transform_version,
                schema_version,
            ),
        )


@dataclass(frozen=True, slots=True)
class DerivedArtifact(_ArtifactView):
    """由一个或多个上游制品生成的派生事实。"""

    metadata: ArtifactMetadata
    input_artifacts: tuple[Artifact, ...]
    derivation_identity: str
    kind: ArtifactKind = ArtifactKind.DERIVED

    def __post_init__(self) -> None:
        _validate_artifact_kind(self.kind, ArtifactKind.DERIVED)
        _validate_metadata(self.metadata)
        inputs = tuple(self.input_artifacts)
        if not inputs:
            raise DataDomainError("input_artifacts 不能为空")
        for artifact in inputs:
            _validate_artifact(artifact)
        input_hashes = tuple(artifact.metadata.content_hash for artifact in inputs)
        if len(input_hashes) != len(set(input_hashes)):
            raise DataDomainError("input_artifacts 不能包含重复内容哈希")
        if self.content_hash in input_hashes:
            raise DataDomainError("派生制品不能把自身作为上游输入")
        latest_available_at = max(artifact.metadata.available_at for artifact in inputs)
        latest_acquired_at = max(artifact.metadata.acquired_at for artifact in inputs)
        if self.acquired_at < latest_acquired_at:
            raise DataDomainError("派生制品的 acquired_at 不能早于任一上游制品")
        if self.available_at < latest_available_at:
            raise DataDomainError("派生制品的 available_at 不能早于任一上游制品")
        expected_identity = derived_identity_hash(
            input_hashes,
            self.transform_version,
            self.schema_version,
        )
        if self.derivation_identity != expected_identity:
            raise DataDomainError("derivation_identity 与上游内容、schema 或转换版本不一致")
        object.__setattr__(self, "input_artifacts", inputs)


@dataclass(frozen=True, slots=True)
class DataQualityResult:
    """针对一份制品的、在发布前完成的质量结论。

    ``checked_at`` 必须落在制品取得后、对研究消费者可用前的闭区间内。事后复检不能伪装成
    原始发布质量；未来如需保存复检，应以带独立可用时间的新结果类型表达。
    """

    artifact: Artifact
    check_id: str
    quality_status: QualityStatus
    checked_at: datetime
    summary: str

    def __post_init__(self) -> None:
        _validate_artifact(self.artifact)
        object.__setattr__(self, "check_id", _required_text(self.check_id, "check_id"))
        if not isinstance(self.quality_status, QualityStatus):
            raise DataDomainError("quality_status 必须是 QualityStatus")
        if (
            _QUALITY_STATUS_PRECEDENCE[self.artifact.metadata.quality_status]
            < _QUALITY_STATUS_PRECEDENCE[self.quality_status]
        ):
            raise DataDomainError(
                "artifact.metadata.quality_status 不得弱于 DataQualityResult 的质量结论"
            )
        checked_at = _utc_datetime(self.checked_at, "checked_at")
        if checked_at < self.artifact.metadata.acquired_at:
            raise DataDomainError("checked_at 不能早于制品 acquired_at")
        if checked_at > self.artifact.metadata.available_at:
            raise DataDomainError("checked_at 不能晚于制品 available_at")
        object.__setattr__(self, "checked_at", checked_at)
        object.__setattr__(self, "summary", _safe_provenance_attribute(self.summary, "summary"))

    @property
    def source_id(self) -> str:
        return self.artifact.metadata.source_id

    @property
    def content_hash(self) -> str:
        return self.artifact.metadata.content_hash

    @property
    def schema_version(self) -> str:
        return self.artifact.metadata.schema_version

    @property
    def transform_version(self) -> str:
        return self.artifact.metadata.transform_version

    @property
    def provenance(self) -> ArtifactProvenance:
        return self.artifact.metadata.provenance


@dataclass(frozen=True, slots=True)
class DataLineage:
    """一份输出制品与其上游制品之间的有向、可验证血缘边。"""

    output_artifact: Artifact
    input_artifacts: tuple[Artifact, ...]
    transform_version: str
    lineage_identity: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        _validate_artifact(self.output_artifact)
        if isinstance(self.output_artifact, RawArtifact):
            raise DataDomainError("RawArtifact 没有上游转换，不能创建 DataLineage")
        if isinstance(self.output_artifact, NormalizedArtifact):
            expected_inputs: tuple[Artifact, ...] = (
                cast(Artifact, self.output_artifact.raw_artifact),
            )
        elif isinstance(self.output_artifact, DerivedArtifact):
            expected_inputs = self.output_artifact.input_artifacts
        else:  # _validate_artifact 已限制实际类型；保留防御性失败关闭。
            raise DataDomainError("DataLineage 输出类型不受支持")
        inputs = tuple(self.input_artifacts)
        if not inputs:
            raise DataDomainError("input_artifacts 不能为空")
        for artifact in inputs:
            _validate_artifact(artifact)
        input_hashes = tuple(artifact.metadata.content_hash for artifact in inputs)
        if len(input_hashes) != len(set(input_hashes)):
            raise DataDomainError("input_artifacts 不能包含重复内容哈希")
        if self.output_artifact.metadata.content_hash in input_hashes:
            raise DataDomainError("DataLineage 输出不能引用自身为输入")
        transform_version = _required_text(self.transform_version, "transform_version")
        if transform_version != self.output_artifact.metadata.transform_version:
            raise DataDomainError("DataLineage.transform_version 必须与输出制品一致")
        if inputs != expected_inputs:
            raise DataDomainError("DataLineage 输入必须精确匹配输出制品记录的上游节点")
        expected_identity = lineage_hash(
            self.output_artifact.metadata.content_hash,
            input_hashes,
            transform_version,
        )
        if self.lineage_identity != expected_identity:
            raise DataDomainError("lineage_identity 与输入、输出或转换版本不一致")
        recorded_at = _utc_datetime(self.recorded_at, "recorded_at")
        latest_available_at = max(
            self.output_artifact.metadata.available_at,
            *(artifact.metadata.available_at for artifact in inputs),
        )
        if recorded_at < latest_available_at:
            raise DataDomainError("recorded_at 不能早于输出或任一输入制品的 available_at")
        object.__setattr__(self, "input_artifacts", inputs)
        object.__setattr__(self, "transform_version", transform_version)
        object.__setattr__(self, "recorded_at", recorded_at)


@dataclass(frozen=True, slots=True)
class ArtifactSnapshot:
    """可独立验证的制品引用，防止数据集把内容、PIT 与 provenance 错配。"""

    artifact_id: str
    kind: ArtifactKind
    source_id: str
    content_hash: str
    acquired_at: datetime
    available_at: datetime
    schema_version: str
    transform_version: str
    quality_status: QualityStatus
    provenance: ArtifactProvenance
    snapshot_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_id", _required_text(self.artifact_id, "artifact_id"))
        if not isinstance(self.kind, ArtifactKind):
            raise DataDomainError("kind 必须是 ArtifactKind")
        object.__setattr__(self, "source_id", _required_text(self.source_id, "source_id"))
        object.__setattr__(self, "content_hash", _checked_hash(self.content_hash, "content_hash"))
        acquired_at = _utc_datetime(self.acquired_at, "acquired_at")
        available_at = _utc_datetime(self.available_at, "available_at")
        if available_at < acquired_at:
            raise DataDomainError("ArtifactSnapshot.available_at 不能早于 acquired_at")
        object.__setattr__(self, "acquired_at", acquired_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(
            self, "schema_version", _required_text(self.schema_version, "schema_version")
        )
        object.__setattr__(
            self,
            "transform_version",
            _required_text(self.transform_version, "transform_version"),
        )
        if not isinstance(self.quality_status, QualityStatus):
            raise DataDomainError("quality_status 必须是 QualityStatus")
        if not isinstance(self.provenance, ArtifactProvenance):
            raise DataDomainError("provenance 必须是 ArtifactProvenance")
        if self.provenance.source_id != self.source_id:
            raise DataDomainError("provenance.source_id 必须与 snapshot source_id 一致")
        expected_hash = artifact_snapshot_hash(
            artifact_id=self.artifact_id,
            kind=self.kind.value,
            source_id=self.source_id,
            content_hash=self.content_hash,
            acquired_at=self.acquired_at.isoformat(),
            available_at=self.available_at.isoformat(),
            schema_version=self.schema_version,
            transform_version=self.transform_version,
            quality_status=self.quality_status.value,
            provenance_hash=self.provenance.identity_hash,
        )
        if self.snapshot_hash != expected_hash:
            raise DataDomainError("snapshot_hash 与制品身份、PIT 或 provenance 不一致")
        object.__setattr__(
            self, "snapshot_hash", _checked_hash(self.snapshot_hash, "snapshot_hash")
        )

    @classmethod
    def from_artifact(cls, artifact: Artifact) -> "ArtifactSnapshot":
        """从完整领域制品冻结一个可供 DatasetVersion 引用的快照。"""

        _validate_artifact(artifact)
        metadata = artifact.metadata
        return cls(
            artifact_id=metadata.artifact_id,
            kind=artifact.kind,
            source_id=metadata.source_id,
            content_hash=metadata.content_hash,
            acquired_at=metadata.acquired_at,
            available_at=metadata.available_at,
            schema_version=metadata.schema_version,
            transform_version=metadata.transform_version,
            quality_status=metadata.quality_status,
            provenance=metadata.provenance,
            snapshot_hash=artifact_snapshot_hash(
                artifact_id=metadata.artifact_id,
                kind=artifact.kind.value,
                source_id=metadata.source_id,
                content_hash=metadata.content_hash,
                acquired_at=metadata.acquired_at.isoformat(),
                available_at=metadata.available_at.isoformat(),
                schema_version=metadata.schema_version,
                transform_version=metadata.transform_version,
                quality_status=metadata.quality_status.value,
                provenance_hash=metadata.provenance.identity_hash,
            ),
        )


@dataclass(frozen=True, slots=True)
class DatasetVersion:
    """一组经验证的不可变制品快照组成的数据集版本。"""

    dataset_id: str
    artifact_snapshots: tuple[ArtifactSnapshot, ...]
    schema_version: str
    transform_version: str
    version_hash: str

    def __post_init__(self) -> None:
        dataset_id = _required_text(self.dataset_id, "dataset_id")
        snapshots = tuple(self.artifact_snapshots)
        if not snapshots:
            raise DataDomainError("artifact_snapshots 不能为空")
        for snapshot in snapshots:
            if not isinstance(snapshot, ArtifactSnapshot):
                raise DataDomainError("artifact_snapshots 必须全部是 ArtifactSnapshot")
        content_hashes = tuple(snapshot.content_hash for snapshot in snapshots)
        if len(content_hashes) != len(set(content_hashes)):
            raise DataDomainError("artifact_snapshots 不能包含重复内容哈希")
        snapshot_hashes = _canonical_content_hashes(
            tuple(snapshot.snapshot_hash for snapshot in snapshots),
            "artifact_snapshot_hashes",
        )
        source_ids = _canonical_texts(
            tuple({snapshot.source_id for snapshot in snapshots}),
            "source_ids",
        )
        schema_version = _required_text(self.schema_version, "schema_version")
        transform_version = _required_text(self.transform_version, "transform_version")
        expected_hash = dataset_version_hash(
            dataset_id,
            snapshot_hashes,
            schema_version,
            transform_version,
            source_ids,
        )
        if self.version_hash != expected_hash:
            raise DataDomainError("version_hash 与数据集快照或版本字段不一致")
        object.__setattr__(self, "dataset_id", dataset_id)
        object.__setattr__(
            self,
            "artifact_snapshots",
            tuple(sorted(snapshots, key=lambda snapshot: snapshot.snapshot_hash)),
        )
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "transform_version", transform_version)
        object.__setattr__(self, "version_hash", _checked_hash(self.version_hash, "version_hash"))

    @classmethod
    def from_artifacts(
        cls,
        *,
        dataset_id: str,
        artifacts: tuple[Artifact, ...],
        schema_version: str,
        transform_version: str,
    ) -> "DatasetVersion":
        """从已验证制品构建版本；其身份与输入枚举顺序无关。"""

        snapshots = tuple(ArtifactSnapshot.from_artifact(artifact) for artifact in artifacts)
        if not snapshots:
            raise DataDomainError("artifacts 不能为空")
        canonical_snapshot_hashes = _canonical_content_hashes(
            tuple(snapshot.snapshot_hash for snapshot in snapshots),
            "artifact_snapshot_hashes",
        )
        source_ids = _canonical_texts(
            tuple({snapshot.source_id for snapshot in snapshots}),
            "source_ids",
        )
        schema_version = _required_text(schema_version, "schema_version")
        transform_version = _required_text(transform_version, "transform_version")
        return cls(
            dataset_id=dataset_id,
            artifact_snapshots=snapshots,
            schema_version=schema_version,
            transform_version=transform_version,
            version_hash=dataset_version_hash(
                dataset_id,
                canonical_snapshot_hashes,
                schema_version,
                transform_version,
                source_ids,
            ),
        )

    @property
    def artifact_content_hashes(self) -> tuple[str, ...]:
        return tuple(sorted(snapshot.content_hash for snapshot in self.artifact_snapshots))

    @property
    def artifact_snapshot_hashes(self) -> tuple[str, ...]:
        return tuple(snapshot.snapshot_hash for snapshot in self.artifact_snapshots)

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(sorted({snapshot.source_id for snapshot in self.artifact_snapshots}))

    @property
    def acquired_at(self) -> datetime:
        return max(snapshot.acquired_at for snapshot in self.artifact_snapshots)

    @property
    def available_at(self) -> datetime:
        return max(snapshot.available_at for snapshot in self.artifact_snapshots)

    @property
    def quality_status(self) -> QualityStatus:
        return _aggregate_quality_status(
            snapshot.quality_status for snapshot in self.artifact_snapshots
        )

    @property
    def provenance(self) -> tuple[ArtifactProvenance, ...]:
        return tuple(snapshot.provenance for snapshot in self.artifact_snapshots)

    @property
    def content_hash(self) -> str:
        """版本本身的内容身份，供通用血缘接口使用。"""

        return self.version_hash


def _validate_metadata(metadata: ArtifactMetadata) -> None:
    if not isinstance(metadata, ArtifactMetadata):
        raise DataDomainError("metadata 必须是 ArtifactMetadata")


def _validate_artifact(artifact: Artifact) -> None:
    if not isinstance(artifact, (RawArtifact, NormalizedArtifact, DerivedArtifact)):
        raise DataDomainError("artifact 必须是 RawArtifact、NormalizedArtifact 或 DerivedArtifact")
    _validate_metadata(artifact.metadata)


def _validate_artifact_kind(actual: ArtifactKind, expected: ArtifactKind) -> None:
    if actual is not expected:
        raise DataDomainError(f"制品 kind 必须是 {expected.value}")


def _checked_hash(value: str, field_name: str) -> str:
    try:
        return require_sha256(value, field_name=field_name)
    except FingerprintError as exc:
        raise DataDomainError(str(exc)) from exc


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataDomainError(f"{field_name} 不能为空")
    return value.strip()


def _safe_reference(value: str, field_name: str) -> str:
    """允许公开 URL 或不透明引用，但拒绝 query/userinfo 与常见凭据文本。"""

    text = _required_text(value, field_name)
    if _SECRET_TEXT_PATTERN.search(text):
        raise DataDomainError(f"{field_name} 不得包含凭据、令牌或授权头")
    _reject_local_absolute_path(text, field_name)
    parsed = urlsplit(text)
    if parsed.scheme.lower() == "file":
        raise DataDomainError(f"{field_name} 不得包含本机绝对路径")
    if parsed.username is not None or parsed.password is not None or parsed.query:
        raise DataDomainError(f"{field_name} 不得包含 URL 用户信息或查询参数")
    return text


def _optional_safe_reference(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _safe_reference(value, field_name)


def _safe_provenance_attribute(value: str, field_name: str) -> str:
    text = _required_text(value, field_name)
    if _SECRET_TEXT_PATTERN.search(text):
        raise DataDomainError(f"{field_name} 不得包含凭据、令牌或授权头")
    _reject_local_absolute_path(text, field_name)
    return text


def _reject_local_absolute_path(text: str, field_name: str) -> None:
    if (
        text.startswith(("/", "\\\\", "~/", "~\\"))
        or re.match(r"^[A-Za-z]:[\\\\/]", text)
        or urlsplit(text).scheme.lower() == "file"
    ):
        raise DataDomainError(f"{field_name} 不得包含本机绝对路径")


def _optional_date(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    text = _required_text(value, field_name)
    try:
        datetime.fromisoformat(f"{text}T00:00:00+00:00")
    except ValueError as exc:
        raise DataDomainError(f"{field_name} 必须是 ISO 日期") from exc
    return text


def _utc_datetime(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DataDomainError(f"{field_name} 必须是带时区的 datetime")
    return value.astimezone(timezone.utc)


def _canonical_content_hashes(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    try:
        canonical = tuple(sorted(_checked_hash(value, field_name) for value in values))
    except TypeError as exc:
        raise DataDomainError(f"{field_name} 必须是 SHA-256 序列") from exc
    if not canonical:
        raise DataDomainError(f"{field_name} 不能为空")
    if len(canonical) != len(set(canonical)):
        raise DataDomainError(f"{field_name} 不能包含重复内容哈希")
    return canonical


def _canonical_texts(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    try:
        canonical = tuple(sorted(_required_text(value, field_name) for value in values))
    except TypeError as exc:
        raise DataDomainError(f"{field_name} 必须是文本序列") from exc
    if not canonical:
        raise DataDomainError(f"{field_name} 不能为空")
    if len(canonical) != len(set(canonical)):
        raise DataDomainError(f"{field_name} 不能包含重复值")
    return canonical


def _aggregate_quality_status(statuses: Iterable[QualityStatus]) -> QualityStatus:
    values = tuple(statuses)
    if not values or any(not isinstance(status, QualityStatus) for status in values):
        raise DataDomainError("质量状态集合不能为空且必须是 QualityStatus")
    return max(values, key=lambda status: _QUALITY_STATUS_PRECEDENCE[status])
