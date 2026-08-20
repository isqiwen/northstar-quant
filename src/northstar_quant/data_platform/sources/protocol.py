"""P1-WP06 数据源适配器的纯值协议与发布授权边界。

本模块只定义来源适配器交换的不可变值对象，以及把受管 ``DataSourceConfig`` 冻结为一次
可发布授权的规则。它不下载、不读取凭据、不写入制品库，也不调用 legacy downloader。

``DataSource`` 是来源和授权的历史快照；``AdapterMetadata`` 只描述技术实现。二者必须
由 :func:`build_publication_authorization` 在显式时间点交叉验证，避免“某个 adapter 能运行”
被误解为“该数据可以保存或用于研究”。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
import re
from typing import Protocol, runtime_checkable

import polars as pl

from northstar_quant.data_platform.artifacts.fingerprints import (
    canonical_json_sha256,
    content_sha256,
)
from northstar_quant.data_platform.contracts.data_domain import (
    DataDomainError,
    DataSource,
    QualityStatus,
)
from northstar_quant.data_platform.quality.models import canonical_frame_payload
from northstar_quant.platform.config.data_sources import (
    DataSourceConfig,
    DataSourceLicense,
    DataSourceSupport,
)


CANONICAL_NORMALIZED_FORMAT = "northstar.data_quality.canonical_frame.v1"


class DataSourceProtocolError(ValueError):
    """数据源适配器、授权范围或捕获证据不满足协议时抛出。"""


class PublicationPurpose(str, Enum):
    """与受管数据源授权表一致的、显式的数据使用目的。"""

    INTERNAL_RESEARCH = "internal_research"
    HISTORICAL_BACKTEST = "historical_backtest"
    MODEL_VALIDATION = "model_validation"
    LOCAL_SIMULATION = "local_simulation"
    LIVE_SIGNAL = "live_signal"


_SECRET_PATTERN = re.compile(
    r"(?i)(?:authorization|bearer|api[ _-]?key|credential|token|secret|password|passwd|cookie)"
)
_WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")


@dataclass(frozen=True, slots=True)
class PublicationScope:
    """一次 raw/normalized 制品发布声明的事实范围和用途。

    范围不能由 adapter 自行扩大。它在授权构建时同时与 source capability、合同范围和
    运行环境核对；没有列入 ``DataSourceConfig.license`` 的数据集、交易所、品种或用途
    一律不能发布。
    """

    dataset_id: str
    market: str
    asset_type: str
    frequency: str
    purpose: PublicationPurpose
    environment: str
    exchanges: tuple[str, ...] = ()
    products: tuple[str, ...] = ()
    actual_contract_data: bool = False
    requires_authoritative_calendar: bool = False
    requires_authoritative_dynamic_rules: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "dataset_id", _identifier(self.dataset_id, "scope.dataset_id"))
        object.__setattr__(self, "market", _identifier(self.market, "scope.market").upper())
        object.__setattr__(
            self,
            "asset_type",
            _identifier(self.asset_type, "scope.asset_type").upper(),
        )
        object.__setattr__(
            self,
            "frequency",
            _identifier(self.frequency, "scope.frequency").lower(),
        )
        if not isinstance(self.purpose, PublicationPurpose):
            raise DataSourceProtocolError("scope.purpose 必须是 PublicationPurpose")
        object.__setattr__(
            self,
            "environment",
            _identifier(self.environment, "scope.environment").lower(),
        )
        object.__setattr__(
            self,
            "exchanges",
            _canonical_identifiers(self.exchanges, "scope.exchanges", upper=True),
        )
        object.__setattr__(
            self,
            "products",
            _canonical_identifiers(self.products, "scope.products", upper=True),
        )
        for field_name in (
            "actual_contract_data",
            "requires_authoritative_calendar",
            "requires_authoritative_dynamic_rules",
        ):
            _require_bool(getattr(self, field_name), f"scope.{field_name}")

    @property
    def identity_hash(self) -> str:
        """范围的稳定、无运行时路径或凭据身份。"""

        return canonical_json_sha256(
            {
                "actual_contract_data": self.actual_contract_data,
                "asset_type": self.asset_type,
                "dataset_id": self.dataset_id,
                "environment": self.environment,
                "exchanges": self.exchanges,
                "frequency": self.frequency,
                "market": self.market,
                "products": self.products,
                "purpose": self.purpose.value,
                "requires_authoritative_calendar": self.requires_authoritative_calendar,
                "requires_authoritative_dynamic_rules": (
                    self.requires_authoritative_dynamic_rules
                ),
            }
        )


@dataclass(frozen=True, slots=True)
class AdapterMetadata:
    """适配器技术身份；不得承载法律授权、账号或凭据。"""

    adapter_id: str
    implementation_version: str
    raw_format: str
    normalized_schema_version: str
    transform_version: str
    normalized_format: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapter_id", _identifier(self.adapter_id, "adapter_id"))
        for field_name in (
            "implementation_version",
            "raw_format",
            "normalized_schema_version",
            "transform_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _safe_text(getattr(self, field_name), f"adapter.{field_name}"),
            )
        normalized_format = _safe_text(self.normalized_format, "adapter.normalized_format")
        if normalized_format != CANONICAL_NORMALIZED_FORMAT:
            raise DataSourceProtocolError(
                "adapter.normalized_format 必须是受支持的 canonical frame 格式"
            )
        object.__setattr__(self, "normalized_format", normalized_format)

    @property
    def identity_hash(self) -> str:
        """技术实现、schema 与 transform 的稳定身份。"""

        return canonical_json_sha256(
            {
                "adapter_id": self.adapter_id,
                "implementation_version": self.implementation_version,
                "normalized_format": self.normalized_format,
                "normalized_schema_version": self.normalized_schema_version,
                "raw_format": self.raw_format,
                "transform_version": self.transform_version,
            }
        )


@dataclass(frozen=True, slots=True)
class SourceFetchRequest:
    """一次获取请求的脱敏、可审计输入。

    ``request_parameters`` 只保存有限文本参数且按键排序；真实 credential 必须由调用层在
    内存中注入 adapter，永不属于本协议或 provenance。
    """

    source_id: str
    scope: PublicationScope
    request_reference: str
    requested_at: datetime
    request_parameters: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _identifier(self.source_id, "request.source_id"))
        if not isinstance(self.scope, PublicationScope):
            raise DataSourceProtocolError("request.scope 必须是 PublicationScope")
        object.__setattr__(
            self,
            "request_reference",
            _safe_text(self.request_reference, "request.request_reference"),
        )
        object.__setattr__(self, "requested_at", _utc_datetime(self.requested_at, "request.requested_at"))
        object.__setattr__(
            self,
            "request_parameters",
            _canonical_attributes(self.request_parameters, "request.request_parameters"),
        )

    @property
    def identity_hash(self) -> str:
        """请求身份；不包含本机路径、令牌或可变对象。"""

        return canonical_json_sha256(
            {
                "request_parameters": self.request_parameters,
                "request_reference": self.request_reference,
                "requested_at": self.requested_at.isoformat(),
                "scope_hash": self.scope.identity_hash,
                "source_id": self.source_id,
            }
        )


@dataclass(frozen=True, slots=True)
class RawCapture:
    """adapter 获取到的原始 bytes 与脱敏采集证据。

    这里的 ``capture_quality_status`` 仅说明采集完整性/可读取性，不等同于 normalized 数据的
    十项语义质量结论；后者必须经过 Data Quality Engine。
    """

    payload: bytes
    raw_format: str
    source_reference: str
    collection_method: str
    available_at: datetime
    capture_quality_status: QualityStatus
    provenance_attributes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes):
            raise DataSourceProtocolError("capture.payload 必须是 bytes")
        object.__setattr__(self, "raw_format", _safe_text(self.raw_format, "capture.raw_format"))
        object.__setattr__(
            self,
            "source_reference",
            _safe_text(self.source_reference, "capture.source_reference"),
        )
        object.__setattr__(
            self,
            "collection_method",
            _safe_text(self.collection_method, "capture.collection_method"),
        )
        object.__setattr__(self, "available_at", _utc_datetime(self.available_at, "capture.available_at"))
        if not isinstance(self.capture_quality_status, QualityStatus):
            raise DataSourceProtocolError("capture.capture_quality_status 必须是 QualityStatus")
        object.__setattr__(
            self,
            "provenance_attributes",
            _canonical_attributes(self.provenance_attributes, "capture.provenance_attributes"),
        )

    @property
    def content_hash(self) -> str:
        """raw bytes 的 SHA-256；发布器仍必须与 RawArtifact 再次核对。"""

        return content_sha256(self.payload, field_name="capture.payload")


@dataclass(frozen=True, slots=True)
class NormalizedTable:
    """可进入质量引擎的标准表与严格绑定的 canonical payload。"""

    frame: pl.DataFrame
    payload: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.frame, pl.DataFrame):
            raise DataSourceProtocolError("normalized.frame 必须是 Polars DataFrame")
        if not isinstance(self.payload, bytes):
            raise DataSourceProtocolError("normalized.payload 必须是 canonical frame bytes")
        # frozen dataclass 不能阻止 Polars 原地写入；保留独立副本，QualityRequest 仍会在
        # 其边界再次复核 canonical payload。
        frame = self.frame.clone()
        expected_payload = canonical_frame_payload(frame)
        if self.payload != expected_payload:
            raise DataSourceProtocolError(
                "normalized.payload 必须逐字节等于 canonical_frame_payload(frame)"
            )
        object.__setattr__(self, "frame", frame)

    @classmethod
    def from_frame(cls, frame: pl.DataFrame) -> "NormalizedTable":
        """从标准表显式生成唯一允许参与质量评估的 payload。"""

        if not isinstance(frame, pl.DataFrame):
            raise DataSourceProtocolError("normalized.frame 必须是 Polars DataFrame")
        return cls(frame=frame, payload=canonical_frame_payload(frame))

    @property
    def content_hash(self) -> str:
        """canonical normalized bytes 的 SHA-256。"""

        return content_sha256(self.payload, field_name="normalized.payload")


@runtime_checkable
class DataSourceAdapter(Protocol):
    """数据平台适配器的最小行为契约。

    ``fetch`` 可以使用由应用层安全注入的凭据，但返回的 RawCapture 不得泄露它们；
    ``normalize`` 必须是仅依赖 raw bytes 与冻结 metadata 的纯转换。Publisher 会把它交给
    ``NormalizedArtifact.from_deterministic_transform`` 双执行验证。
    """

    @property
    def adapter_id(self) -> str:
        """与受管 ``DataSourceConfig.adapter_id`` 精确匹配的技术标识。"""

    def metadata(self, scope: PublicationScope) -> AdapterMetadata:
        """返回与本次 scope 适配的、无敏感信息的实现 metadata。"""

    def fetch(self, request: SourceFetchRequest) -> RawCapture:
        """获取原始 bytes；不负责授权判定或制品持久化。"""

    def normalize(
        self,
        raw_payload: bytes,
        *,
        metadata: AdapterMetadata,
    ) -> NormalizedTable:
        """把 raw bytes 纯转换为严格 canonical 的标准表。"""


@dataclass(frozen=True, slots=True)
class PublicationAuthorization:
    """由受管配置、scope 和 adapter metadata 冻结的单次发布许可。"""

    source: DataSource
    scope: PublicationScope
    adapter_metadata: AdapterMetadata
    authorized_at: datetime
    authorization_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.source, DataSource):
            raise DataSourceProtocolError("authorization.source 必须是 DataSource")
        if not isinstance(self.scope, PublicationScope):
            raise DataSourceProtocolError("authorization.scope 必须是 PublicationScope")
        if not isinstance(self.adapter_metadata, AdapterMetadata):
            raise DataSourceProtocolError("authorization.adapter_metadata 必须是 AdapterMetadata")
        if self.source.adapter_id != self.adapter_metadata.adapter_id:
            raise DataSourceProtocolError("authorization 的 source.adapter_id 与 adapter metadata 不一致")
        authorized_at = _utc_datetime(self.authorized_at, "authorization.authorized_at")
        object.__setattr__(self, "authorized_at", authorized_at)
        object.__setattr__(
            self,
            "authorization_hash",
            canonical_json_sha256(self.as_mapping()),
        )

    def as_mapping(self) -> dict[str, object]:
        """返回可写入不可变发布收据的完整脱敏授权事实。

        这里同时保存本次实际使用的范围与 DataSource 快照，而不是只留下一个可变配置文件的
        hash。credential、环境变量名、合同原件和 URL query 永远不在这个记录中。
        """

        return {
            "adapter_metadata": {
                "adapter_id": self.adapter_metadata.adapter_id,
                "implementation_version": self.adapter_metadata.implementation_version,
                "normalized_format": self.adapter_metadata.normalized_format,
                "normalized_schema_version": self.adapter_metadata.normalized_schema_version,
                "raw_format": self.adapter_metadata.raw_format,
                "transform_version": self.adapter_metadata.transform_version,
            },
            "authorized_at": self.authorized_at.isoformat(),
            "scope": {
                "actual_contract_data": self.scope.actual_contract_data,
                "asset_type": self.scope.asset_type,
                "dataset_id": self.scope.dataset_id,
                "environment": self.scope.environment,
                "exchanges": list(self.scope.exchanges),
                "frequency": self.scope.frequency,
                "market": self.scope.market,
                "products": list(self.scope.products),
                "purpose": self.scope.purpose.value,
                "requires_authoritative_calendar": self.scope.requires_authoritative_calendar,
                "requires_authoritative_dynamic_rules": (
                    self.scope.requires_authoritative_dynamic_rules
                ),
            },
            "source": {
                "adapter_id": self.source.adapter_id,
                "config_sha256": self.source.config_sha256,
                "license": {
                    "allows_derived_data_storage": self.source.license.allows_derived_data_storage,
                    "allows_internal_storage": self.source.license.allows_internal_storage,
                    "allows_live_trading": self.source.license.allows_live_trading,
                    "contract_reference": self.source.license.contract_reference,
                    "effective_from": self.source.license.effective_from,
                    "expires_on": self.source.license.expires_on,
                    "permitted_purposes": list(self.source.license.permitted_purposes),
                    "status": self.source.license.status,
                    "terms_sha256": self.source.license.terms_sha256,
                },
                "name": self.source.name,
                "official_references": list(self.source.official_references),
                "source_id": self.source.source_id,
                "status": self.source.status,
                "tier": self.source.tier,
            },
        }


def build_publication_authorization(
    source_config: DataSourceConfig,
    scope: PublicationScope,
    adapter_metadata: AdapterMetadata,
    *,
    authorized_at: datetime,
) -> PublicationAuthorization:
    """在明确时间点冻结一次 raw/normalized 发布许可。

    此函数是 adapter 调用前的 fail-closed 预检。它不触网、不读取环境变量，也不会保存
    任何对象；所有 source 许可、范围、用途、存储权和技术 adapter 身份均需同时通过。
    """

    if not isinstance(source_config, DataSourceConfig):
        raise DataSourceProtocolError("source_config 必须是 DataSourceConfig")
    if not isinstance(scope, PublicationScope):
        raise DataSourceProtocolError("scope 必须是 PublicationScope")
    if not isinstance(adapter_metadata, AdapterMetadata):
        raise DataSourceProtocolError("adapter_metadata 必须是 AdapterMetadata")
    authorized_at = _utc_datetime(authorized_at, "authorized_at")

    _validate_source_config_for_publication(source_config, scope, authorized_at)
    if source_config.adapter_id != adapter_metadata.adapter_id:
        raise DataSourceProtocolError(
            "数据源配置 adapter_id 与 adapter metadata.adapter_id 不一致，已拒绝发布"
        )
    try:
        source = DataSource.from_config(source_config)
    except DataDomainError as exc:
        raise DataSourceProtocolError("数据源配置无法冻结为可审计 DataSource") from exc
    return PublicationAuthorization(
        source=source,
        scope=scope,
        adapter_metadata=adapter_metadata,
        authorized_at=authorized_at,
    )


def validate_publication_authorization(
    authorization: PublicationAuthorization,
    source_config: DataSourceConfig,
    adapter_metadata: AdapterMetadata,
    *,
    authorized_at: datetime,
) -> PublicationAuthorization:
    """重新从当前配置构建并精确比对冻结授权。

    配置、adapter metadata、scope 或时间任一变化都返回失败，而不是把旧授权静默投射到
    新发布。返回原对象仅表示它仍可用于同一显式发布时点。
    """

    if not isinstance(authorization, PublicationAuthorization):
        raise DataSourceProtocolError("authorization 必须是 PublicationAuthorization")
    expected = build_publication_authorization(
        source_config,
        authorization.scope,
        adapter_metadata,
        authorized_at=authorized_at,
    )
    if authorization != expected:
        raise DataSourceProtocolError("冻结的发布授权与当前配置、adapter 或时间不一致")
    return authorization


def _validate_source_config_for_publication(
    source_config: DataSourceConfig,
    scope: PublicationScope,
    authorized_at: datetime,
) -> None:
    """对配置模型做不依赖当前时钟的发布前授权核验。"""

    source_id = _identifier(source_config.source_id, "source_config.source_id")
    adapter_id = _identifier(source_config.adapter_id, "source_config.adapter_id")
    if source_config.status != "active":
        raise DataSourceProtocolError(f"数据源 {source_id} 不是 active，已拒绝发布")
    if source_config.tier != "commercial_licensed":
        raise DataSourceProtocolError(f"数据源 {source_id} 不是 commercial_licensed，已拒绝发布")
    if not isinstance(source_config.supported, DataSourceSupport):
        raise DataSourceProtocolError("source_config.supported 必须是 DataSourceSupport")
    if not isinstance(source_config.license, DataSourceLicense):
        raise DataSourceProtocolError("source_config.license 必须是 DataSourceLicense")
    if not adapter_id:
        raise DataSourceProtocolError("source_config.adapter_id 不能为空")

    supported = source_config.supported
    license_config = source_config.license
    _validate_active_license(license_config, authorized_at)
    if not _strict_true(license_config.allows_internal_storage):
        raise DataSourceProtocolError("数据源授权不允许内部保存 raw 或 normalized 制品")
    if license_config.retention_days < 1:
        raise DataSourceProtocolError("数据源授权的 retention_days 必须至少为 1 才能发布制品")
    permitted = _text_set(license_config.permitted_purposes, "license.permitted_purposes")
    prohibited = _text_set(license_config.prohibited_purposes, "license.prohibited_purposes")
    required_purposes = {PublicationPurpose.INTERNAL_RESEARCH.value, scope.purpose.value}
    missing_purposes = sorted(required_purposes.difference(permitted))
    if missing_purposes:
        raise DataSourceProtocolError(
            "数据源授权缺少发布所需用途：" + ", ".join(missing_purposes)
        )
    if scope.purpose.value in prohibited:
        raise DataSourceProtocolError("数据源授权明确禁止当前发布用途")
    if scope.purpose is PublicationPurpose.LIVE_SIGNAL and (
        not _strict_true(license_config.allows_live_trading) or "live_trading" in prohibited
    ):
        raise DataSourceProtocolError("live_signal 发布缺少明确 live trading 授权")

    if not _supported_values(supported.markets, "supported.markets", upper=True) >= {scope.market}:
        raise DataSourceProtocolError("数据源不支持当前 market")
    if not _supported_values(
        supported.asset_types, "supported.asset_types", upper=True
    ) >= {scope.asset_type}:
        raise DataSourceProtocolError("数据源不支持当前 asset_type")
    if not _supported_values(
        supported.frequencies, "supported.frequencies", upper=False
    ) >= {scope.frequency}:
        raise DataSourceProtocolError("数据源不支持当前 frequency")
    if scope.actual_contract_data and not _strict_true(supported.actual_contract_data):
        raise DataSourceProtocolError("数据源不支持 actual contract data")
    if scope.requires_authoritative_calendar and not _strict_true(
        supported.authoritative_calendar
    ):
        raise DataSourceProtocolError("数据源不具备 authoritative calendar 声明")
    if scope.requires_authoritative_dynamic_rules and not _strict_true(
        supported.authoritative_dynamic_rules
    ):
        raise DataSourceProtocolError("数据源不具备 authoritative dynamic rules 声明")

    _require_authorized_membership(
        scope.dataset_id,
        _text_set(license_config.authorized_datasets, "license.authorized_datasets"),
        "dataset",
    )
    _require_authorized_membership(
        scope.frequency,
        _text_set(license_config.authorized_frequencies, "license.authorized_frequencies"),
        "frequency",
    )
    _require_authorized_membership(
        scope.environment,
        _text_set(license_config.authorized_environments, "license.authorized_environments"),
        "environment",
    )
    _require_authorized_subset(
        scope.exchanges,
        _text_set(license_config.authorized_exchanges, "license.authorized_exchanges", upper=True),
        "exchange",
    )
    _require_authorized_subset(
        scope.products,
        _text_set(license_config.authorized_products, "license.authorized_products", upper=True),
        "product",
    )


def _validate_active_license(license_config: DataSourceLicense, authorized_at: datetime) -> None:
    """复刻 active contract 的必要事实，但以调用方传入时点判定。"""

    if license_config.status != "active":
        raise DataSourceProtocolError("数据源 license 不是 active，已拒绝发布")
    for field_name in (
        "legal_entity",
        "contract_ref",
        "effective_from",
        "expires_on",
        "last_verified_at",
        "verified_by",
        "contract_document_sha256",
    ):
        _safe_text(getattr(license_config, field_name), f"license.{field_name}")
    try:
        effective_from = date.fromisoformat(str(license_config.effective_from))
        expires_on = date.fromisoformat(str(license_config.expires_on))
    except ValueError as exc:
        raise DataSourceProtocolError("数据源授权有效期格式不安全") from exc
    if effective_from > expires_on:
        raise DataSourceProtocolError("数据源授权 effective_from 不能晚于 expires_on")
    if not effective_from <= authorized_at.date() <= expires_on:
        raise DataSourceProtocolError("发布时点不在数据源授权有效期内")
    evidence = tuple(license_config.exchange_authorization_evidence)
    if not evidence:
        raise DataSourceProtocolError("active 数据源授权缺少逐交易所证据")
    authorized_exchanges = _text_set(
        license_config.authorized_exchanges,
        "license.authorized_exchanges",
        upper=True,
    )
    evidence_exchanges: set[str] = set()
    for item in evidence:
        exchange = _identifier(getattr(item, "exchange", None), "license.evidence.exchange").upper()
        _safe_text(getattr(item, "evidence_ref", None), "license.evidence.evidence_ref")
        _safe_text(getattr(item, "document_sha256", None), "license.evidence.document_sha256")
        _safe_text(getattr(item, "verified_at", None), "license.evidence.verified_at")
        evidence_exchanges.add(exchange)
    if not authorized_exchanges or not authorized_exchanges.issubset(evidence_exchanges):
        raise DataSourceProtocolError("active 数据源授权缺少已授权交易所的证据")


def _require_authorized_membership(value: str, allowed: set[str], label: str) -> None:
    if value not in allowed:
        raise DataSourceProtocolError(f"数据源授权不包含当前 {label}：{value}")


def _require_authorized_subset(values: tuple[str, ...], allowed: set[str], label: str) -> None:
    missing = sorted(set(values).difference(allowed))
    if missing:
        raise DataSourceProtocolError(
            f"数据源授权不包含当前 {label}：" + ", ".join(missing)
        )


def _supported_values(values: object, field_name: str, *, upper: bool) -> set[str]:
    return _text_set(values, field_name, upper=upper)


def _text_set(values: object, field_name: str, *, upper: bool = False) -> set[str]:
    if not isinstance(values, tuple):
        raise DataSourceProtocolError(f"{field_name} 必须是 tuple")
    result = {
        (_identifier(value, field_name).upper() if upper else _identifier(value, field_name).lower())
        for value in values
    }
    if len(result) != len(values):
        raise DataSourceProtocolError(f"{field_name} 不能包含重复值")
    return result


def _canonical_identifiers(values: object, field_name: str, *, upper: bool) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise DataSourceProtocolError(f"{field_name} 必须是 tuple")
    normalized = tuple(
        _identifier(value, field_name).upper() if upper else _identifier(value, field_name).lower()
        for value in values
    )
    if len(normalized) != len(set(normalized)):
        raise DataSourceProtocolError(f"{field_name} 不能包含重复值")
    return tuple(sorted(normalized))


def _canonical_attributes(values: object, field_name: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(values, tuple):
        raise DataSourceProtocolError(f"{field_name} 必须是 tuple")
    if len(values) > 64:
        raise DataSourceProtocolError(f"{field_name} 不能超过 64 项")
    pairs: list[tuple[str, str]] = []
    for item in values:
        if not isinstance(item, tuple) or len(item) != 2:
            raise DataSourceProtocolError(f"{field_name} 每项必须是 (key, value) 元组")
        key, value = item
        pairs.append(
            (
                _identifier(key, f"{field_name}.key"),
                _safe_text(value, f"{field_name}.value"),
            )
        )
    pairs.sort(key=lambda item: item[0])
    if len({key for key, _ in pairs}) != len(pairs):
        raise DataSourceProtocolError(f"{field_name} 不能包含重复键")
    return tuple(pairs)


def _safe_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataSourceProtocolError(f"{field_name} 必须是非空文本")
    text = value.strip()
    if _SECRET_PATTERN.search(text):
        raise DataSourceProtocolError(f"{field_name} 不得包含凭据、令牌或授权头")
    if text.startswith(("/", "\\\\", "~/", "~\\")) or _WINDOWS_ABSOLUTE_PATH_PATTERN.match(text):
        raise DataSourceProtocolError(f"{field_name} 不得包含本机绝对路径")
    return text


def _identifier(value: object, field_name: str) -> str:
    text = _safe_text(value, field_name)
    if any(character.isspace() for character in text):
        raise DataSourceProtocolError(f"{field_name} 不得包含空白字符")
    return text


def _utc_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DataSourceProtocolError(f"{field_name} 必须是带时区 datetime")
    return value.astimezone(UTC)


def _require_bool(value: object, field_name: str) -> None:
    if type(value) is not bool:
        raise DataSourceProtocolError(f"{field_name} 必须是 bool")


def _strict_true(value: object) -> bool:
    return type(value) is bool and value


__all__ = [
    "AdapterMetadata",
    "CANONICAL_NORMALIZED_FORMAT",
    "DataSourceAdapter",
    "DataSourceProtocolError",
    "NormalizedTable",
    "PublicationAuthorization",
    "PublicationPurpose",
    "PublicationScope",
    "RawCapture",
    "SourceFetchRequest",
    "build_publication_authorization",
    "validate_publication_authorization",
]
