"""P1-WP06 受控的数据源发布器。

本模块是新协议中唯一把 adapter 输出写入不可变制品库的组合入口。它刻意不调用 legacy
``downloader.py``、不读取网络或凭据，也不会改变 profile、日历、合约或任何交易资格。
真实 adapter 可自行在 ``fetch`` 内使用安全注入的凭据；发布器只接收已脱敏的
``RawCapture`` 和 canonical normalized table。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import re
from typing import Protocol, cast

from northstar_quant.data_platform.artifacts.immutable_store import (
    ArtifactStore,
    ArtifactStoreError,
    StoredArtifact,
    StoredDatasetVersion,
    StoredQualityAssessment,
)
from northstar_quant.data_platform.contracts.data_domain import (
    Artifact,
    ArtifactMetadata,
    ArtifactProvenance,
    ArtifactSnapshot,
    DataLineage,
    DataQualityResult,
    DatasetVersion,
    NormalizedArtifact,
    QualityStatus,
    RawArtifact,
)
from northstar_quant.data_platform.artifacts.fingerprints import content_sha256, lineage_hash
from northstar_quant.data_platform.quality import (
    DataQualityEngine,
    DataQualityError,
    PublishedQualityAssessment,
    QualityEvaluation,
    QualityMode,
    QualityRequest,
)
from northstar_quant.data_platform.sources.protocol import (
    AdapterMetadata,
    DataSourceAdapter,
    DataSourceProtocolError,
    NormalizedTable,
    PublicationAuthorization,
    RawCapture,
    SourceFetchRequest,
    build_publication_authorization,
)
from northstar_quant.platform.config.data_sources import DataSourceConfig, get_data_source


class DataSourcePublisherError(RuntimeError):
    """授权、适配器、确定性、质量或发布步骤不满足时失败关闭。"""


class QualityRequestBuilder(Protocol):
    """由调用方提供的质量策略工厂；发布器始终自行执行引擎。"""

    def build(
        self,
        *,
        candidate: NormalizedArtifact,
        normalized: NormalizedTable,
        checked_at: datetime,
        decision_at: datetime,
    ) -> QualityRequest:
        """基于发布器生成的候选制品构造一份严格绑定的质量请求。"""


_ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class SourcePublicationSpec:
    """一次适配器发布所需的全部显式、无密钥输入。"""

    request: SourceFetchRequest
    acquired_at: datetime
    normalized_available_at: datetime
    checked_at: datetime
    decision_at: datetime
    raw_artifact_id: str
    normalized_artifact_id: str
    quality_request_builder: QualityRequestBuilder
    dataset_transform_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.request, SourceFetchRequest):
            raise DataSourcePublisherError("request 必须是 SourceFetchRequest")
        acquired_at = _utc_datetime(self.acquired_at, "acquired_at")
        normalized_available_at = _utc_datetime(
            self.normalized_available_at,
            "normalized_available_at",
        )
        checked_at = _utc_datetime(self.checked_at, "checked_at")
        decision_at = _utc_datetime(self.decision_at, "decision_at")
        if normalized_available_at < acquired_at:
            raise DataSourcePublisherError("normalized_available_at 不能早于 acquired_at")
        if self.request.requested_at > acquired_at:
            raise DataSourcePublisherError("request.requested_at 不能晚于 acquired_at")
        if checked_at < acquired_at or checked_at > normalized_available_at:
            raise DataSourcePublisherError("checked_at 必须位于候选 normalized 制品可用窗口内")
        if decision_at < acquired_at or decision_at > normalized_available_at:
            raise DataSourcePublisherError("decision_at 必须位于候选 normalized 制品可用窗口内")
        if decision_at < checked_at:
            raise DataSourcePublisherError("decision_at 不能早于质量 checked_at")
        object.__setattr__(self, "acquired_at", acquired_at)
        object.__setattr__(self, "normalized_available_at", normalized_available_at)
        object.__setattr__(self, "checked_at", checked_at)
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(
            self,
            "raw_artifact_id",
            _artifact_id(self.raw_artifact_id, "raw_artifact_id"),
        )
        object.__setattr__(
            self,
            "normalized_artifact_id",
            _artifact_id(self.normalized_artifact_id, "normalized_artifact_id"),
        )
        if not callable(getattr(self.quality_request_builder, "build", None)):
            raise DataSourcePublisherError("quality_request_builder 必须提供 build()")
        object.__setattr__(
            self,
            "dataset_transform_version",
            _artifact_id(self.dataset_transform_version, "dataset_transform_version"),
        )


@dataclass(frozen=True, slots=True)
class AssessedSourcePublication:
    """已经保存 raw、normalized 与 assessment，但尚未被释放为研究数据集的结果。"""

    authorization: PublicationAuthorization
    raw_artifact: RawArtifact
    normalized_artifact: NormalizedArtifact
    dataset_transform_version: str
    raw: StoredArtifact
    normalized: StoredArtifact
    quality_evaluation: QualityEvaluation
    quality_assessment: StoredQualityAssessment
    quality_results: tuple[DataQualityResult, ...]


@dataclass(frozen=True, slots=True)
class PublishedSourceDataset:
    """通过显式研究质量门禁并写入不可变 DatasetVersion 的发布结果。"""

    assessed: AssessedSourcePublication
    dataset: StoredDatasetVersion


class DataSourceAdapterRegistry:
    """进程内显式注册表；重复技术 adapter ID 默认拒绝。"""

    def __init__(self) -> None:
        self._adapters: dict[str, DataSourceAdapter] = {}

    def register(self, adapter: DataSourceAdapter) -> None:
        adapter_id = _adapter_id(adapter)
        if adapter_id in self._adapters:
            raise DataSourcePublisherError(f"数据源适配器已注册：{adapter_id}")
        self._adapters[adapter_id] = adapter

    def get(self, adapter_id: str) -> DataSourceAdapter:
        normalized = _artifact_id(adapter_id, "adapter_id")
        try:
            return self._adapters[normalized]
        except KeyError as exc:
            raise DataSourcePublisherError(f"未注册数据源适配器：{normalized}") from exc

    def list_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))


class DataSourcePublisher:
    """把受授权 adapter 输出发布为追加式、可审计的研究制品。

    每个关键写入前都会重新读取数据源配置并重建授权。授权撤销、范围变化、adapter metadata
    漂移或任何质量异常均不产生 DatasetVersion；已持久化 raw / 失败 normalized 只保留作审计
    证据，绝不被覆盖或自动清理。
    """

    def __init__(
        self,
        *,
        store: ArtifactStore,
        source_config_loader: Callable[[str], DataSourceConfig] = get_data_source,
        quality_engine: DataQualityEngine | None = None,
    ) -> None:
        if not isinstance(store, ArtifactStore):
            raise DataSourcePublisherError("store 必须是 ArtifactStore")
        if not callable(source_config_loader):
            raise DataSourcePublisherError("source_config_loader 必须可调用")
        if quality_engine is not None and not isinstance(quality_engine, DataQualityEngine):
            raise DataSourcePublisherError("quality_engine 必须是 DataQualityEngine 或 None")
        self._store = store
        self._source_config_loader = source_config_loader
        self._quality_engine = quality_engine or DataQualityEngine()

    def capture_and_assess(
        self,
        *,
        adapter: DataSourceAdapter,
        spec: SourcePublicationSpec,
    ) -> AssessedSourcePublication:
        """执行 raw → deterministic normalize → quality assessment，尚不释放数据集。"""

        _validate_adapter(adapter)
        if not isinstance(spec, SourcePublicationSpec):
            raise DataSourcePublisherError("spec 必须是 SourcePublicationSpec")
        adapter_id = _adapter_id(adapter)
        metadata = self._adapter_metadata(adapter, spec)
        authorization = self._authorize(spec, metadata)
        if authorization.source.adapter_id != adapter_id:
            raise DataSourcePublisherError("adapter.adapter_id 与冻结来源授权不一致")

        capture = self._fetch(adapter, spec)
        if capture.raw_format != metadata.raw_format:
            raise DataSourcePublisherError("RawCapture.raw_format 与 AdapterMetadata.raw_format 不一致")
        if capture.available_at < spec.acquired_at:
            raise DataSourcePublisherError("RawCapture.available_at 不能早于 acquired_at")
        if spec.checked_at < capture.available_at or spec.decision_at < capture.available_at:
            raise DataSourcePublisherError(
                "质量 checked_at 和 decision_at 不能早于 raw 制品可用时间"
            )

        # 重新读取 metadata 与来源配置；未经授权的 source 连 fetch 都不能执行，fetch 后变更
        # 也不能继续写入任何 raw / normalized record。
        refreshed_metadata = self._adapter_metadata(adapter, spec)
        if refreshed_metadata != metadata:
            raise DataSourcePublisherError("adapter metadata 在 fetch 前后发生变化，已拒绝发布")
        self._revalidate_authorization(authorization, refreshed_metadata)

        raw = self._raw_artifact(authorization, metadata, capture, spec)
        try:
            raw_stored = self._store.put_raw(
                source=authorization.source,
                artifact=raw,
                payload=capture.payload,
                authorization=authorization,
            )
        except ArtifactStoreError as exc:
            raise DataSourcePublisherError("raw 制品发布失败") from exc

        if capture.capture_quality_status is not QualityStatus.PASS:
            raise DataSourcePublisherError(
                "RawCapture 采集质量不是 PASS；raw 审计证据已保留，拒绝继续标准化"
            )

        self._revalidate_authorization(authorization, refreshed_metadata)
        raw_payload = self._store.read_payload(raw_stored.snapshot.snapshot_hash)
        candidate, normalized = self._candidate_normalized(
            adapter=adapter,
            authorization=authorization,
            metadata=metadata,
            raw=raw,
            raw_payload=raw_payload,
            spec=spec,
        )
        evaluation = self._evaluate(candidate=candidate, normalized=normalized, spec=spec)
        final = replace(
            candidate,
            metadata=replace(candidate.metadata, quality_status=evaluation.aggregate_status),
        )
        try:
            quality_results = evaluation.bind_published_artifact(_as_artifact(final))
            assessment = PublishedQualityAssessment.from_evaluation(
                evaluation=evaluation,
                published_artifact=_as_artifact(final),
            )
        except DataQualityError as exc:
            raise DataSourcePublisherError("质量结论无法绑定最终 normalized 制品") from exc

        self._revalidate_authorization(authorization, refreshed_metadata)
        lineage = DataLineage(
            output_artifact=_as_artifact(final),
            input_artifacts=(_as_artifact(raw),),
            transform_version=final.transform_version,
            lineage_identity=lineage_hash(
                final.content_hash,
                (raw.content_hash,),
                final.transform_version,
            ),
            recorded_at=final.available_at,
        )
        try:
            normalized_stored = self._store.put_normalized(
                source=authorization.source,
                artifact=final,
                payload=normalized.payload,
                lineage=lineage,
                assessment=assessment,
                authorization=authorization,
            )
            stored_assessment = self._store.load_quality_assessment(
                normalized_stored.snapshot.snapshot_hash
            )
        except ArtifactStoreError as exc:
            raise DataSourcePublisherError("normalized 制品或质量证据发布失败") from exc
        return AssessedSourcePublication(
            authorization=authorization,
            raw_artifact=raw,
            normalized_artifact=final,
            dataset_transform_version=spec.dataset_transform_version,
            raw=raw_stored,
            normalized=normalized_stored,
            quality_evaluation=evaluation,
            quality_assessment=stored_assessment,
            quality_results=quality_results,
        )

    def publish_dataset(
        self,
        assessed: AssessedSourcePublication,
        *,
        released_at: datetime,
        allow_warn: bool = False,
        allow_unknown_for_noncritical: bool = False,
    ) -> PublishedSourceDataset:
        """通过显式门禁后，把已评估的 normalized 制品释放为 DatasetVersion。"""

        if not isinstance(assessed, AssessedSourcePublication):
            raise DataSourcePublisherError("assessed 必须是 AssessedSourcePublication")
        released_at = _utc_datetime(released_at, "released_at")
        if released_at < assessed.normalized.snapshot.available_at:
            raise DataSourcePublisherError("released_at 不能早于 normalized 制品 available_at")
        if type(allow_warn) is not bool or type(allow_unknown_for_noncritical) is not bool:
            raise DataSourcePublisherError("数据集质量策略必须显式使用 bool")

        # DatasetVersion 是新的可消费发布动作，必须以 release 时点重新验证当前 source 配置。
        self._revalidate_authorization(
            assessed.authorization,
            assessed.authorization.adapter_metadata,
            authorized_at=released_at,
        )
        try:
            assessed.quality_evaluation.require_eligible(
                mode=QualityMode.RESEARCH,
                allow_warn=allow_warn,
                allow_unknown_for_noncritical=allow_unknown_for_noncritical,
            )
        except DataQualityError as exc:
            raise DataSourcePublisherError("质量门禁拒绝创建 DatasetVersion") from exc
        if assessed.normalized.quality_assessment_hash is None:
            raise DataSourcePublisherError("normalized 制品缺少不可变 quality assessment")
        if (
            ArtifactSnapshot.from_artifact(_as_artifact(assessed.normalized_artifact))
            != assessed.normalized.snapshot
        ):
            raise DataSourcePublisherError("内存 normalized 制品与不可变 snapshot 不一致")
        try:
            dataset_version = DatasetVersion.from_artifacts(
                dataset_id=assessed.authorization.scope.dataset_id,
                artifacts=(_as_artifact(assessed.normalized_artifact),),
                schema_version=assessed.normalized_artifact.schema_version,
                transform_version=assessed.dataset_transform_version,
            )
            stored_dataset = self._store.put_dataset_version(
                dataset_version,
                require_quality_assessments=True,
            )
        except (ArtifactStoreError, ValueError) as exc:
            raise DataSourcePublisherError("DatasetVersion 发布失败") from exc
        return PublishedSourceDataset(assessed=assessed, dataset=stored_dataset)

    def publish(
        self,
        *,
        adapter: DataSourceAdapter,
        spec: SourcePublicationSpec,
        released_at: datetime,
        allow_warn: bool = False,
        allow_unknown_for_noncritical: bool = False,
    ) -> PublishedSourceDataset:
        """便捷的端到端入口；质量拒绝时仍会保留此前已发布的审计制品。"""

        assessed = self.capture_and_assess(adapter=adapter, spec=spec)
        return self.publish_dataset(
            assessed,
            released_at=released_at,
            allow_warn=allow_warn,
            allow_unknown_for_noncritical=allow_unknown_for_noncritical,
        )

    def _authorize(
        self,
        spec: SourcePublicationSpec,
        metadata: AdapterMetadata,
        *,
        authorized_at: datetime | None = None,
    ) -> PublicationAuthorization:
        try:
            source_config = self._source_config_loader(spec.request.source_id)
            return build_publication_authorization(
                source_config,
                spec.request.scope,
                metadata,
                authorized_at=authorized_at or spec.request.requested_at,
            )
        except (DataSourceProtocolError, ValueError) as exc:
            raise DataSourcePublisherError("数据源发布授权预检失败") from exc

    def _revalidate_authorization(
        self,
        authorization: PublicationAuthorization,
        metadata: AdapterMetadata,
        *,
        authorized_at: datetime | None = None,
    ) -> None:
        effective_at = authorized_at or authorization.authorized_at
        try:
            source_config = self._source_config_loader(authorization.source.source_id)
            expected = build_publication_authorization(
                source_config,
                authorization.scope,
                metadata,
                authorized_at=effective_at,
            )
        except (DataSourceProtocolError, ValueError) as exc:
            raise DataSourcePublisherError("当前数据源授权已失效或范围不再满足发布条件") from exc
        if authorized_at is None:
            if expected != authorization:
                raise DataSourcePublisherError("数据源配置或 adapter metadata 在发布过程中发生变化")
        elif expected.source.config_sha256 != authorization.source.config_sha256:
            raise DataSourcePublisherError("数据源配置在 DatasetVersion 发布前发生变化")

    @staticmethod
    def _adapter_metadata(adapter: DataSourceAdapter, spec: SourcePublicationSpec) -> AdapterMetadata:
        try:
            metadata = adapter.metadata(spec.request.scope)
        except Exception as exc:  # adapter 是外部边界；转换为明确失败关闭错误。
            raise DataSourcePublisherError("adapter.metadata() 失败") from exc
        if not isinstance(metadata, AdapterMetadata):
            raise DataSourcePublisherError("adapter.metadata() 必须返回 AdapterMetadata")
        if metadata.adapter_id != _adapter_id(adapter):
            raise DataSourcePublisherError("adapter.metadata().adapter_id 与 adapter.adapter_id 不一致")
        return metadata

    @staticmethod
    def _fetch(adapter: DataSourceAdapter, spec: SourcePublicationSpec) -> RawCapture:
        try:
            capture = adapter.fetch(spec.request)
        except Exception as exc:  # adapter 是外部边界；不得静默回退或伪造输入。
            raise DataSourcePublisherError("adapter.fetch() 失败") from exc
        if not isinstance(capture, RawCapture):
            raise DataSourcePublisherError("adapter.fetch() 必须返回 RawCapture")
        return capture

    @staticmethod
    def _raw_artifact(
        authorization: PublicationAuthorization,
        metadata: AdapterMetadata,
        capture: RawCapture,
        spec: SourcePublicationSpec,
    ) -> RawArtifact:
        return RawArtifact(
            metadata=ArtifactMetadata(
                artifact_id=spec.raw_artifact_id,
                source_id=authorization.source.source_id,
                acquired_at=spec.acquired_at,
                available_at=capture.available_at,
                schema_version=f"raw.{metadata.implementation_version}",
                content_hash=content_sha256(capture.payload, field_name="RawCapture.payload"),
                transform_version=f"capture.{metadata.implementation_version}",
                quality_status=capture.capture_quality_status,
                provenance=ArtifactProvenance(
                    source_id=authorization.source.source_id,
                    source_reference=capture.source_reference,
                    collection_method=capture.collection_method,
                    attributes=_publication_attributes(
                        capture.provenance_attributes,
                        adapter_metadata_hash=metadata.identity_hash,
                        authorization_hash=authorization.authorization_hash,
                    ),
                ),
            ),
            raw_format=capture.raw_format,
        )

    @staticmethod
    def _candidate_normalized(
        *,
        adapter: DataSourceAdapter,
        authorization: PublicationAuthorization,
        metadata: AdapterMetadata,
        raw: RawArtifact,
        raw_payload: bytes,
        spec: SourcePublicationSpec,
    ) -> tuple[NormalizedArtifact, NormalizedTable]:
        if spec.normalized_available_at < raw.available_at:
            raise DataSourcePublisherError("normalized_available_at 不能早于 raw.available_at")
        normalized_tables: list[NormalizedTable] = []

        def normalize(payload: bytes) -> bytes:
            try:
                normalized = adapter.normalize(payload, metadata=metadata)
            except Exception as exc:  # adapter 是外部边界；确定性失败不能退化为旧路径。
                raise DataSourcePublisherError("adapter.normalize() 失败") from exc
            if not isinstance(normalized, NormalizedTable):
                raise DataSourcePublisherError("adapter.normalize() 必须返回 NormalizedTable")
            normalized_tables.append(normalized)
            return normalized.payload

        try:
            candidate = NormalizedArtifact.from_deterministic_transform(
                artifact_id=spec.normalized_artifact_id,
                raw_artifact=raw,
                raw_payload=raw_payload,
                normalize=normalize,
                acquired_at=spec.acquired_at,
                available_at=spec.normalized_available_at,
                schema_version=metadata.normalized_schema_version,
                transform_version=metadata.transform_version,
                quality_status=QualityStatus.PASS,
                provenance=ArtifactProvenance(
                    source_id=authorization.source.source_id,
                    source_reference=raw.provenance.source_reference,
                    collection_method="adapter-normalize",
                    attributes=_publication_attributes(
                        raw.provenance.attributes,
                        adapter_metadata_hash=metadata.identity_hash,
                        authorization_hash=authorization.authorization_hash,
                    ),
                ),
            )
        except (DataQualityError, ValueError) as exc:
            raise DataSourcePublisherError("normalized 确定性转换或元数据校验失败") from exc
        if len(normalized_tables) != 2:
            raise DataSourcePublisherError("normalized 确定性校验未执行两次，已拒绝")
        first, second = normalized_tables
        if first.payload != second.payload:
            raise DataSourcePublisherError("同一 raw 输入产生不同 normalized payload")
        if first.content_hash != candidate.content_hash:
            raise DataSourcePublisherError("normalized payload 与候选制品内容哈希不一致")
        return candidate, first

    def _evaluate(
        self,
        *,
        candidate: NormalizedArtifact,
        normalized: NormalizedTable,
        spec: SourcePublicationSpec,
    ) -> QualityEvaluation:
        try:
            request = spec.quality_request_builder.build(
                candidate=candidate,
                normalized=normalized,
                checked_at=spec.checked_at,
                decision_at=spec.decision_at,
            )
        except Exception as exc:
            raise DataSourcePublisherError("quality_request_builder.build() 失败") from exc
        if not isinstance(request, QualityRequest):
            raise DataSourcePublisherError("quality_request_builder 必须返回 QualityRequest")
        if ArtifactSnapshot.from_artifact(request.artifact) != ArtifactSnapshot.from_artifact(
            _as_artifact(candidate)
        ):
            raise DataSourcePublisherError("QualityRequest 必须精确引用发布器生成的候选制品")
        if request.evaluated_payload != normalized.payload:
            raise DataSourcePublisherError("QualityRequest 必须精确引用 adapter 的 canonical payload")
        if request.checked_at != spec.checked_at or request.decision_at != spec.decision_at:
            raise DataSourcePublisherError("QualityRequest 时间必须与发布 spec 精确一致")
        try:
            return self._quality_engine.evaluate(request)
        except DataQualityError as exc:
            raise DataSourcePublisherError("Data Quality Engine 拒绝候选 normalized 制品") from exc


def _validate_adapter(adapter: object) -> None:
    if not isinstance(adapter, DataSourceAdapter):
        raise DataSourcePublisherError("adapter 必须实现 DataSourceAdapter 协议")
    _adapter_id(adapter)


def _as_artifact(artifact: RawArtifact | NormalizedArtifact) -> Artifact:
    """收窄冻结领域制品与历史可写 Protocol 注解之间的静态类型差异。"""

    return cast(Artifact, artifact)


def _adapter_id(adapter: DataSourceAdapter) -> str:
    try:
        return _artifact_id(adapter.adapter_id, "adapter.adapter_id")
    except Exception as exc:
        raise DataSourcePublisherError("adapter.adapter_id 无效") from exc


def _artifact_id(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _ARTIFACT_ID_RE.fullmatch(value) is None:
        raise DataSourcePublisherError(f"{field_name} 必须是受限、无空白的稳定标识")
    return value


def _utc_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DataSourcePublisherError(f"{field_name} 必须是带时区 datetime")
    return value.astimezone(UTC)


def _publication_attributes(
    values: tuple[tuple[str, str], ...],
    *,
    adapter_metadata_hash: str,
    authorization_hash: str,
) -> tuple[tuple[str, str], ...]:
    reserved = {
        "adapter_metadata_hash": adapter_metadata_hash,
        # ``ArtifactProvenance`` 的秘密过滤器刻意拒绝 attribute key 中的
        # "authorization"；这里记录的是无密钥、内容寻址的发布收据身份，故使用中性的
        # receipt 名称。正式 record 关系仍使用 publication_authorization_hash。
        "publication_receipt_hash": authorization_hash,
    }
    merged = dict(values)
    # Raw → normalized 会继承 raw 的 provenance。保留字段若是本发布器已经写入的同一值，
    # 可以幂等地继续传递；任一试图伪造或替换的值则必须失败关闭。
    for key, value in reserved.items():
        existing = merged.get(key)
        if existing is not None and existing != value:
            raise DataSourcePublisherError("adapter provenance_attributes 试图覆盖发布器保留字段")
        merged[key] = value
    return tuple(sorted(merged.items()))


__all__ = [
    "AssessedSourcePublication",
    "DataSourceAdapterRegistry",
    "DataSourcePublisher",
    "DataSourcePublisherError",
    "PublishedSourceDataset",
    "QualityRequestBuilder",
    "SourcePublicationSpec",
]
