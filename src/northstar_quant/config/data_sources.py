"""数据供应商、授权边界与可用用途的严格配置加载器。

``adapter_id`` 只描述本项目的技术适配器；``source_id`` 和 ``license`` 才描述
数据的法律/运营身份。两者刻意分离，避免把“能下载”误解为“已获授权”。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any

from northstar_quant.config.settings import get_settings
from northstar_quant.config.yaml_loader import load_yaml


class DataSourceConfigError(ValueError):
    """数据源、授权或用途配置不完整。"""


_ROOT_FIELDS = frozenset({"version", "sources"})
_SOURCE_FIELDS = frozenset(
    {
        "source_id",
        "adapter_id",
        "name",
        "tier",
        "status",
        "official_references",
        "supported",
        "license",
    }
)
_SUPPORTED_FIELDS = frozenset(
    {
        "markets",
        "asset_types",
        "frequencies",
        "actual_contract_data",
        "authoritative_calendar",
        "authoritative_dynamic_rules",
    }
)
_LICENSE_FIELDS = frozenset(
    {
        "status",
        "legal_entity",
        "contract_ref",
        "order_form_ref",
        "effective_from",
        "expires_on",
        "last_verified_at",
        "verified_by",
        "authorized_exchanges",
        "authorized_products",
        "authorized_datasets",
        "authorized_frequencies",
        "authorized_environments",
        "permitted_purposes",
        "prohibited_purposes",
        "allows_internal_storage",
        "retention_days",
        "allows_derived_data_storage",
        "allows_model_training",
        "allows_redistribution",
        "allows_public_display",
        "allows_live_trading",
        "credential_env_var",
        "vendor_terms_url",
        "contract_document_sha256",
        "exchange_authorization_evidence",
        "request_rate_limit_per_minute",
    }
)
_EXCHANGE_EVIDENCE_FIELDS = frozenset(
    {"exchange", "evidence_ref", "evidence_url", "document_sha256", "verified_at"}
)

_SOURCE_TIERS = frozenset(
    {"public_reference", "commercial_candidate", "commercial_licensed", "local_import"}
)
_SOURCE_STATUSES = frozenset({"research_only", "procurement_pending", "active", "retired"})
_LICENSE_STATUSES = frozenset(
    {"public_reference_unverified", "procurement_pending", "active", "expired", "rejected"}
)
_PURPOSES = frozenset(
    {
        "internal_research",
        "historical_backtest",
        "model_validation",
        "local_simulation",
        "live_signal",
    }
)
_PROHIBITED_PURPOSES = frozenset(
    {
        "redistribution",
        "resale",
        "public_raw_data_publication",
        "external_api_serving",
        "third_party_access",
        "model_training",
        "live_trading",
    }
)


@dataclass(frozen=True, slots=True)
class DataSourceSupport:
    """供应商声明支持的数据维度；权威性必须与行情覆盖分别表达。"""

    markets: tuple[str, ...]
    asset_types: tuple[str, ...]
    frequencies: tuple[str, ...]
    actual_contract_data: bool
    authoritative_calendar: bool
    authoritative_dynamic_rules: bool


@dataclass(frozen=True, slots=True)
class DataSourceLicense:
    """仅存脱敏授权元数据，绝不存合同原件、账号或令牌。"""

    status: str
    legal_entity: str | None
    contract_ref: str | None
    order_form_ref: str | None
    effective_from: str | None
    expires_on: str | None
    last_verified_at: str | None
    verified_by: str | None
    authorized_exchanges: tuple[str, ...]
    authorized_products: tuple[str, ...]
    authorized_datasets: tuple[str, ...]
    authorized_frequencies: tuple[str, ...]
    authorized_environments: tuple[str, ...]
    permitted_purposes: tuple[str, ...]
    prohibited_purposes: tuple[str, ...]
    allows_internal_storage: bool
    retention_days: int
    allows_derived_data_storage: bool
    allows_model_training: bool
    allows_redistribution: bool
    allows_public_display: bool
    allows_live_trading: bool
    credential_env_var: str | None
    vendor_terms_url: str | None
    contract_document_sha256: str | None
    exchange_authorization_evidence: tuple["ExchangeAuthorizationEvidence", ...]
    request_rate_limit_per_minute: int | None

    @property
    def is_active(self) -> bool:
        """只有有效期未过且状态为 active 的合同才可作为准入证据。"""

        if (
            self.status != "active"
            or not self.legal_entity
            or not self.contract_ref
            or not self.effective_from
            or not self.expires_on
            or not self.last_verified_at
            or not self.verified_by
            or not self.contract_document_sha256
            or not self.exchange_authorization_evidence
        ):
            return False
        return date.fromisoformat(self.expires_on) >= date.today()


@dataclass(frozen=True, slots=True)
class ExchangeAuthorizationEvidence:
    """一份按交易所保存的脱敏授权/转发资格证据。"""

    exchange: str
    evidence_ref: str
    evidence_url: str | None
    document_sha256: str
    verified_at: str


@dataclass(frozen=True, slots=True)
class DataSourceConfig:
    """一个可被画像引用的数据来源。"""

    source_id: str
    adapter_id: str
    name: str
    tier: str
    status: str
    official_references: tuple[str, ...]
    supported: DataSourceSupport
    license: DataSourceLicense

    @property
    def is_research_admission_eligible(self) -> bool:
        """判断是否已满足候选研究所需的最低供应商与授权状态。"""

        return (
            self.tier == "commercial_licensed"
            and self.status == "active"
            and self.license.is_active
            and {"internal_research", "historical_backtest"}.issubset(
                self.license.permitted_purposes
            )
        )

    def supports(self, *, market: str, asset_type: str, frequency: str) -> bool:
        """验证画像维度与供应商声明能力一致。"""

        return (
            market.upper() in self.supported.markets
            and asset_type.upper() in self.supported.asset_types
            and frequency.lower() in self.supported.frequencies
        )


def get_data_source_config_path(path: str | Path | None = None) -> Path:
    """返回数据源注册表路径。"""

    if path is None:
        return get_settings().project_root / "configs" / "data" / "sources.yaml"
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = get_settings().project_root / candidate
    return candidate.resolve()


def load_data_sources(path: str | Path | None = None) -> tuple[DataSourceConfig, ...]:
    """读取全部数据源，并拒绝未知字段或不安全的 active 声明。"""

    config_path = get_data_source_config_path(path)
    if not config_path.is_file():
        raise DataSourceConfigError(f"数据源配置不存在：{config_path}")
    payload = load_yaml(config_path)
    if not isinstance(payload, dict) or set(payload) != _ROOT_FIELDS:
        raise DataSourceConfigError("数据源配置只能包含 version 和 sources")
    if payload["version"] != 1:
        raise DataSourceConfigError("数据源配置 version 当前必须为 1")
    raw_sources = payload["sources"]
    if not isinstance(raw_sources, list) or not raw_sources:
        raise DataSourceConfigError("数据源配置 sources 必须是非空列表")

    sources = tuple(_parse_source(item, index=index) for index, item in enumerate(raw_sources))
    source_ids = [source.source_id for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise DataSourceConfigError("数据源配置 source_id 不能重复")
    adapter_ids = [source.adapter_id for source in sources]
    if len(adapter_ids) != len(set(adapter_ids)):
        raise DataSourceConfigError("数据源配置 adapter_id 不能重复")
    return sources


def get_data_source(
    source_id: str,
    path: str | Path | None = None,
) -> DataSourceConfig:
    """按稳定 ID 获取一个数据源。"""

    normalized = _required_text(source_id, "source_id")
    for source in load_data_sources(path):
        if source.source_id == normalized:
            return source
    raise DataSourceConfigError(f"未配置数据源：{normalized}")


def data_source_config_sha256(source: DataSourceConfig) -> str:
    """计算单个数据源配置指纹，供数据 manifest 和回测清单冻结。"""

    encoded = json.dumps(
        asdict(source),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def list_data_source_summaries(path: str | Path | None = None) -> list[dict[str, object]]:
    """返回不会泄露合同或凭据的供应源摘要。"""

    return [
        {
            "source_id": source.source_id,
            "adapter_id": source.adapter_id,
            "name": source.name,
            "tier": source.tier,
            "status": source.status,
            "license_status": source.license.status,
            "research_admission_eligible": source.is_research_admission_eligible,
            "allows_live_trading": source.license.allows_live_trading,
            "config_sha256": data_source_config_sha256(source),
        }
        for source in load_data_sources(path)
    ]


def _parse_source(payload: Any, *, index: int) -> DataSourceConfig:
    context = f"sources[{index}]"
    if not isinstance(payload, dict) or set(payload) != _SOURCE_FIELDS:
        raise DataSourceConfigError(f"{context} 字段不完整或包含未知字段")
    tier = _choice(payload["tier"], _SOURCE_TIERS, f"{context}.tier")
    status = _choice(payload["status"], _SOURCE_STATUSES, f"{context}.status")
    official_references = _text_list(
        payload["official_references"],
        f"{context}.official_references",
        minimum=1,
    )
    supported = _parse_supported(payload["supported"], context)
    license_config = _parse_license(payload["license"], context)
    source = DataSourceConfig(
        source_id=_required_text(payload["source_id"], f"{context}.source_id"),
        adapter_id=_required_text(payload["adapter_id"], f"{context}.adapter_id"),
        name=_required_text(payload["name"], f"{context}.name"),
        tier=tier,
        status=status,
        official_references=official_references,
        supported=supported,
        license=license_config,
    )
    _validate_source_state(source, context)
    return source


def _parse_supported(payload: Any, context: str) -> DataSourceSupport:
    field = f"{context}.supported"
    if not isinstance(payload, dict) or set(payload) != _SUPPORTED_FIELDS:
        raise DataSourceConfigError(f"{field} 字段不完整或包含未知字段")
    return DataSourceSupport(
        markets=_upper_text_list(payload["markets"], f"{field}.markets", minimum=1),
        asset_types=_upper_text_list(
            payload["asset_types"], f"{field}.asset_types", minimum=1
        ),
        frequencies=_lower_text_list(
            payload["frequencies"], f"{field}.frequencies", minimum=1
        ),
        actual_contract_data=_boolean(
            payload["actual_contract_data"], f"{field}.actual_contract_data"
        ),
        authoritative_calendar=_boolean(
            payload["authoritative_calendar"], f"{field}.authoritative_calendar"
        ),
        authoritative_dynamic_rules=_boolean(
            payload["authoritative_dynamic_rules"],
            f"{field}.authoritative_dynamic_rules",
        ),
    )


def _parse_license(payload: Any, context: str) -> DataSourceLicense:
    field = f"{context}.license"
    if not isinstance(payload, dict) or set(payload) != _LICENSE_FIELDS:
        raise DataSourceConfigError(f"{field} 字段不完整或包含未知字段")
    license_status = _choice(payload["status"], _LICENSE_STATUSES, f"{field}.status")
    effective_from = _optional_date(payload["effective_from"], f"{field}.effective_from")
    expires_on = _optional_date(payload["expires_on"], f"{field}.expires_on")
    retention_days = _nonnegative_int(payload["retention_days"], f"{field}.retention_days")
    return DataSourceLicense(
        status=license_status,
        legal_entity=_optional_text(payload["legal_entity"], f"{field}.legal_entity"),
        contract_ref=_optional_text(payload["contract_ref"], f"{field}.contract_ref"),
        order_form_ref=_optional_text(payload["order_form_ref"], f"{field}.order_form_ref"),
        effective_from=effective_from,
        expires_on=expires_on,
        last_verified_at=_optional_date(payload["last_verified_at"], f"{field}.last_verified_at"),
        verified_by=_optional_text(payload["verified_by"], f"{field}.verified_by"),
        authorized_exchanges=_upper_text_list(
            payload["authorized_exchanges"], f"{field}.authorized_exchanges"
        ),
        authorized_products=_upper_text_list(
            payload["authorized_products"], f"{field}.authorized_products"
        ),
        authorized_datasets=_text_list(
            payload["authorized_datasets"], f"{field}.authorized_datasets"
        ),
        authorized_frequencies=_lower_text_list(
            payload["authorized_frequencies"], f"{field}.authorized_frequencies"
        ),
        authorized_environments=_lower_text_list(
            payload["authorized_environments"], f"{field}.authorized_environments"
        ),
        permitted_purposes=_choice_list(
            payload["permitted_purposes"], _PURPOSES, f"{field}.permitted_purposes"
        ),
        prohibited_purposes=_choice_list(
            payload["prohibited_purposes"],
            _PROHIBITED_PURPOSES,
            f"{field}.prohibited_purposes",
        ),
        allows_internal_storage=_boolean(
            payload["allows_internal_storage"], f"{field}.allows_internal_storage"
        ),
        retention_days=retention_days,
        allows_derived_data_storage=_boolean(
            payload["allows_derived_data_storage"],
            f"{field}.allows_derived_data_storage",
        ),
        allows_model_training=_boolean(
            payload["allows_model_training"], f"{field}.allows_model_training"
        ),
        allows_redistribution=_boolean(
            payload["allows_redistribution"], f"{field}.allows_redistribution"
        ),
        allows_public_display=_boolean(
            payload["allows_public_display"], f"{field}.allows_public_display"
        ),
        allows_live_trading=_boolean(
            payload["allows_live_trading"], f"{field}.allows_live_trading"
        ),
        credential_env_var=_optional_text(
            payload["credential_env_var"], f"{field}.credential_env_var"
        ),
        vendor_terms_url=_optional_text(
            payload["vendor_terms_url"], f"{field}.vendor_terms_url"
        ),
        contract_document_sha256=_optional_sha256(
            payload["contract_document_sha256"], f"{field}.contract_document_sha256"
        ),
        exchange_authorization_evidence=_exchange_evidence_list(
            payload["exchange_authorization_evidence"], f"{field}.exchange_authorization_evidence"
        ),
        request_rate_limit_per_minute=_optional_positive_int(
            payload["request_rate_limit_per_minute"], f"{field}.request_rate_limit_per_minute"
        ),
    )


def _validate_source_state(source: DataSourceConfig, context: str) -> None:
    license_config = source.license
    if source.status == "active" and source.tier != "commercial_licensed":
        raise DataSourceConfigError(f"{context} active 数据源必须是 commercial_licensed")
    if source.status == "active" and not license_config.is_active:
        raise DataSourceConfigError(f"{context} active 数据源必须具备有效且未过期的合同证据")
    if license_config.status == "active" and not license_config.is_active:
        raise DataSourceConfigError(f"{context}.license active 状态缺少有效合同或到期日")
    if license_config.is_active:
        if not license_config.authorized_exchanges:
            raise DataSourceConfigError(f"{context}.license active 状态必须列出已授权交易所")
        evidence_exchanges = {
            evidence.exchange for evidence in license_config.exchange_authorization_evidence
        }
        if not set(license_config.authorized_exchanges).issubset(evidence_exchanges):
            raise DataSourceConfigError(
                f"{context}.license 授权交易所必须逐个提供授权证据"
            )
    if source.status != "active" and license_config.allows_live_trading:
        raise DataSourceConfigError(f"{context} 非 active 数据源不得声明允许 live_trading")
    if license_config.allows_redistribution or license_config.allows_public_display:
        raise DataSourceConfigError(f"{context} 不允许在此项目配置中放宽再分发或公开展示")
    if source.tier == "public_reference" and license_config.status != "public_reference_unverified":
        raise DataSourceConfigError(f"{context} 公开参考源必须标记为 public_reference_unverified")


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataSourceConfigError(f"{field} 必须是非空字符串")
    return value.strip()


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field)


def _optional_date(value: object, field: str) -> str | None:
    text = _optional_text(value, field)
    if text is None:
        return None
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise DataSourceConfigError(f"{field} 必须是 YYYY-MM-DD 或 null") from exc
    return text


def _optional_sha256(value: object, field: str) -> str | None:
    text = _optional_text(value, field)
    if text is None:
        return None
    normalized = text.lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise DataSourceConfigError(f"{field} 必须是 64 位 SHA-256 或 null")
    return normalized


def _exchange_evidence_list(value: object, field: str) -> tuple[ExchangeAuthorizationEvidence, ...]:
    if not isinstance(value, list):
        raise DataSourceConfigError(f"{field} 必须是列表")
    evidence: list[ExchangeAuthorizationEvidence] = []
    for index, item in enumerate(value):
        context = f"{field}[{index}]"
        if not isinstance(item, dict) or set(item) != _EXCHANGE_EVIDENCE_FIELDS:
            raise DataSourceConfigError(f"{context} 字段不完整或包含未知字段")
        evidence.append(
            ExchangeAuthorizationEvidence(
                exchange=_required_text(item["exchange"], f"{context}.exchange").upper(),
                evidence_ref=_required_text(item["evidence_ref"], f"{context}.evidence_ref"),
                evidence_url=_optional_text(item["evidence_url"], f"{context}.evidence_url"),
                document_sha256=_optional_sha256(
                    item["document_sha256"], f"{context}.document_sha256"
                )
                or "",
                verified_at=_optional_date(item["verified_at"], f"{context}.verified_at") or "",
            )
        )
    exchanges = [item.exchange for item in evidence]
    if len(exchanges) != len(set(exchanges)):
        raise DataSourceConfigError(f"{field} exchange 不能重复")
    if any(not item.document_sha256 or not item.verified_at for item in evidence):
        raise DataSourceConfigError(f"{field} 必须包含文档 SHA-256 和核验日期")
    return tuple(evidence)


def _optional_positive_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DataSourceConfigError(f"{field} 必须是正整数或 null")
    return value


def _choice(value: object, allowed: frozenset[str], field: str) -> str:
    text = _required_text(value, field).lower()
    if text not in allowed:
        raise DataSourceConfigError(f"{field} 取值无效；仅支持：{', '.join(sorted(allowed))}")
    return text


def _choice_list(value: object, allowed: frozenset[str], field: str) -> tuple[str, ...]:
    values = _lower_text_list(value, field)
    invalid = sorted(set(values).difference(allowed))
    if invalid:
        raise DataSourceConfigError(f"{field} 包含不支持的值：{', '.join(invalid)}")
    return values


def _text_list(value: object, field: str, *, minimum: int = 0) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise DataSourceConfigError(f"{field} 必须是列表")
    values = tuple(_required_text(item, f"{field}[]") for item in value)
    if len(values) < minimum or len(values) != len(set(values)):
        raise DataSourceConfigError(f"{field} 必须满足最小数量且不能重复")
    return values


def _upper_text_list(value: object, field: str, *, minimum: int = 0) -> tuple[str, ...]:
    values = tuple(item.upper() for item in _text_list(value, field, minimum=minimum))
    if len(values) != len(set(values)):
        raise DataSourceConfigError(f"{field} 规范化后不能重复")
    return values


def _lower_text_list(value: object, field: str, *, minimum: int = 0) -> tuple[str, ...]:
    values = tuple(item.lower() for item in _text_list(value, field, minimum=minimum))
    if len(values) != len(set(values)):
        raise DataSourceConfigError(f"{field} 规范化后不能重复")
    return values


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise DataSourceConfigError(f"{field} 必须是布尔值")
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DataSourceConfigError(f"{field} 必须是非负整数")
    return value
