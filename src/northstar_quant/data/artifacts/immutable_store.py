"""追加式、内容寻址的数据制品库。

本模块与 ``storage.py`` 的 legacy market/cache 投影刻意分离：后者使用原子替换以维护当前
可读文件，本模块则只允许创建新对象或验证完全相同的既有对象。它不连接数据库、不维护
``latest`` 指针，也不提供 GC/清理能力；旧制品和中断后留下的不可达对象必须保留，直到用户
在仓库外明确处置。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import errno
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
from typing import cast

from northstar_quant.data.artifacts.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
    content_sha256,
    derived_identity_hash,
    lineage_hash,
    normalization_binding_hash,
    normalization_identity_hash,
    require_sha256,
    snapshot_lineage_hash,
)
from northstar_quant.data.contracts.data_domain import (
    Artifact,
    ArtifactKind,
    ArtifactProvenance,
    ArtifactSnapshot,
    DataDomainError,
    DataLineage,
    DataSource,
    DatasetVersion,
    DerivedArtifact,
    LicenseMetadata,
    NormalizedArtifact,
    QualityStatus,
    RawArtifact,
)
from northstar_quant.data.quality.models import (
    DataQualityError,
    PublishedQualityAssessment,
)
from northstar_quant.data.sources.protocol import (
    AdapterMetadata,
    DataSourceProtocolError,
    PublicationAuthorization,
    PublicationPurpose,
    PublicationScope,
)


ArtifactValue = RawArtifact | NormalizedArtifact | DerivedArtifact

_ARTIFACT_RECORD_FORMAT = "northstar.immutable-artifact-record.v1"
_DATASET_MANIFEST_FORMAT = "northstar.immutable-dataset-manifest.v1"
_NORMALIZATION_BINDING_FORMAT = "northstar.normalization-binding.v1"
_SNAPSHOT_LINEAGE_FORMAT = "northstar.snapshot-lineage.v1"
_QUALITY_ASSESSMENT_FORMAT = "northstar.quality-assessment.v1"
_QUALITY_BINDING_FORMAT = "northstar.quality-assessment-binding.v1"
_PUBLICATION_AUTHORIZATION_FORMAT = "northstar.publication-authorization.v1"
_REPARSE_POINT_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


class ArtifactStoreError(RuntimeError):
    """制品库的安全、完整性或不可变性约束未满足。"""


class ArtifactNotFoundError(ArtifactStoreError):
    """请求的不可变对象不存在，或无法作为完整对象读取。"""


class ArtifactIntegrityConflict(ArtifactStoreError):
    """目标哈希路径已有与本次发布不一致的对象。"""


def _snapshot_for(artifact: object) -> ArtifactSnapshot:
    """从受支持的具体制品生成快照，并收窄 Protocol 的静态类型。"""

    if not isinstance(artifact, (RawArtifact, NormalizedArtifact, DerivedArtifact)):
        raise ArtifactStoreError("制品必须是 RawArtifact、NormalizedArtifact 或 DerivedArtifact")
    # ``Artifact`` 的 Protocol 使用了可写 metadata 字段，而领域制品是 frozen dataclass。
    # 运行时已验证具体类型；这里仅消除该历史 Protocol 定义造成的静态不兼容。
    return ArtifactSnapshot.from_artifact(cast(Artifact, artifact))


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    """已验证并持久化的制品引用；路径只由哈希派生。"""

    snapshot: ArtifactSnapshot
    source: DataSource
    blob_path: Path
    record_path: Path
    byte_length: int
    lineage_snapshot_hash: str | None
    parent_snapshot_hashes: tuple[str, ...]
    quality_assessment_hash: str | None
    publication_authorization_hash: str | None


@dataclass(frozen=True, slots=True)
class ArtifactReplay:
    """按 snapshot 精确读取的内容与元数据。"""

    stored: StoredArtifact
    payload: bytes


@dataclass(frozen=True, slots=True)
class StoredDatasetVersion:
    """已写入终态 manifest 的数据集版本引用。"""

    dataset_version: DatasetVersion
    manifest_path: Path
    quality_assessment_hashes: tuple[str | None, ...]


@dataclass(frozen=True, slots=True)
class StoredQualityAssessment:
    """已追加式保存的最终制品质量证据。"""

    assessment: PublishedQualityAssessment
    record_path: Path
    binding_path: Path


@dataclass(frozen=True, slots=True)
class StoredPublicationAuthorization:
    """已按 hash 保存的、脱敏且范围完整的发布授权收据。"""

    authorization_hash: str
    authorization: dict[str, object]
    record_path: Path


@dataclass(frozen=True, slots=True)
class DatasetReplay:
    """按明确 version hash 回放的完整输入集合。"""

    dataset_version: DatasetVersion
    artifacts: tuple[ArtifactReplay, ...]


@dataclass(frozen=True, slots=True)
class _ArtifactRecord:
    snapshot: ArtifactSnapshot
    source: DataSource
    byte_length: int
    relations: dict[str, object]
    raw_bytes: bytes


class ArtifactStore:
    """文件系统上的不可变制品库。

    ``root`` 必须是绝对路径；正式使用请通过 :meth:`from_settings` 固定到
    ``<storage_dir>/artifacts``。API 不接受用户提供的相对文件名，所有永久路径均只由受验证
    的 SHA-256 派生。
    """

    def __init__(self, root: str | Path) -> None:
        candidate = Path(root).expanduser()
        if not candidate.is_absolute():
            raise ArtifactStoreError("不可变制品库根目录必须是绝对路径")
        if ".." in candidate.parts:
            raise ArtifactStoreError("不可变制品库根目录不得包含 '..' 路径段")
        _assert_safe_existing_ancestors(candidate)
        candidate.mkdir(parents=True, exist_ok=True, mode=0o700)
        root_state = _assert_directory(candidate, "不可变制品库根目录")
        _assert_posix_private_directory(root_state, "不可变制品库根目录")
        self._root = candidate
        for directory in (
            self._root / "blobs" / "sha256",
            self._root / "snapshots" / "sha256",
            self._root / "datasets" / "sha256",
            self._root / "normalization-bindings" / "sha256",
            self._root / "lineage" / "sha256",
            self._root / "quality-assessments" / "sha256",
            self._root / "quality-bindings" / "sha256",
            self._root / "publication-authorizations" / "sha256",
        ):
            self._ensure_directory(directory)

    @classmethod
    def from_settings(cls) -> "ArtifactStore":
        """从运行时 storage 根构造制品库；不读取 downloads 或 legacy market 投影。"""

        from northstar_quant.foundation.config.settings import get_settings

        return cls(get_settings().storage_dir / "artifacts")

    @property
    def root(self) -> Path:
        return self._root

    def blob_path(self, content_hash: str) -> Path:
        digest = _checked_hash(content_hash, "content_hash")
        return self._root / "blobs" / "sha256" / digest[:2] / f"{digest}.blob"

    def snapshot_path(self, snapshot_hash: str) -> Path:
        digest = _checked_hash(snapshot_hash, "snapshot_hash")
        return self._root / "snapshots" / "sha256" / digest[:2] / f"{digest}.json"

    def dataset_manifest_path(self, version_hash: str) -> Path:
        digest = _checked_hash(version_hash, "version_hash")
        return self._root / "datasets" / "sha256" / digest[:2] / f"{digest}.json"

    def normalization_binding_path(self, binding_hash: str) -> Path:
        digest = _checked_hash(binding_hash, "normalization_binding_hash")
        return self._root / "normalization-bindings" / "sha256" / digest[:2] / f"{digest}.json"

    def lineage_path(self, lineage_snapshot_hash: str) -> Path:
        digest = _checked_hash(lineage_snapshot_hash, "snapshot_lineage_hash")
        return self._root / "lineage" / "sha256" / digest[:2] / f"{digest}.json"

    def quality_assessment_path(self, assessment_hash: str) -> Path:
        """返回按 assessment hash 定位的不可变质量记录路径。"""

        digest = _checked_hash(assessment_hash, "quality_assessment_hash")
        return self._root / "quality-assessments" / "sha256" / digest[:2] / f"{digest}.json"

    def quality_binding_path(self, published_snapshot_hash: str) -> Path:
        """返回最终制品 snapshot 到质量证据的唯一绑定路径。"""

        digest = _checked_hash(published_snapshot_hash, "published_snapshot_hash")
        return self._root / "quality-bindings" / "sha256" / digest[:2] / f"{digest}.json"

    def publication_authorization_path(self, authorization_hash: str) -> Path:
        """返回按授权收据 hash 定位的不可变路径。"""

        digest = _checked_hash(authorization_hash, "publication_authorization_hash")
        return (
            self._root
            / "publication-authorizations"
            / "sha256"
            / digest[:2]
            / f"{digest}.json"
        )

    def put_raw(
        self,
        *,
        source: DataSource,
        artifact: RawArtifact,
        payload: bytes,
        authorization: PublicationAuthorization | None = None,
    ) -> StoredArtifact:
        """发布一份已授权的 raw 制品；重复发布同一对象只校验，不覆盖。"""

        authorization_hash = self._store_publication_authorization(source, authorization)
        return self._put_artifact(
            source=source,
            artifact=artifact,
            payload=payload,
            lineage=None,
            publication_authorization_hash=authorization_hash,
        )

    def put_normalized(
        self,
        *,
        source: DataSource,
        artifact: NormalizedArtifact,
        payload: bytes,
        lineage: DataLineage,
        assessment: PublishedQualityAssessment | None = None,
        authorization: PublicationAuthorization | None = None,
    ) -> StoredArtifact:
        """发布标准化制品，并可绑定发布器生成的不可变质量证据。"""

        assessment_hash: str | None = None
        if assessment is not None:
            if not isinstance(assessment, PublishedQualityAssessment):
                raise ArtifactStoreError("assessment 必须是 PublishedQualityAssessment 或 None")
            snapshot = _snapshot_for(artifact)
            if assessment.published_snapshot_hash != snapshot.snapshot_hash:
                raise ArtifactStoreError("assessment 必须精确绑定待发布 normalized snapshot")
            if assessment.aggregate_status is not artifact.quality_status:
                raise ArtifactStoreError("assessment 聚合状态必须与待发布 normalized 质量状态一致")
            self.put_quality_assessment(assessment)
            assessment_hash = assessment.assessment_hash

        authorization_hash = self._store_publication_authorization(source, authorization)
        return self._put_artifact(
            source=source,
            artifact=artifact,
            payload=payload,
            lineage=lineage,
            quality_assessment_hash=assessment_hash,
            publication_authorization_hash=authorization_hash,
        )

    def put_derived(
        self,
        *,
        source: DataSource,
        artifact: DerivedArtifact,
        payload: bytes,
        lineage: DataLineage,
    ) -> StoredArtifact:
        """发布派生制品；所有上游 snapshot 必须已被追加式保存。"""

        return self._put_artifact(
            source=source,
            artifact=artifact,
            payload=payload,
            lineage=lineage,
        )

    def put_quality_assessment(
        self,
        assessment: PublishedQualityAssessment,
    ) -> StoredQualityAssessment:
        """追加式保存一份质量 assessment 及最终 snapshot 的唯一绑定。

        assessment 可以先于最终 normalized record 落盘；若随后发布中断，最多遗留不可达
        证据，绝不会改写或删除任何既有制品。最终 record 会再次验证该绑定。
        """

        if not isinstance(assessment, PublishedQualityAssessment):
            raise ArtifactStoreError("assessment 必须是 PublishedQualityAssessment")
        assessment_payload = {
            "assessment": assessment.as_mapping(),
            "assessment_hash": assessment.assessment_hash,
            "format": _QUALITY_ASSESSMENT_FORMAT,
        }
        record_path = self.quality_assessment_path(assessment.assessment_hash)
        self._write_immutable(
            record_path,
            _canonical_json_bytes(assessment_payload),
            "quality assessment",
        )
        binding_payload = {
            "assessment_hash": assessment.assessment_hash,
            "format": _QUALITY_BINDING_FORMAT,
            "published_snapshot_hash": assessment.published_snapshot_hash,
        }
        binding_path = self.quality_binding_path(assessment.published_snapshot_hash)
        self._write_immutable(
            binding_path,
            _canonical_json_bytes(binding_payload),
            "quality assessment binding",
        )
        return StoredQualityAssessment(
            assessment=assessment,
            record_path=record_path,
            binding_path=binding_path,
        )

    def load_quality_assessment(self, published_snapshot_hash: str) -> StoredQualityAssessment:
        """读取并验证最终 snapshot 所绑定的质量证据，不信任文件名或可变状态。"""

        snapshot_hash = _checked_hash(published_snapshot_hash, "published_snapshot_hash")
        binding_path = self.quality_binding_path(snapshot_hash)
        binding = _load_canonical_mapping(
            self._read_immutable_bytes(binding_path, "quality assessment binding"),
            "quality assessment binding",
        )
        _require_exact_keys(
            binding,
            {"assessment_hash", "format", "published_snapshot_hash"},
            "quality assessment binding",
        )
        if binding["format"] != _QUALITY_BINDING_FORMAT:
            raise ArtifactIntegrityConflict("quality assessment binding 格式不受支持")
        if _required_hash(
            binding["published_snapshot_hash"], "published_snapshot_hash"
        ) != snapshot_hash:
            raise ArtifactIntegrityConflict("quality assessment binding 的 snapshot 身份不一致")
        assessment_hash = _required_hash(binding["assessment_hash"], "assessment_hash")
        record_path = self.quality_assessment_path(assessment_hash)
        record = _load_canonical_mapping(
            self._read_immutable_bytes(record_path, "quality assessment"),
            "quality assessment",
        )
        _require_exact_keys(
            record,
            {"assessment", "assessment_hash", "format"},
            "quality assessment",
        )
        if record["format"] != _QUALITY_ASSESSMENT_FORMAT:
            raise ArtifactIntegrityConflict("quality assessment 格式不受支持")
        if _required_hash(record["assessment_hash"], "assessment_hash") != assessment_hash:
            raise ArtifactIntegrityConflict("quality assessment 文件名与 assessment_hash 不一致")
        assessment_payload = _require_mapping(record["assessment"], "quality assessment.assessment")
        try:
            assessment = PublishedQualityAssessment.from_mapping(assessment_payload)
        except DataQualityError as exc:
            raise ArtifactIntegrityConflict("quality assessment 无法重建领域对象") from exc
        if assessment.assessment_hash != assessment_hash:
            raise ArtifactIntegrityConflict("quality assessment 内容哈希不一致")
        if assessment.published_snapshot_hash != snapshot_hash:
            raise ArtifactIntegrityConflict("quality assessment 与最终 snapshot 不一致")
        return StoredQualityAssessment(
            assessment=assessment,
            record_path=record_path,
            binding_path=binding_path,
        )

    def put_publication_authorization(
        self,
        authorization: PublicationAuthorization,
    ) -> StoredPublicationAuthorization:
        """追加式保存发布前由当前配置核验出的无密钥授权收据。"""

        if not isinstance(authorization, PublicationAuthorization):
            raise ArtifactStoreError("authorization 必须是 PublicationAuthorization")
        authorization_payload = authorization.as_mapping()
        if canonical_json_sha256(authorization_payload) != authorization.authorization_hash:
            raise ArtifactIntegrityConflict("publication authorization 内容哈希不一致")
        record_payload = {
            "authorization": authorization_payload,
            "authorization_hash": authorization.authorization_hash,
            "format": _PUBLICATION_AUTHORIZATION_FORMAT,
        }
        record_path = self.publication_authorization_path(authorization.authorization_hash)
        self._write_immutable(
            record_path,
            _canonical_json_bytes(record_payload),
            "publication authorization",
        )
        return StoredPublicationAuthorization(
            authorization_hash=authorization.authorization_hash,
            authorization=authorization_payload,
            record_path=record_path,
        )

    def load_publication_authorization(
        self,
        authorization_hash: str,
    ) -> StoredPublicationAuthorization:
        """读取并校验历史授权收据；回放不依赖今天的配置文件。"""

        digest = _checked_hash(authorization_hash, "publication_authorization_hash")
        record_path = self.publication_authorization_path(digest)
        record = _load_canonical_mapping(
            self._read_immutable_bytes(record_path, "publication authorization"),
            "publication authorization",
        )
        _require_exact_keys(
            record,
            {"authorization", "authorization_hash", "format"},
            "publication authorization",
        )
        if record["format"] != _PUBLICATION_AUTHORIZATION_FORMAT:
            raise ArtifactIntegrityConflict("publication authorization 格式不受支持")
        if _required_hash(record["authorization_hash"], "authorization_hash") != digest:
            raise ArtifactIntegrityConflict("publication authorization 文件名与 hash 不一致")
        authorization = _require_mapping(record["authorization"], "publication authorization.authorization")
        if canonical_json_sha256(authorization) != digest:
            raise ArtifactIntegrityConflict("publication authorization 内容哈希不一致")
        self._validate_publication_authorization_payload(authorization)
        return StoredPublicationAuthorization(
            authorization_hash=digest,
            authorization=authorization,
            record_path=record_path,
        )

    def put_dataset_version(
        self,
        dataset_version: DatasetVersion,
        *,
        require_quality_assessments: bool = False,
    ) -> StoredDatasetVersion:
        """发布数据集终态 manifest；它只引用已验证的 snapshot，不维护可变 current 指针。"""

        if not isinstance(dataset_version, DatasetVersion):
            raise ArtifactStoreError("dataset_version 必须是 DatasetVersion")
        if type(require_quality_assessments) is not bool:
            raise ArtifactStoreError("require_quality_assessments 必须是 bool")

        records: list[dict[str, object]] = []
        quality_assessment_hashes: list[str | None] = []
        for snapshot in dataset_version.artifact_snapshots:
            if snapshot.quality_status in {QualityStatus.FAIL, QualityStatus.UNKNOWN}:
                raise ArtifactStoreError(
                    "FAIL 或 UNKNOWN 质量状态的制品不能发布为可研究回放的 DatasetVersion"
                )
            stored = self.load_artifact(snapshot.snapshot_hash)
            if stored.snapshot != snapshot:
                raise ArtifactIntegrityConflict("DatasetVersion 引用的 snapshot 与持久化记录不一致")
            record_bytes = self._read_immutable_bytes(stored.record_path, "snapshot record")
            if require_quality_assessments and stored.quality_assessment_hash is None:
                raise ArtifactStoreError(
                    "发布器数据集要求每个制品都有不可变 quality assessment"
                )
            records.append(
                {
                    "lineage_snapshot_hash": stored.lineage_snapshot_hash,
                    "publication_authorization_hash": stored.publication_authorization_hash,
                    "quality_assessment_hash": stored.quality_assessment_hash,
                    "record_sha256": content_sha256(record_bytes, field_name="snapshot record"),
                    "snapshot_hash": snapshot.snapshot_hash,
                }
            )
            quality_assessment_hashes.append(stored.quality_assessment_hash)

        payload = {
            "artifact_records": records,
            "dataset_version": {
                "artifact_snapshot_hashes": list(dataset_version.artifact_snapshot_hashes),
                "dataset_id": dataset_version.dataset_id,
                "schema_version": dataset_version.schema_version,
                "transform_version": dataset_version.transform_version,
                "version_hash": dataset_version.version_hash,
            },
            "format": _DATASET_MANIFEST_FORMAT,
        }
        path = self.dataset_manifest_path(dataset_version.version_hash)
        self._write_immutable(path, _canonical_json_bytes(payload), "DatasetVersion manifest")
        return StoredDatasetVersion(
            dataset_version=dataset_version,
            manifest_path=path,
            quality_assessment_hashes=tuple(quality_assessment_hashes),
        )

    def load_artifact(self, snapshot_hash: str) -> StoredArtifact:
        """读取并全面校验一份 artifact record、blob、来源快照和上游关系。"""

        return self._load_artifact(snapshot_hash, visiting=frozenset())

    def _load_artifact(
        self,
        snapshot_hash: str,
        *,
        visiting: frozenset[str],
    ) -> StoredArtifact:
        """递归校验一份制品及其完整上游图，并拒绝磁盘伪造的环。"""

        digest = _checked_hash(snapshot_hash, "snapshot_hash")
        if digest in visiting:
            raise ArtifactIntegrityConflict("artifact record 上游关系存在循环，已拒绝回放")
        next_visiting = visiting | {digest}
        record = self._load_artifact_record(digest)
        blob_path = self.blob_path(record.snapshot.content_hash)
        blob = self._read_immutable_bytes(blob_path, "artifact blob")
        if len(blob) != record.byte_length:
            raise ArtifactIntegrityConflict("artifact blob 长度与不可变 record 不一致")
        if content_sha256(blob, field_name="artifact blob") != record.snapshot.content_hash:
            raise ArtifactIntegrityConflict("artifact blob 内容哈希与 snapshot 不一致")

        lineage_snapshot_hash = _relation_lineage_hash(record.relations)
        parent_hashes = _relation_parent_hashes(record.snapshot.kind, record.relations)
        parent_stored = tuple(
            self._load_artifact(parent_hash, visiting=next_visiting)
            for parent_hash in parent_hashes
        )
        parent_records = tuple(
            self._load_artifact_record(parent.snapshot.snapshot_hash) for parent in parent_stored
        )
        self._validate_record_relations(record, parent_records)
        if lineage_snapshot_hash is not None:
            lineage = self._load_lineage_record(lineage_snapshot_hash)
            self._validate_lineage_record(record, parent_records, lineage)
        elif record.snapshot.kind is not ArtifactKind.RAW:
            raise ArtifactIntegrityConflict("非 raw 制品缺少 snapshot-level lineage")

        if record.snapshot.kind is ArtifactKind.NORMALIZED:
            self._validate_normalization_binding(record, parent_records)
            quality_assessment_hash = _optional_hash(
                record.relations["quality_assessment_hash"], "quality_assessment_hash"
            )
            if quality_assessment_hash is not None:
                self._validate_quality_assessment_for_record(record, quality_assessment_hash)
        else:
            quality_assessment_hash = None
        publication_authorization_hash = _relation_publication_authorization_hash(
            record.snapshot.kind,
            record.relations,
        )
        if publication_authorization_hash is not None:
            self._validate_publication_authorization_for_record(
                record,
                publication_authorization_hash,
            )

        return StoredArtifact(
            snapshot=record.snapshot,
            source=record.source,
            blob_path=blob_path,
            record_path=self.snapshot_path(record.snapshot.snapshot_hash),
            byte_length=record.byte_length,
            lineage_snapshot_hash=lineage_snapshot_hash,
            # 这是已经通过递归 record/blob/lineage 校验的直接上游身份，供 Application
            # 在组合日历、规则等受控事实时绑定来源制品；不暴露可变路径或关系字典。
            parent_snapshot_hashes=parent_hashes,
            quality_assessment_hash=quality_assessment_hash,
            publication_authorization_hash=publication_authorization_hash,
        )

    def read_payload(self, snapshot_hash: str) -> bytes:
        """读取并复核特定 snapshot 的不可变字节内容。"""

        stored = self.load_artifact(snapshot_hash)
        return self._read_immutable_bytes(stored.blob_path, "artifact blob")

    def load_dataset_version(self, version_hash: str) -> DatasetVersion:
        """读取终态 dataset manifest，并逐项验证 record、lineage 和 blob。"""

        digest = _checked_hash(version_hash, "version_hash")
        payload = _load_canonical_mapping(
            self._read_immutable_bytes(
                self.dataset_manifest_path(digest), "DatasetVersion manifest"
            ),
            "DatasetVersion manifest",
        )
        _require_exact_keys(
            payload, {"artifact_records", "dataset_version", "format"}, "DatasetVersion manifest"
        )
        if payload["format"] != _DATASET_MANIFEST_FORMAT:
            raise ArtifactIntegrityConflict("DatasetVersion manifest 格式不受支持")
        version_payload = _require_mapping(payload["dataset_version"], "dataset_version")
        _require_exact_keys(
            version_payload,
            {
                "artifact_snapshot_hashes",
                "dataset_id",
                "schema_version",
                "transform_version",
                "version_hash",
            },
            "dataset_version",
        )
        snapshot_hashes = _require_hash_list(
            version_payload["artifact_snapshot_hashes"],
            "dataset_version.artifact_snapshot_hashes",
        )
        if version_payload["version_hash"] != digest:
            raise ArtifactIntegrityConflict("DatasetVersion manifest 文件名与 version_hash 不一致")
        artifact_records = _require_list(payload["artifact_records"], "artifact_records")
        if len(artifact_records) != len(snapshot_hashes):
            raise ArtifactIntegrityConflict("DatasetVersion manifest 的 record 数量不一致")

        snapshots: list[ArtifactSnapshot] = []
        for snapshot_hash, entry in zip(snapshot_hashes, artifact_records, strict=True):
            entry_payload = _require_mapping(entry, "artifact_records[]")
            _require_exact_keys(
                entry_payload,
                {
                    "lineage_snapshot_hash",
                    "publication_authorization_hash",
                    "quality_assessment_hash",
                    "record_sha256",
                    "snapshot_hash",
                },
                "artifact_records[]",
            )
            if (
                _required_hash(entry_payload["snapshot_hash"], "artifact_records[].snapshot_hash")
                != snapshot_hash
            ):
                raise ArtifactIntegrityConflict(
                    "DatasetVersion manifest 的 snapshot 顺序或引用不一致"
                )
            stored = self.load_artifact(snapshot_hash)
            if stored.snapshot.quality_status in {QualityStatus.FAIL, QualityStatus.UNKNOWN}:
                raise ArtifactIntegrityConflict("DatasetVersion 包含不可研究回放的质量状态")
            if (
                _optional_hash(entry_payload["lineage_snapshot_hash"], "lineage_snapshot_hash")
                != stored.lineage_snapshot_hash
            ):
                raise ArtifactIntegrityConflict("DatasetVersion manifest 的 lineage 引用不一致")
            if (
                _optional_hash(
                    entry_payload["quality_assessment_hash"], "quality_assessment_hash"
                )
                != stored.quality_assessment_hash
            ):
                raise ArtifactIntegrityConflict("DatasetVersion manifest 的 quality assessment 引用不一致")
            if (
                _optional_hash(
                    entry_payload["publication_authorization_hash"],
                    "publication_authorization_hash",
                )
                != stored.publication_authorization_hash
            ):
                raise ArtifactIntegrityConflict("DatasetVersion manifest 的 publication authorization 引用不一致")
            record_bytes = self._read_immutable_bytes(stored.record_path, "snapshot record")
            if _required_hash(entry_payload["record_sha256"], "record_sha256") != content_sha256(
                record_bytes,
                field_name="snapshot record",
            ):
                raise ArtifactIntegrityConflict("DatasetVersion manifest 的 record 哈希不一致")
            snapshots.append(stored.snapshot)

        try:
            version = DatasetVersion(
                dataset_id=_required_text(version_payload["dataset_id"], "dataset_id"),
                artifact_snapshots=tuple(snapshots),
                schema_version=_required_text(version_payload["schema_version"], "schema_version"),
                transform_version=_required_text(
                    version_payload["transform_version"],
                    "transform_version",
                ),
                version_hash=_required_hash(version_payload["version_hash"], "version_hash"),
            )
        except DataDomainError as exc:
            raise ArtifactIntegrityConflict("DatasetVersion manifest 无法重建领域对象") from exc
        if version.artifact_snapshot_hashes != snapshot_hashes:
            raise ArtifactIntegrityConflict("DatasetVersion manifest 的 snapshot 集合不一致")
        return version

    def replay_dataset_version(self, version_hash: str) -> DatasetReplay:
        """按 version hash 返回精确 snapshot 与原始字节；不执行“最新”或 as-of 推断。"""

        dataset_version = self.load_dataset_version(version_hash)
        artifacts = tuple(
            ArtifactReplay(
                stored=self.load_artifact(snapshot.snapshot_hash),
                payload=self.read_payload(snapshot.snapshot_hash),
            )
            for snapshot in dataset_version.artifact_snapshots
        )
        return DatasetReplay(dataset_version=dataset_version, artifacts=artifacts)

    def _put_artifact(
        self,
        *,
        source: DataSource,
        artifact: ArtifactValue,
        payload: bytes,
        lineage: DataLineage | None,
        quality_assessment_hash: str | None = None,
        publication_authorization_hash: str | None = None,
    ) -> StoredArtifact:
        if not isinstance(artifact, (RawArtifact, NormalizedArtifact, DerivedArtifact)):
            raise ArtifactStoreError(
                "artifact 必须是 RawArtifact、NormalizedArtifact 或 DerivedArtifact"
            )
        if not isinstance(source, DataSource):
            raise ArtifactStoreError("source 必须是 DataSource")
        if not isinstance(payload, bytes):
            raise ArtifactStoreError("payload 必须是 bytes")
        if source.source_id != artifact.source_id:
            raise ArtifactStoreError("DataSource.source_id 必须与 artifact.source_id 一致")
        if quality_assessment_hash is not None:
            if not isinstance(artifact, NormalizedArtifact):
                raise ArtifactStoreError("quality assessment 只能绑定 normalized 制品")
            self._assert_quality_assessment_matches_artifact(artifact, quality_assessment_hash)
        if publication_authorization_hash is not None:
            self._assert_publication_authorization_matches_source(
                source,
                publication_authorization_hash,
            )
        self._assert_publication_authorization(source, artifact)
        if content_sha256(payload, field_name="payload") != artifact.content_hash:
            raise ArtifactIntegrityConflict("payload 内容哈希必须与 artifact.content_hash 一致")

        snapshot = _snapshot_for(artifact)
        parent_records = self._validate_parent_records(artifact)
        lineage_payload, lineage_digest = self._prepare_lineage(
            artifact, snapshot, lineage, parent_records
        )
        relations = self._relations_for(
            artifact,
            lineage_digest,
            quality_assessment_hash,
            publication_authorization_hash,
        )
        record_payload = {
            "byte_length": len(payload),
            "format": _ARTIFACT_RECORD_FORMAT,
            "relations": relations,
            "snapshot": _snapshot_payload(snapshot),
            "source": _source_payload(source),
        }

        if isinstance(artifact, NormalizedArtifact):
            self._assert_normalization_binding_is_compatible(artifact, snapshot)

        blob_path = self.blob_path(snapshot.content_hash)
        self._write_immutable(blob_path, payload, "artifact blob")
        # 绑定是同一 raw snapshot + transform/schema 的唯一约束。必须早于 record：并发的
        # 不同输出在这里失败，只会留下不可见 blob，不会留下看似可读取却无有效绑定的 record。
        if isinstance(artifact, NormalizedArtifact):
            self._write_normalization_binding(artifact, snapshot)
        record_path = self.snapshot_path(snapshot.snapshot_hash)
        self._write_immutable(record_path, _canonical_json_bytes(record_payload), "artifact record")
        if lineage_payload is not None and lineage_digest is not None:
            self._write_immutable(
                self.lineage_path(lineage_digest),
                _canonical_json_bytes(lineage_payload),
                "snapshot lineage",
            )
        return self.load_artifact(snapshot.snapshot_hash)

    def _assert_publication_authorization(
        self,
        source: DataSource,
        artifact: ArtifactValue,
    ) -> None:
        """仅在发布时确认来源授权；回放历史证据不依赖今天的授权状态。"""

        license_metadata = source.license
        if source.status != "active" or license_metadata.status != "active":
            raise ArtifactStoreError("数据源和授权状态必须均为 active 才能发布制品")
        if "internal_research" not in license_metadata.permitted_purposes:
            raise ArtifactStoreError("数据源授权未包含 internal_research 用途")
        if license_metadata.contract_reference is None or license_metadata.terms_sha256 is None:
            raise ArtifactStoreError("数据源授权缺少可审计的合同引用或条款哈希")
        if license_metadata.effective_from is None or license_metadata.expires_on is None:
            raise ArtifactStoreError("数据源授权缺少有效期，已拒绝发布")
        try:
            effective_from = date.fromisoformat(license_metadata.effective_from)
            expires_on = date.fromisoformat(license_metadata.expires_on)
        except ValueError as exc:  # 领域模型通常已校验；此处保留存储边界的失败关闭。
            raise ArtifactStoreError("数据源授权有效期格式不安全") from exc
        if not effective_from <= artifact.acquired_at.date() <= expires_on:
            raise ArtifactStoreError("制品 acquired_at 不在数据源授权有效期内")
        if artifact.kind is ArtifactKind.DERIVED:
            if not license_metadata.allows_derived_data_storage:
                raise ArtifactStoreError("数据源授权不允许保存派生制品")
        elif not license_metadata.allows_internal_storage:
            raise ArtifactStoreError("数据源授权不允许内部保存 raw 或 normalized 制品")

    def _validate_parent_records(self, artifact: ArtifactValue) -> tuple[_ArtifactRecord, ...]:
        if isinstance(artifact, RawArtifact):
            return ()
        if isinstance(artifact, NormalizedArtifact):
            parent_snapshots: tuple[ArtifactSnapshot, ...] = (_snapshot_for(artifact.raw_artifact),)
        else:
            parent_snapshots = tuple(_snapshot_for(item) for item in artifact.input_artifacts)
        # 发布子制品前必须验证完整上游（record、blob、lineage、binding），不能只确认
        # record 文件存在；否则上游 blob 被篡改后仍可能继续形成新的可回放制品。
        validated_parents = tuple(
            self.load_artifact(snapshot.snapshot_hash) for snapshot in parent_snapshots
        )
        records = tuple(
            self._load_artifact_record(parent.snapshot.snapshot_hash)
            for parent in validated_parents
        )
        if isinstance(artifact, DerivedArtifact):
            for record in records:
                if not record.source.license.allows_derived_data_storage:
                    raise ArtifactStoreError("上游数据源授权不允许保存派生制品")
        return records

    def _prepare_lineage(
        self,
        artifact: ArtifactValue,
        snapshot: ArtifactSnapshot,
        lineage: DataLineage | None,
        parent_records: tuple[_ArtifactRecord, ...],
    ) -> tuple[dict[str, object] | None, str | None]:
        if isinstance(artifact, RawArtifact):
            if lineage is not None:
                raise ArtifactStoreError("RawArtifact 不得附带 DataLineage")
            return None, None
        if not isinstance(lineage, DataLineage):
            raise ArtifactStoreError("normalized 或 derived 制品必须附带 DataLineage")
        output_snapshot = _snapshot_for(lineage.output_artifact)
        if output_snapshot != snapshot:
            raise ArtifactStoreError("DataLineage.output_artifact 必须与待发布 artifact 精确一致")
        input_snapshots = tuple(_snapshot_for(item) for item in lineage.input_artifacts)
        parent_hashes = tuple(record.snapshot.snapshot_hash for record in parent_records)
        input_hashes = tuple(item.snapshot_hash for item in input_snapshots)
        if input_hashes != parent_hashes:
            raise ArtifactStoreError(
                "DataLineage 输入必须与 artifact 的持久化上游 snapshot 精确一致"
            )
        digest = snapshot_lineage_hash(
            snapshot.snapshot_hash,
            input_hashes,
            lineage.transform_version,
        )
        return (
            {
                "content_lineage_identity": lineage.lineage_identity,
                "format": _SNAPSHOT_LINEAGE_FORMAT,
                "input_snapshot_hashes": list(input_hashes),
                "output_snapshot_hash": snapshot.snapshot_hash,
                "recorded_at": lineage.recorded_at.isoformat(),
                "snapshot_lineage_hash": digest,
                "transform_version": lineage.transform_version,
            },
            digest,
        )

    def _relations_for(
        self,
        artifact: ArtifactValue,
        lineage_digest: str | None,
        quality_assessment_hash: str | None,
        publication_authorization_hash: str | None,
    ) -> dict[str, object]:
        if isinstance(artifact, RawArtifact):
            if quality_assessment_hash is not None:
                raise ArtifactStoreError("raw 制品不得绑定 quality assessment")
            return {
                "lineage_snapshot_hash": None,
                "publication_authorization_hash": publication_authorization_hash,
                "raw_format": artifact.raw_format,
            }
        if isinstance(artifact, NormalizedArtifact):
            return {
                "lineage_snapshot_hash": lineage_digest,
                "normalization_identity": artifact.normalization_identity,
                "publication_authorization_hash": publication_authorization_hash,
                "quality_assessment_hash": quality_assessment_hash,
                "raw_snapshot_hash": _snapshot_for(artifact.raw_artifact).snapshot_hash,
            }
        if quality_assessment_hash is not None or publication_authorization_hash is not None:
            raise ArtifactStoreError("derived 制品暂不支持直接绑定发布授权或 quality assessment")
        return {
            "derivation_identity": artifact.derivation_identity,
            "input_snapshot_hashes": [
                _snapshot_for(item).snapshot_hash for item in artifact.input_artifacts
            ],
            "lineage_snapshot_hash": lineage_digest,
        }

    def _assert_quality_assessment_matches_artifact(
        self,
        artifact: NormalizedArtifact,
        quality_assessment_hash: str,
    ) -> None:
        """在写最终 record 前复核先前追加的 assessment binding。"""

        digest = _checked_hash(quality_assessment_hash, "quality_assessment_hash")
        snapshot = _snapshot_for(artifact)
        assessment = self.load_quality_assessment(snapshot.snapshot_hash).assessment
        if assessment.assessment_hash != digest:
            raise ArtifactIntegrityConflict("quality assessment binding 与传入 hash 不一致")
        if assessment.aggregate_status is not artifact.quality_status:
            raise ArtifactIntegrityConflict("quality assessment 聚合状态与 normalized snapshot 不一致")

    def _validate_quality_assessment_for_record(
        self,
        record: _ArtifactRecord,
        quality_assessment_hash: str,
    ) -> None:
        assessment = self.load_quality_assessment(record.snapshot.snapshot_hash).assessment
        if assessment.assessment_hash != quality_assessment_hash:
            raise ArtifactIntegrityConflict("normalized record 的 quality assessment hash 不一致")
        if assessment.aggregate_status is not record.snapshot.quality_status:
            raise ArtifactIntegrityConflict("normalized record 的质量状态与 assessment 不一致")

    def _store_publication_authorization(
        self,
        source: DataSource,
        authorization: PublicationAuthorization | None,
    ) -> str | None:
        if authorization is None:
            return None
        if not isinstance(authorization, PublicationAuthorization):
            raise ArtifactStoreError("authorization 必须是 PublicationAuthorization 或 None")
        if authorization.source != source:
            raise ArtifactStoreError("publication authorization 的 DataSource 必须与制品 source 一致")
        self.put_publication_authorization(authorization)
        return authorization.authorization_hash

    def _assert_publication_authorization_matches_source(
        self,
        source: DataSource,
        authorization_hash: str,
    ) -> None:
        receipt = self.load_publication_authorization(authorization_hash)
        source_payload = _require_mapping(receipt.authorization["source"], "authorization.source")
        try:
            receipt_source = _source_from_payload(source_payload)
        except ArtifactIntegrityConflict as exc:
            raise ArtifactIntegrityConflict("publication authorization 的 DataSource 无效") from exc
        if receipt_source != source:
            raise ArtifactIntegrityConflict("publication authorization 与制品 source 不一致")

    def _validate_publication_authorization_for_record(
        self,
        record: _ArtifactRecord,
        authorization_hash: str,
    ) -> None:
        self._assert_publication_authorization_matches_source(record.source, authorization_hash)

    @staticmethod
    def _validate_publication_authorization_payload(payload: dict[str, object]) -> None:
        """重建协议值对象以拒绝篡改、未知字段及任何非 canonical 授权收据。"""

        _require_exact_keys(payload, {"adapter_metadata", "authorized_at", "scope", "source"}, "authorization")
        metadata_payload = _require_mapping(payload["adapter_metadata"], "authorization.adapter_metadata")
        _require_exact_keys(
            metadata_payload,
            {
                "adapter_id",
                "implementation_version",
                "normalized_format",
                "normalized_schema_version",
                "raw_format",
                "transform_version",
            },
            "authorization.adapter_metadata",
        )
        scope_payload = _require_mapping(payload["scope"], "authorization.scope")
        _require_exact_keys(
            scope_payload,
            {
                "actual_contract_data",
                "asset_type",
                "dataset_id",
                "environment",
                "exchanges",
                "frequency",
                "market",
                "products",
                "purpose",
                "requires_authoritative_calendar",
                "requires_authoritative_dynamic_rules",
            },
            "authorization.scope",
        )
        try:
            metadata = AdapterMetadata(
                adapter_id=_required_text(metadata_payload["adapter_id"], "authorization.adapter_id"),
                implementation_version=_required_text(
                    metadata_payload["implementation_version"], "authorization.implementation_version"
                ),
                raw_format=_required_text(metadata_payload["raw_format"], "authorization.raw_format"),
                normalized_schema_version=_required_text(
                    metadata_payload["normalized_schema_version"],
                    "authorization.normalized_schema_version",
                ),
                transform_version=_required_text(
                    metadata_payload["transform_version"], "authorization.transform_version"
                ),
                normalized_format=_required_text(
                    metadata_payload["normalized_format"], "authorization.normalized_format"
                ),
            )
            scope = PublicationScope(
                dataset_id=_required_text(scope_payload["dataset_id"], "authorization.dataset_id"),
                market=_required_text(scope_payload["market"], "authorization.market"),
                asset_type=_required_text(scope_payload["asset_type"], "authorization.asset_type"),
                frequency=_required_text(scope_payload["frequency"], "authorization.frequency"),
                purpose=PublicationPurpose(
                    _required_text(scope_payload["purpose"], "authorization.purpose")
                ),
                environment=_required_text(scope_payload["environment"], "authorization.environment"),
                exchanges=tuple(
                    _required_text(item, "authorization.exchanges")
                    for item in _require_list(scope_payload["exchanges"], "authorization.exchanges")
                ),
                products=tuple(
                    _required_text(item, "authorization.products")
                    for item in _require_list(scope_payload["products"], "authorization.products")
                ),
                actual_contract_data=_required_bool(
                    scope_payload["actual_contract_data"], "authorization.actual_contract_data"
                ),
                requires_authoritative_calendar=_required_bool(
                    scope_payload["requires_authoritative_calendar"],
                    "authorization.requires_authoritative_calendar",
                ),
                requires_authoritative_dynamic_rules=_required_bool(
                    scope_payload["requires_authoritative_dynamic_rules"],
                    "authorization.requires_authoritative_dynamic_rules",
                ),
            )
            source = _source_from_payload(
                _require_mapping(payload["source"], "authorization.source")
            )
            authorization = PublicationAuthorization(
                source=source,
                scope=scope,
                adapter_metadata=metadata,
                authorized_at=_utc_datetime(payload["authorized_at"], "authorization.authorized_at"),
            )
        except (ArtifactIntegrityConflict, DataSourceProtocolError, ValueError) as exc:
            raise ArtifactIntegrityConflict("publication authorization 无法重建协议对象") from exc
        if authorization.as_mapping() != payload:
            raise ArtifactIntegrityConflict("publication authorization 不是唯一 canonical 记录")

    def _assert_normalization_binding_is_compatible(
        self,
        artifact: NormalizedArtifact,
        snapshot: ArtifactSnapshot,
    ) -> None:
        raw_snapshot_hash = _snapshot_for(artifact.raw_artifact).snapshot_hash
        binding_hash = normalization_binding_hash(
            raw_snapshot_hash,
            artifact.transform_version,
            artifact.schema_version,
        )
        path = self.normalization_binding_path(binding_hash)
        existing = self._read_immutable_bytes_if_exists(path, "normalization binding")
        if existing is None:
            return
        payload = _load_canonical_mapping(existing, "normalization binding")
        self._validate_normalization_binding_payload(
            payload,
            raw_snapshot_hash=raw_snapshot_hash,
            transform_version=artifact.transform_version,
            schema_version=artifact.schema_version,
            expected_content_hash=snapshot.content_hash,
        )

    def _write_normalization_binding(
        self,
        artifact: NormalizedArtifact,
        snapshot: ArtifactSnapshot,
    ) -> None:
        raw_snapshot_hash = _snapshot_for(artifact.raw_artifact).snapshot_hash
        binding_hash = normalization_binding_hash(
            raw_snapshot_hash,
            artifact.transform_version,
            artifact.schema_version,
        )
        payload = {
            "binding_hash": binding_hash,
            "format": _NORMALIZATION_BINDING_FORMAT,
            "normalized_content_hash": snapshot.content_hash,
            "raw_snapshot_hash": raw_snapshot_hash,
            "schema_version": artifact.schema_version,
            "transform_version": artifact.transform_version,
        }
        self._write_immutable(
            self.normalization_binding_path(binding_hash),
            _canonical_json_bytes(payload),
            "normalization binding",
        )

    def _validate_normalization_binding(
        self,
        record: _ArtifactRecord,
        parents: tuple[_ArtifactRecord, ...],
    ) -> None:
        if len(parents) != 1:
            raise ArtifactIntegrityConflict("normalized record 必须恰有一个 raw 上游")
        raw_snapshot_hash = parents[0].snapshot.snapshot_hash
        transform_version = record.snapshot.transform_version
        schema_version = record.snapshot.schema_version
        binding_hash = normalization_binding_hash(
            raw_snapshot_hash, transform_version, schema_version
        )
        payload = _load_canonical_mapping(
            self._read_immutable_bytes(
                self.normalization_binding_path(binding_hash), "normalization binding"
            ),
            "normalization binding",
        )
        self._validate_normalization_binding_payload(
            payload,
            raw_snapshot_hash=raw_snapshot_hash,
            transform_version=transform_version,
            schema_version=schema_version,
            expected_content_hash=record.snapshot.content_hash,
        )

    def _validate_normalization_binding_payload(
        self,
        payload: dict[str, object],
        *,
        raw_snapshot_hash: str,
        transform_version: str,
        schema_version: str,
        expected_content_hash: str,
    ) -> None:
        _require_exact_keys(
            payload,
            {
                "binding_hash",
                "format",
                "normalized_content_hash",
                "raw_snapshot_hash",
                "schema_version",
                "transform_version",
            },
            "normalization binding",
        )
        if payload["format"] != _NORMALIZATION_BINDING_FORMAT:
            raise ArtifactIntegrityConflict("normalization binding 格式不受支持")
        actual_binding_hash = _required_hash(payload["binding_hash"], "binding_hash")
        expected_binding_hash = normalization_binding_hash(
            raw_snapshot_hash,
            transform_version,
            schema_version,
        )
        if actual_binding_hash != expected_binding_hash:
            raise ArtifactIntegrityConflict("normalization binding hash 不一致")
        if _required_hash(payload["raw_snapshot_hash"], "raw_snapshot_hash") != raw_snapshot_hash:
            raise ArtifactIntegrityConflict("normalization binding raw snapshot 不一致")
        if _required_text(payload["transform_version"], "transform_version") != transform_version:
            raise ArtifactIntegrityConflict("normalization binding transform version 不一致")
        if _required_text(payload["schema_version"], "schema_version") != schema_version:
            raise ArtifactIntegrityConflict("normalization binding schema version 不一致")
        actual_content_hash = _required_hash(
            payload["normalized_content_hash"],
            "normalized_content_hash",
        )
        if actual_content_hash != expected_content_hash:
            raise ArtifactIntegrityConflict(
                "同一 raw snapshot 与 transform/schema 已绑定不同 normalized 内容"
            )

    def _load_artifact_record(self, snapshot_hash: str) -> _ArtifactRecord:
        digest = _checked_hash(snapshot_hash, "snapshot_hash")
        raw_bytes = self._read_immutable_bytes(self.snapshot_path(digest), "artifact record")
        payload = _load_canonical_mapping(raw_bytes, "artifact record")
        _require_exact_keys(
            payload,
            {"byte_length", "format", "relations", "snapshot", "source"},
            "artifact record",
        )
        if payload["format"] != _ARTIFACT_RECORD_FORMAT:
            raise ArtifactIntegrityConflict("artifact record 格式不受支持")
        snapshot = _snapshot_from_payload(_require_mapping(payload["snapshot"], "snapshot"))
        if snapshot.snapshot_hash != digest:
            raise ArtifactIntegrityConflict("artifact record 文件名与 snapshot_hash 不一致")
        source = _source_from_payload(_require_mapping(payload["source"], "source"))
        if source.source_id != snapshot.source_id:
            raise ArtifactIntegrityConflict(
                "artifact record 的 DataSource 与 snapshot source_id 不一致"
            )
        byte_length = _required_nonnegative_int(payload["byte_length"], "byte_length")
        relations = _require_mapping(payload["relations"], "relations")
        _validate_relation_shape(snapshot.kind, relations)
        return _ArtifactRecord(
            snapshot=snapshot,
            source=source,
            byte_length=byte_length,
            relations=relations,
            raw_bytes=raw_bytes,
        )

    def _validate_record_relations(
        self,
        record: _ArtifactRecord,
        parents: tuple[_ArtifactRecord, ...],
    ) -> None:
        kind = record.snapshot.kind
        if kind is ArtifactKind.RAW:
            if parents:
                raise ArtifactIntegrityConflict("raw record 不得具有上游 snapshot")
            return
        if kind is ArtifactKind.NORMALIZED:
            if len(parents) != 1 or parents[0].snapshot.kind is not ArtifactKind.RAW:
                raise ArtifactIntegrityConflict("normalized record 必须绑定一个 raw snapshot")
            parent = parents[0].snapshot
            if record.snapshot.source_id != parent.source_id:
                raise ArtifactIntegrityConflict("normalized record 的 source_id 与 raw 上游不一致")
            if record.snapshot.acquired_at < parent.acquired_at:
                raise ArtifactIntegrityConflict("normalized record 的 acquired_at 早于 raw 上游")
            if record.snapshot.available_at < parent.available_at:
                raise ArtifactIntegrityConflict("normalized record 的 available_at 早于 raw 上游")
            expected = normalization_identity_hash(
                parent.content_hash,
                record.snapshot.content_hash,
                record.snapshot.transform_version,
                record.snapshot.schema_version,
            )
            if (
                _required_hash(record.relations["normalization_identity"], "normalization_identity")
                != expected
            ):
                raise ArtifactIntegrityConflict(
                    "normalized record 的 normalization_identity 不一致"
                )
            return
        if not parents:
            raise ArtifactIntegrityConflict("derived record 缺少上游 snapshot")
        if record.snapshot.acquired_at < max(item.snapshot.acquired_at for item in parents):
            raise ArtifactIntegrityConflict("derived record 的 acquired_at 早于上游")
        if record.snapshot.available_at < max(item.snapshot.available_at for item in parents):
            raise ArtifactIntegrityConflict("derived record 的 available_at 早于上游")
        expected = derived_identity_hash(
            (item.snapshot.content_hash for item in parents),
            record.snapshot.transform_version,
            record.snapshot.schema_version,
        )
        if (
            _required_hash(record.relations["derivation_identity"], "derivation_identity")
            != expected
        ):
            raise ArtifactIntegrityConflict("derived record 的 derivation_identity 不一致")

    def _load_lineage_record(self, lineage_snapshot_hash: str) -> dict[str, object]:
        digest = _checked_hash(lineage_snapshot_hash, "snapshot_lineage_hash")
        payload = _load_canonical_mapping(
            self._read_immutable_bytes(self.lineage_path(digest), "snapshot lineage"),
            "snapshot lineage",
        )
        _require_exact_keys(
            payload,
            {
                "content_lineage_identity",
                "format",
                "input_snapshot_hashes",
                "output_snapshot_hash",
                "recorded_at",
                "snapshot_lineage_hash",
                "transform_version",
            },
            "snapshot lineage",
        )
        if payload["format"] != _SNAPSHOT_LINEAGE_FORMAT:
            raise ArtifactIntegrityConflict("snapshot lineage 格式不受支持")
        inputs = _require_hash_list(payload["input_snapshot_hashes"], "input_snapshot_hashes")
        output = _required_hash(payload["output_snapshot_hash"], "output_snapshot_hash")
        transform = _required_text(payload["transform_version"], "transform_version")
        if _required_hash(payload["snapshot_lineage_hash"], "snapshot_lineage_hash") != digest:
            raise ArtifactIntegrityConflict("snapshot lineage 文件名与身份不一致")
        if snapshot_lineage_hash(output, inputs, transform) != digest:
            raise ArtifactIntegrityConflict("snapshot lineage 身份不一致")
        _utc_datetime(payload["recorded_at"], "recorded_at")
        _required_hash(payload["content_lineage_identity"], "content_lineage_identity")
        return payload

    def _validate_lineage_record(
        self,
        record: _ArtifactRecord,
        parents: tuple[_ArtifactRecord, ...],
        lineage: dict[str, object],
    ) -> None:
        parent_hashes = tuple(item.snapshot.snapshot_hash for item in parents)
        if (
            _required_hash(lineage["output_snapshot_hash"], "output_snapshot_hash")
            != record.snapshot.snapshot_hash
        ):
            raise ArtifactIntegrityConflict("snapshot lineage 输出不一致")
        if (
            _require_hash_list(lineage["input_snapshot_hashes"], "input_snapshot_hashes")
            != parent_hashes
        ):
            raise ArtifactIntegrityConflict("snapshot lineage 输入不一致")
        if (
            _required_text(lineage["transform_version"], "transform_version")
            != record.snapshot.transform_version
        ):
            raise ArtifactIntegrityConflict("snapshot lineage transform version 不一致")
        expected_content_lineage = lineage_hash(
            record.snapshot.content_hash,
            (item.snapshot.content_hash for item in parents),
            record.snapshot.transform_version,
        )
        if (
            _required_hash(lineage["content_lineage_identity"], "content_lineage_identity")
            != expected_content_lineage
        ):
            raise ArtifactIntegrityConflict("snapshot lineage 的 content lineage 不一致")
        recorded_at = _utc_datetime(lineage["recorded_at"], "recorded_at")
        latest_available_at = max(
            record.snapshot.available_at,
            *(item.snapshot.available_at for item in parents),
        )
        if recorded_at < latest_available_at:
            raise ArtifactIntegrityConflict("snapshot lineage 的 recorded_at 早于制品可用时间")

    def _read_immutable_bytes(self, path: Path, label: str) -> bytes:
        """通过固定目录句柄读取正式对象，拒绝中间目录被链接替换。"""

        if os.name == "nt":
            # Windows 没有本实现可移植使用的 openat/dir_fd；依赖根目录 ACL，并在读取前后
            # 检查 reparse point/普通文件身份。部署层必须只给服务用户写入该根。
            return _read_regular_bytes(path, label)
        directory_fd = self._open_posix_directory_fd(path.parent)
        try:
            return _read_regular_bytes_at(directory_fd, path.name, label)
        finally:
            os.close(directory_fd)

    def _read_immutable_bytes_if_exists(self, path: Path, label: str) -> bytes | None:
        try:
            return self._read_immutable_bytes(path, label)
        except ArtifactNotFoundError:
            return None

    def _write_immutable(self, path: Path, payload: bytes, label: str) -> None:
        """以 create-or-verify 语义发布正式对象，永久路径永不替换或删除。"""

        if not isinstance(payload, bytes):
            raise ArtifactStoreError(f"{label} 必须是 bytes")
        self._ensure_directory(path.parent)
        existing = self._read_immutable_bytes_if_exists(path, label)
        if existing is not None:
            if existing != payload:
                raise ArtifactIntegrityConflict(f"{label} 已存在但内容不一致，已拒绝覆盖：{path}")
            return
        if os.name == "nt":
            self._write_immutable_windows(path, payload, label)
            return
        self._write_immutable_posix(path, payload, label)

    def _write_immutable_posix(self, path: Path, payload: bytes, label: str) -> None:
        """在固定 parent dir_fd 内 stage、fsync、hard-link 发布并回读验证。"""

        directory_fd = self._open_posix_directory_fd(path.parent)
        temporary_name: str | None = None
        try:
            descriptor, temporary_name = self._create_posix_staging_file(directory_fd)
            with os.fdopen(descriptor, "wb") as file_obj:
                file_obj.write(payload)
                file_obj.flush()
                os.fsync(file_obj.fileno())
            try:
                os.link(
                    temporary_name,
                    path.name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                self._assert_existing_bytes_at(
                    directory_fd,
                    path.name,
                    payload,
                    label,
                    path,
                )
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    self._assert_existing_bytes_at(
                        directory_fd,
                        path.name,
                        payload,
                        label,
                        path,
                    )
                else:
                    raise ArtifactStoreError(
                        f"文件系统不支持安全的 no-replace 制品发布：{path}"
                    ) from exc
            self._assert_existing_bytes_at(directory_fd, path.name, payload, label, path)
            self._fsync_open_directory(directory_fd, path.parent)
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
            os.close(directory_fd)

    def _write_immutable_windows(self, path: Path, payload: bytes, label: str) -> None:
        """Windows 的同目录 hard-link 发布；无法建立链接时失败关闭。"""

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".artifact-", suffix=".tmp", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as file_obj:
                file_obj.write(payload)
                file_obj.flush()
                os.fsync(file_obj.fileno())
            try:
                os.link(temporary_path, path, follow_symlinks=False)
            except FileExistsError:
                existing = _read_regular_bytes(path, label)
                if existing != payload:
                    raise ArtifactIntegrityConflict(
                        f"{label} 并发发布后内容不一致，已拒绝覆盖：{path}"
                    )
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    existing = _read_regular_bytes(path, label)
                    if existing != payload:
                        raise ArtifactIntegrityConflict(
                            f"{label} 并发发布后内容不一致，已拒绝覆盖：{path}"
                        ) from exc
                else:
                    raise ArtifactStoreError(
                        f"文件系统不支持安全的 no-replace 制品发布：{path}"
                    ) from exc
            existing = _read_regular_bytes(path, label)
            if existing != payload:
                raise ArtifactIntegrityConflict(f"{label} 发布后内容不一致，已拒绝继续：{path}")
        finally:
            # 只清理本进程刚创建的 staging 文件；从不删除永久对象或旧版本。
            temporary_path.unlink(missing_ok=True)

    def _create_posix_staging_file(self, directory_fd: int) -> tuple[int, str]:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        for _ in range(3):
            temporary_name = f".artifact-{secrets.token_hex(24)}.tmp"
            try:
                descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
            except FileExistsError:
                continue
            return descriptor, temporary_name
        raise ArtifactStoreError("无法安全创建制品 staging 文件")

    def _assert_existing_bytes_at(
        self,
        directory_fd: int,
        filename: str,
        payload: bytes,
        label: str,
        path: Path,
    ) -> None:
        existing = _read_regular_bytes_at(directory_fd, filename, label)
        if existing != payload:
            raise ArtifactIntegrityConflict(f"{label} 并发发布后内容不一致，已拒绝覆盖：{path}")

    def _ensure_directory(self, path: Path) -> None:
        try:
            relative = path.relative_to(self._root)
        except ValueError as exc:
            raise ArtifactStoreError("制品库内部目录越出根目录，已拒绝") from exc
        if os.name == "nt":
            current = self._root
            _assert_directory(current, "不可变制品库根目录")
            for part in relative.parts:
                current = current / part
                if _lstat(current) is None:
                    try:
                        current.mkdir()
                    except FileExistsError:
                        pass
                _assert_directory(current, "制品库目录")
            return

        directory_fd = self._open_posix_directory_fd(self._root)
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
            for part in relative.parts:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=directory_fd)
                except FileExistsError:
                    pass
                try:
                    next_fd = os.open(part, flags, dir_fd=directory_fd)
                except OSError as exc:
                    raise ArtifactStoreError(f"制品库目录不安全：{path}") from exc
                try:
                    next_state = os.fstat(next_fd)
                    if not stat.S_ISDIR(next_state.st_mode):
                        raise ArtifactStoreError(f"制品库目录不是普通目录：{path}")
                    _assert_posix_private_directory(next_state, "制品库目录")
                except BaseException:
                    os.close(next_fd)
                    raise
                os.close(directory_fd)
                directory_fd = next_fd
        finally:
            os.close(directory_fd)

    def _open_posix_directory_fd(self, path: Path) -> int:
        """从制品库 root 开始逐段打开非链接目录，返回调用方负责关闭的 fd。"""

        try:
            relative = path.relative_to(self._root)
        except ValueError as exc:
            raise ArtifactStoreError("制品库路径越出根目录，已拒绝") from exc
        root_state = _assert_directory(self._root, "不可变制品库根目录")
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        try:
            directory_fd = os.open(self._root, flags)
        except OSError as exc:
            raise ArtifactStoreError(f"无法安全打开制品库根目录：{self._root}") from exc
        try:
            opened_root = os.fstat(directory_fd)
            if not stat.S_ISDIR(opened_root.st_mode) or (
                opened_root.st_dev,
                opened_root.st_ino,
            ) != (root_state.st_dev, root_state.st_ino):
                raise ArtifactStoreError("不可变制品库根目录在打开时发生变化，已拒绝")
            _assert_posix_private_directory(opened_root, "不可变制品库根目录")
            for part in relative.parts:
                try:
                    next_fd = os.open(part, flags, dir_fd=directory_fd)
                except FileNotFoundError as exc:
                    raise ArtifactNotFoundError(f"制品库目录不存在：{path}") from exc
                except OSError as exc:
                    raise ArtifactStoreError(f"无法安全打开制品库目录：{path}") from exc
                next_state = os.fstat(next_fd)
                if not stat.S_ISDIR(next_state.st_mode):
                    os.close(next_fd)
                    raise ArtifactStoreError(f"制品库目录不是普通目录：{path}")
                _assert_posix_private_directory(next_state, "制品库目录")
                os.close(directory_fd)
                directory_fd = next_fd
            return directory_fd
        except BaseException:
            os.close(directory_fd)
            raise

    def _fsync_open_directory(self, directory_fd: int, directory: Path) -> None:
        try:
            os.fsync(directory_fd)
        except OSError as exc:
            raise ArtifactStoreError(f"无法同步制品库目录：{directory}") from exc


def _snapshot_payload(snapshot: ArtifactSnapshot) -> dict[str, object]:
    return {
        "acquired_at": snapshot.acquired_at.isoformat(),
        "artifact_id": snapshot.artifact_id,
        "available_at": snapshot.available_at.isoformat(),
        "content_hash": snapshot.content_hash,
        "kind": snapshot.kind.value,
        "provenance": {
            "attributes": [list(item) for item in snapshot.provenance.attributes],
            "collection_method": snapshot.provenance.collection_method,
            "source_id": snapshot.provenance.source_id,
            "source_reference": snapshot.provenance.source_reference,
        },
        "quality_status": snapshot.quality_status.value,
        "schema_version": snapshot.schema_version,
        "snapshot_hash": snapshot.snapshot_hash,
        "source_id": snapshot.source_id,
        "transform_version": snapshot.transform_version,
    }


def _snapshot_from_payload(payload: dict[str, object]) -> ArtifactSnapshot:
    _require_exact_keys(
        payload,
        {
            "acquired_at",
            "artifact_id",
            "available_at",
            "content_hash",
            "kind",
            "provenance",
            "quality_status",
            "schema_version",
            "snapshot_hash",
            "source_id",
            "transform_version",
        },
        "snapshot",
    )
    provenance_payload = _require_mapping(payload["provenance"], "snapshot.provenance")
    _require_exact_keys(
        provenance_payload,
        {"attributes", "collection_method", "source_id", "source_reference"},
        "snapshot.provenance",
    )
    attributes: list[tuple[str, str]] = []
    for item in _require_list(provenance_payload["attributes"], "snapshot.provenance.attributes"):
        pair = _require_list(item, "snapshot.provenance.attributes[]")
        if len(pair) != 2:
            raise ArtifactIntegrityConflict("snapshot.provenance.attributes 项必须有两个文本值")
        attributes.append(
            (
                _required_text(pair[0], "snapshot.provenance.attributes.key"),
                _required_text(pair[1], "snapshot.provenance.attributes.value"),
            )
        )
    try:
        provenance = ArtifactProvenance(
            source_id=_required_text(provenance_payload["source_id"], "provenance.source_id"),
            source_reference=_required_text(
                provenance_payload["source_reference"],
                "provenance.source_reference",
            ),
            collection_method=_required_text(
                provenance_payload["collection_method"],
                "provenance.collection_method",
            ),
            attributes=tuple(attributes),
        )
        return ArtifactSnapshot(
            artifact_id=_required_text(payload["artifact_id"], "artifact_id"),
            kind=ArtifactKind(_required_text(payload["kind"], "kind")),
            source_id=_required_text(payload["source_id"], "source_id"),
            content_hash=_required_hash(payload["content_hash"], "content_hash"),
            acquired_at=_utc_datetime(payload["acquired_at"], "acquired_at"),
            available_at=_utc_datetime(payload["available_at"], "available_at"),
            schema_version=_required_text(payload["schema_version"], "schema_version"),
            transform_version=_required_text(payload["transform_version"], "transform_version"),
            quality_status=QualityStatus(
                _required_text(payload["quality_status"], "quality_status")
            ),
            provenance=provenance,
            snapshot_hash=_required_hash(payload["snapshot_hash"], "snapshot_hash"),
        )
    except (DataDomainError, ValueError) as exc:
        raise ArtifactIntegrityConflict("snapshot 无法重建领域对象") from exc


def _source_payload(source: DataSource) -> dict[str, object]:
    license_metadata = source.license
    return {
        "adapter_id": source.adapter_id,
        "config_sha256": source.config_sha256,
        "license": {
            "allows_derived_data_storage": license_metadata.allows_derived_data_storage,
            "allows_internal_storage": license_metadata.allows_internal_storage,
            "allows_live_trading": license_metadata.allows_live_trading,
            "contract_reference": license_metadata.contract_reference,
            "effective_from": license_metadata.effective_from,
            "expires_on": license_metadata.expires_on,
            "permitted_purposes": list(license_metadata.permitted_purposes),
            "status": license_metadata.status,
            "terms_sha256": license_metadata.terms_sha256,
        },
        "name": source.name,
        "official_references": list(source.official_references),
        "source_id": source.source_id,
        "status": source.status,
        "tier": source.tier,
    }


def _source_from_payload(payload: dict[str, object]) -> DataSource:
    _require_exact_keys(
        payload,
        {
            "adapter_id",
            "config_sha256",
            "license",
            "name",
            "official_references",
            "source_id",
            "status",
            "tier",
        },
        "source",
    )
    license_payload = _require_mapping(payload["license"], "source.license")
    _require_exact_keys(
        license_payload,
        {
            "allows_derived_data_storage",
            "allows_internal_storage",
            "allows_live_trading",
            "contract_reference",
            "effective_from",
            "expires_on",
            "permitted_purposes",
            "status",
            "terms_sha256",
        },
        "source.license",
    )
    try:
        license_metadata = LicenseMetadata(
            status=_required_text(license_payload["status"], "license.status"),
            contract_reference=_optional_text(
                license_payload["contract_reference"],
                "license.contract_reference",
            ),
            effective_from=_optional_text(
                license_payload["effective_from"],
                "license.effective_from",
            ),
            expires_on=_optional_text(license_payload["expires_on"], "license.expires_on"),
            terms_sha256=_optional_hash(license_payload["terms_sha256"], "license.terms_sha256"),
            permitted_purposes=tuple(
                _required_text(item, "license.permitted_purposes")
                for item in _require_list(
                    license_payload["permitted_purposes"],
                    "license.permitted_purposes",
                )
            ),
            allows_internal_storage=_required_bool(
                license_payload["allows_internal_storage"],
                "license.allows_internal_storage",
            ),
            allows_derived_data_storage=_required_bool(
                license_payload["allows_derived_data_storage"],
                "license.allows_derived_data_storage",
            ),
            allows_live_trading=_required_bool(
                license_payload["allows_live_trading"],
                "license.allows_live_trading",
            ),
        )
        return DataSource(
            source_id=_required_text(payload["source_id"], "source_id"),
            adapter_id=_required_text(payload["adapter_id"], "adapter_id"),
            name=_required_text(payload["name"], "name"),
            tier=_required_text(payload["tier"], "tier"),
            status=_required_text(payload["status"], "status"),
            config_sha256=_required_hash(payload["config_sha256"], "config_sha256"),
            official_references=tuple(
                _required_text(item, "official_references")
                for item in _require_list(payload["official_references"], "official_references")
            ),
            license=license_metadata,
        )
    except (DataDomainError, ValueError) as exc:
        raise ArtifactIntegrityConflict("source 快照无法重建领域对象") from exc


def _validate_relation_shape(kind: ArtifactKind, relations: dict[str, object]) -> None:
    if kind is ArtifactKind.RAW:
        _require_exact_keys(
            relations,
            {"lineage_snapshot_hash", "publication_authorization_hash", "raw_format"},
            "raw relations",
        )
        if relations["lineage_snapshot_hash"] is not None:
            raise ArtifactIntegrityConflict("raw relations 不得包含 lineage")
        _optional_hash(
            relations["publication_authorization_hash"], "publication_authorization_hash"
        )
        _required_text(relations["raw_format"], "raw_format")
        return
    if kind is ArtifactKind.NORMALIZED:
        _require_exact_keys(
            relations,
            {
                "lineage_snapshot_hash",
                "normalization_identity",
                "publication_authorization_hash",
                "quality_assessment_hash",
                "raw_snapshot_hash",
            },
            "normalized relations",
        )
        _required_hash(relations["raw_snapshot_hash"], "raw_snapshot_hash")
        _required_hash(relations["normalization_identity"], "normalization_identity")
        _required_hash(relations["lineage_snapshot_hash"], "lineage_snapshot_hash")
        _optional_hash(
            relations["publication_authorization_hash"], "publication_authorization_hash"
        )
        _optional_hash(relations["quality_assessment_hash"], "quality_assessment_hash")
        return
    _require_exact_keys(
        relations,
        {"derivation_identity", "input_snapshot_hashes", "lineage_snapshot_hash"},
        "derived relations",
    )
    _required_hash(relations["derivation_identity"], "derivation_identity")
    _require_hash_list(relations["input_snapshot_hashes"], "input_snapshot_hashes")
    _required_hash(relations["lineage_snapshot_hash"], "lineage_snapshot_hash")


def _relation_parent_hashes(kind: ArtifactKind, relations: dict[str, object]) -> tuple[str, ...]:
    if kind is ArtifactKind.RAW:
        return ()
    if kind is ArtifactKind.NORMALIZED:
        return (_required_hash(relations["raw_snapshot_hash"], "raw_snapshot_hash"),)
    return _require_hash_list(relations["input_snapshot_hashes"], "input_snapshot_hashes")


def _relation_lineage_hash(relations: dict[str, object]) -> str | None:
    return _optional_hash(relations["lineage_snapshot_hash"], "lineage_snapshot_hash")


def _relation_publication_authorization_hash(
    kind: ArtifactKind,
    relations: dict[str, object],
) -> str | None:
    if kind is ArtifactKind.DERIVED:
        return None
    return _optional_hash(
        relations["publication_authorization_hash"], "publication_authorization_hash"
    )


def _canonical_json_bytes(payload: object) -> bytes:
    try:
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError) as exc:
        raise ArtifactStoreError("不可变制品记录必须是有限、可 JSON 序列化的值") from exc


def _load_canonical_mapping(raw_bytes: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(
            raw_bytes.decode("utf-8"),
            object_pairs_hook=_json_object_without_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ArtifactIntegrityConflict(f"{label} 不是安全的 canonical JSON") from exc
    mapping = _require_mapping(value, label)
    if _canonical_json_bytes(mapping) != raw_bytes:
        raise ArtifactIntegrityConflict(f"{label} 不是唯一规范 JSON，已拒绝读取")
    return mapping


def _json_object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 重复字段：{key}")
        result[key] = value
    return result


def _read_regular_bytes(path: Path, label: str) -> bytes:
    before = _assert_regular_file(path, label)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise ArtifactNotFoundError(f"{label} 不存在：{path}") from exc
    except OSError as exc:
        raise ArtifactStoreError(f"无法安全读取 {label}：{path}") from exc
    try:
        after_open = os.fstat(descriptor)
        if not stat.S_ISREG(after_open.st_mode):
            raise ArtifactStoreError(f"{label} 不是普通文件：{path}")
        with os.fdopen(descriptor, "rb", closefd=False) as file_obj:
            data = file_obj.read()
        after = _assert_regular_file(path, label)
        if (before.st_dev, before.st_ino) != (after_open.st_dev, after_open.st_ino) or (
            after_open.st_dev,
            after_open.st_ino,
        ) != (after.st_dev, after.st_ino):
            raise ArtifactStoreError(f"读取 {label} 时文件身份发生变化，已拒绝")
        return data
    finally:
        os.close(descriptor)


def _read_regular_bytes_at(directory_fd: int, filename: str, label: str) -> bytes:
    """在已固定的 POSIX 目录句柄内读取一个无链接普通文件。"""

    if Path(filename).name != filename:
        raise ArtifactStoreError(f"{label} 文件名不安全，已拒绝读取")
    try:
        before = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ArtifactNotFoundError(f"{label} 不存在：{filename}") from exc
    except OSError as exc:
        raise ArtifactStoreError(f"无法安全检查 {label}：{filename}") from exc
    if _is_reparse_or_symlink(before) or not stat.S_ISREG(before.st_mode):
        raise ArtifactStoreError(f"{label} 必须是非链接的普通文件：{filename}")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(filename, flags, dir_fd=directory_fd)
    except FileNotFoundError as exc:
        raise ArtifactNotFoundError(f"{label} 不存在：{filename}") from exc
    except OSError as exc:
        raise ArtifactStoreError(f"无法安全读取 {label}：{filename}") from exc
    try:
        after_open = os.fstat(descriptor)
        if _is_reparse_or_symlink(after_open) or not stat.S_ISREG(after_open.st_mode):
            raise ArtifactStoreError(f"{label} 不是普通文件：{filename}")
        with os.fdopen(descriptor, "rb", closefd=False) as file_obj:
            data = file_obj.read()
        after = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        if _is_reparse_or_symlink(after) or not stat.S_ISREG(after.st_mode):
            raise ArtifactStoreError(f"{label} 读取后不再是普通文件：{filename}")
        if (before.st_dev, before.st_ino) != (after_open.st_dev, after_open.st_ino) or (
            after_open.st_dev,
            after_open.st_ino,
        ) != (after.st_dev, after.st_ino):
            raise ArtifactStoreError(f"读取 {label} 时文件身份发生变化，已拒绝")
        return data
    finally:
        os.close(descriptor)


def _assert_safe_existing_ancestors(path: Path) -> None:
    candidates = (path, *path.parents)
    for candidate in reversed(candidates):
        existing = _lstat(candidate)
        if existing is None:
            continue
        if _is_reparse_or_symlink(existing):
            raise ArtifactStoreError(f"不可变制品库祖先路径不能是符号链接或重解析点：{candidate}")
        if not stat.S_ISDIR(existing.st_mode):
            raise ArtifactStoreError(f"不可变制品库祖先路径不是目录：{candidate}")


def _assert_directory(path: Path, label: str) -> os.stat_result:
    state = _lstat(path)
    if state is None:
        raise ArtifactNotFoundError(f"{label} 不存在：{path}")
    if _is_reparse_or_symlink(state) or not stat.S_ISDIR(state.st_mode):
        raise ArtifactStoreError(f"{label} 必须是非链接的普通目录：{path}")
    return state


def _assert_posix_private_directory(state: os.stat_result, label: str) -> None:
    """POSIX 根及内部目录不得允许组或其他用户篡改。"""

    if os.name == "nt":
        return
    if state.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ArtifactStoreError(f"{label} 对组或其他用户可写，已拒绝使用")


def _assert_regular_file(path: Path, label: str) -> os.stat_result:
    state = _lstat(path)
    if state is None:
        raise ArtifactNotFoundError(f"{label} 不存在：{path}")
    if _is_reparse_or_symlink(state) or not stat.S_ISREG(state.st_mode):
        raise ArtifactStoreError(f"{label} 必须是非链接的普通文件：{path}")
    return state


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ArtifactStoreError(f"无法检查制品库路径：{path}") from exc


def _is_reparse_or_symlink(state: os.stat_result) -> bool:
    return stat.S_ISLNK(state.st_mode) or bool(
        getattr(state, "st_file_attributes", 0) & _REPARSE_POINT_FLAG
    )


def _checked_hash(value: str, field_name: str) -> str:
    try:
        return require_sha256(value, field_name=field_name)
    except FingerprintError as exc:
        raise ArtifactStoreError(str(exc)) from exc


def _required_hash(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ArtifactIntegrityConflict(f"{field_name} 必须是 SHA-256 文本")
    try:
        return require_sha256(value, field_name=field_name)
    except FingerprintError as exc:
        raise ArtifactIntegrityConflict(str(exc)) from exc


def _optional_hash(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_hash(value, field_name)


def _require_hash_list(value: object, field_name: str) -> tuple[str, ...]:
    values = tuple(_required_hash(item, field_name) for item in _require_list(value, field_name))
    if not values:
        raise ArtifactIntegrityConflict(f"{field_name} 不能为空")
    if len(values) != len(set(values)):
        raise ArtifactIntegrityConflict(f"{field_name} 不能包含重复哈希")
    return values


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactIntegrityConflict(f"{field_name} 必须是非空文本")
    return value.strip()


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _required_bool(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise ArtifactIntegrityConflict(f"{field_name} 必须是 bool")
    return value


def _required_nonnegative_int(value: object, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise ArtifactIntegrityConflict(f"{field_name} 必须是非负整数")
    return value


def _require_mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ArtifactIntegrityConflict(f"{field_name} 必须是对象")
    return value


def _require_list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise ArtifactIntegrityConflict(f"{field_name} 必须是数组")
    return value


def _require_exact_keys(
    payload: dict[str, object],
    expected: set[str],
    field_name: str,
) -> None:
    if set(payload) != expected:
        raise ArtifactIntegrityConflict(f"{field_name} 字段集合不受支持")


def _utc_datetime(value: object, field_name: str) -> datetime:
    text = _required_text(value, field_name)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ArtifactIntegrityConflict(f"{field_name} 必须是 ISO 带时区时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ArtifactIntegrityConflict(f"{field_name} 必须是带时区时间")
    return parsed
