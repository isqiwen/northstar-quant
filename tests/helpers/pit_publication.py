"""PIT 测试使用的受控、授权数据集发布 fixture。

这里故意走 ``DataSourcePublisher``，避免 PIT 测试把手工 ``ArtifactStore.put_*`` 误当成
可消费研究数据。质量规则的细节已由质量/发布器测试覆盖；本 fixture 只提供一个全部 PASS
的、离线且无凭据的授权边界，以测试下游 immutable 数据消费。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
from typing import cast

import polars as pl

from northstar_quant.data.artifacts.immutable_store import ArtifactStore
from northstar_quant.data.contracts.data_domain import (
    Artifact,
    DatasetVersion,
    NormalizedArtifact,
    QualityStatus,
)
from northstar_quant.data.quality import (
    CompletenessRule,
    DataQualityEngine,
    GapRule,
    OrderingRule,
    QualityEvaluation,
    QualityEvidence,
    QualityFinding,
    QualityRequest,
    QualityRule,
    RevisionRule,
    SchemaField,
    StalenessRule,
    UniquenessRule,
)
from northstar_quant.data.sources.protocol import (
    AdapterMetadata,
    CANONICAL_NORMALIZED_FORMAT,
    NormalizedTable,
    PublicationPurpose,
    PublicationScope,
    RawCapture,
    SourceFetchRequest,
)
from northstar_quant.data.sources.publisher import (
    DataSourcePublisher,
    SourcePublicationSpec,
)
from northstar_quant.foundation.config.data_sources import (
    DataSourceConfig,
    DataSourceLicense,
    DataSourceSupport,
    ExchangeAuthorizationEvidence,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class _PassQualityEngine(DataQualityEngine):
    """仅供下游 PIT fixture 使用；真实质量规则在其专属测试中覆盖。"""

    def evaluate(self, request: QualityRequest) -> QualityEvaluation:
        evidence = QualityEvidence.from_mapping({"fixture": "authorized-pit-publication"})
        findings = tuple(
            QualityFinding(
                rule=rule,
                status=QualityStatus.PASS,
                reason_code="PIT_FIXTURE_RULE_CONFIRMED",
                summary="受控 PIT fixture 已由对应质量测试覆盖。",
                evidence=evidence,
            )
            for rule in QualityRule
        )
        return QualityEvaluation(
            artifact=request.artifact,
            checked_at=request.checked_at,
            decision_at=request.decision_at,
            findings=findings,
            critical_rules=request.critical_rules,
            policy_hash=request.policy_hash,
            frame_hash=request.frame_hash,
            evaluated_payload_hash=request.evaluated_payload_hash,
        )


class _PITAdapter:
    def __init__(
        self,
        *,
        adapter_id: str,
        frame: pl.DataFrame,
        raw_available_at: datetime,
        schema_version: str,
        transform_version: str,
    ) -> None:
        self._adapter_id = adapter_id
        self._frame = frame.clone()
        self._raw_available_at = raw_available_at.astimezone(UTC)
        self._schema_version = schema_version
        self._transform_version = transform_version

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    def metadata(self, scope: PublicationScope) -> AdapterMetadata:
        assert isinstance(scope, PublicationScope)
        return AdapterMetadata(
            adapter_id=self._adapter_id,
            implementation_version="pit-fixture-adapter.v1",
            raw_format="application/json",
            normalized_schema_version=self._schema_version,
            transform_version=self._transform_version,
            normalized_format=CANONICAL_NORMALIZED_FORMAT,
        )

    def fetch(self, request: SourceFetchRequest) -> RawCapture:
        return RawCapture(
            payload=f'{{"fixture":"pit","request":"{request.request_reference}"}}'.encode("utf-8"),
            raw_format="application/json",
            source_reference=request.request_reference,
            collection_method="fixture-import",
            available_at=self._raw_available_at,
            capture_quality_status=QualityStatus.PASS,
        )

    def normalize(self, raw_payload: bytes, *, metadata: AdapterMetadata) -> NormalizedTable:
        assert raw_payload
        assert metadata.adapter_id == self._adapter_id
        return NormalizedTable.from_frame(self._frame)


class _QualityRequestBuilder:
    def __init__(
        self,
        *,
        key_columns: tuple[str, ...],
        event_time_column: str,
        available_at_column: str,
        value_columns: tuple[str, ...],
    ) -> None:
        self._key_columns = key_columns
        self._event_time_column = event_time_column
        self._available_at_column = available_at_column
        self._value_columns = value_columns

    def build(
        self,
        *,
        candidate: NormalizedArtifact,
        normalized: NormalizedTable,
        checked_at: datetime,
        decision_at: datetime,
    ) -> QualityRequest:
        frame = normalized.frame
        group_by = tuple(
            column for column in self._key_columns if column != self._event_time_column
        )
        return QualityRequest(
            artifact=cast(Artifact, candidate),
            frame=frame,
            checked_at=checked_at,
            decision_at=decision_at,
            completeness=CompletenessRule(tuple(frame.columns), 1, 0.0),
            uniqueness=UniquenessRule((*self._key_columns, self._available_at_column)),
            ordering=OrderingRule(
                (self._event_time_column, self._available_at_column),
                group_by,
            ),
            schema=tuple(
                SchemaField(name, str(frame.schema[name]), False) for name in frame.columns
            ),
            expected_artifact_schema_version=candidate.schema_version,
            allow_additional_columns=False,
            ranges=(),
            staleness=StalenessRule(None, timedelta(hours=1)),
            gap=GapRule(
                self._event_time_column,
                timedelta(days=7),
                group_by,
                coverage_start=checked_at - timedelta(minutes=1),
                coverage_end=checked_at,
            ),
            revision=RevisionRule(
                (*self._key_columns, self._available_at_column),
                self._value_columns,
                QualityStatus.WARN,
                None,
            ),
            policy_id="pit-fixture-quality-policy",
            policy_version="v1",
            evaluated_payload=normalized.payload,
            critical_rules=frozenset(QualityRule),
        )


def _source_config(
    *,
    source_id: str,
    adapter_id: str,
    dataset_id: str,
    permitted_purposes: tuple[str, ...],
    authorized_exchanges: tuple[str, ...],
    authorized_products: tuple[str, ...],
    actual_contract_data: bool,
    frequency: str,
    requires_authoritative_dynamic_rules: bool,
) -> DataSourceConfig:
    return DataSourceConfig(
        source_id=source_id,
        adapter_id=adapter_id,
        name="PIT Authorized Fixture Source",
        tier="commercial_licensed",
        status="active",
        official_references=("https://example.test/pit-authorized-fixture",),
        supported=DataSourceSupport(
            markets=("CN",),
            asset_types=("FUTURES",),
            frequencies=(frequency,),
            actual_contract_data=actual_contract_data,
            authoritative_calendar=False,
            authoritative_dynamic_rules=requires_authoritative_dynamic_rules,
        ),
        license=DataSourceLicense(
            status="active",
            legal_entity="Northstar PIT Fixture Ltd",
            contract_ref="PIT-FIXTURE-CONTRACT-2026",
            order_form_ref="PIT-FIXTURE-ORDER-2026",
            effective_from="2026-01-01",
            expires_on="2026-12-31",
            last_verified_at="2026-01-01",
            verified_by="pit-fixture-reviewer",
            authorized_exchanges=authorized_exchanges,
            authorized_products=authorized_products,
            authorized_datasets=(dataset_id,),
            authorized_frequencies=(frequency,),
            authorized_environments=("internal_server",),
            permitted_purposes=permitted_purposes,
            prohibited_purposes=("live_trading",),
            allows_internal_storage=True,
            retention_days=3650,
            allows_derived_data_storage=True,
            allows_model_training=False,
            allows_redistribution=False,
            allows_public_display=False,
            allows_live_trading=False,
            credential_env_var=None,
            vendor_terms_url="https://example.test/pit-fixture-terms",
            contract_document_sha256=_hash("pit-fixture-contract"),
            exchange_authorization_evidence=tuple(
                ExchangeAuthorizationEvidence(
                    exchange=exchange,
                    evidence_ref=f"PIT-FIXTURE-{exchange}",
                    evidence_url=f"https://example.test/pit-fixture-{exchange.lower()}",
                    document_sha256=_hash(f"pit-fixture-{exchange}"),
                    verified_at="2026-01-01",
                )
                for exchange in authorized_exchanges
            ),
            request_rate_limit_per_minute=60,
        ),
    )


def publish_authorized_pit_dataset(
    root: Path,
    frame: pl.DataFrame,
    *,
    dataset_id: str,
    source_id: str,
    adapter_id: str,
    schema_version: str,
    artifact_id: str,
    key_columns: tuple[str, ...],
    event_time_column: str,
    available_at_column: str,
    value_columns: tuple[str, ...],
    normalized_available_at: datetime,
    store: ArtifactStore | None = None,
    purpose: PublicationPurpose = PublicationPurpose.HISTORICAL_BACKTEST,
    scope_exchanges: tuple[str, ...] = ("SHFE",),
    scope_products: tuple[str, ...] = ("RB",),
    actual_contract_data: bool = False,
    frequency: str = "1d",
    requires_authoritative_dynamic_rules: bool = False,
    transform_version: str = "normalize.pit-fixture.v1",
    dataset_transform_version: str | None = None,
) -> tuple[ArtifactStore, DatasetVersion]:
    """以受控发布器生成一个可被 PIT selector 消费的 DatasetVersion。"""

    if normalized_available_at.tzinfo is None or normalized_available_at.utcoffset() is None:
        raise ValueError("normalized_available_at 必须带时区")
    normalized_available_at = normalized_available_at.astimezone(UTC)
    if not isinstance(purpose, PublicationPurpose):
        raise ValueError("purpose 必须是 PublicationPurpose")
    if type(actual_contract_data) is not bool:
        raise ValueError("actual_contract_data 必须是 bool")
    if type(requires_authoritative_dynamic_rules) is not bool:
        raise ValueError("requires_authoritative_dynamic_rules 必须是 bool")
    frequency = str(frequency).strip().lower()
    transform_version = str(transform_version).strip()
    if not frequency or not transform_version:
        raise ValueError("frequency 和 transform_version 均不能为空")
    if dataset_transform_version is not None:
        dataset_transform_version = str(dataset_transform_version).strip()
        if not dataset_transform_version:
            raise ValueError("dataset_transform_version 不能为空")
    if not scope_exchanges or not scope_products:
        raise ValueError("scope_exchanges 和 scope_products 均必须显式提供")
    scope_exchanges = tuple(str(exchange).strip().upper() for exchange in scope_exchanges)
    scope_products = tuple(str(product).strip().upper() for product in scope_products)
    if any(not exchange for exchange in scope_exchanges) or any(
        not product for product in scope_products
    ):
        raise ValueError("scope_exchanges 和 scope_products 不能包含空值")
    if len(set(scope_exchanges)) != len(scope_exchanges) or len(set(scope_products)) != len(
        scope_products
    ):
        raise ValueError("scope_exchanges 和 scope_products 不能包含重复值")
    store = store or ArtifactStore(root / "artifacts")
    source_config = _source_config(
        source_id=source_id,
        adapter_id=adapter_id,
        dataset_id=dataset_id,
        permitted_purposes=(
            ("internal_research",)
            if purpose is PublicationPurpose.INTERNAL_RESEARCH
            else ("internal_research", purpose.value)
        ),
        authorized_exchanges=scope_exchanges,
        authorized_products=scope_products,
        actual_contract_data=actual_contract_data,
        frequency=frequency,
        requires_authoritative_dynamic_rules=requires_authoritative_dynamic_rules,
    )
    requested_at = normalized_available_at - timedelta(minutes=5)
    acquired_at = normalized_available_at - timedelta(minutes=4)
    raw_available_at = normalized_available_at - timedelta(minutes=3)
    checked_at = normalized_available_at - timedelta(minutes=1)
    adapter = _PITAdapter(
        adapter_id=adapter_id,
        frame=frame,
        raw_available_at=raw_available_at,
        schema_version=schema_version,
        transform_version=transform_version,
    )

    def source_config_loader(requested_source_id: str) -> DataSourceConfig:
        if requested_source_id != source_config.source_id:
            raise ValueError("未知 PIT fixture source")
        return source_config

    publisher = DataSourcePublisher(
        store=store,
        source_config_loader=source_config_loader,
        quality_engine=_PassQualityEngine(),
    )
    publication = publisher.publish(
        adapter=adapter,
        spec=SourcePublicationSpec(
            request=SourceFetchRequest(
                source_id=source_id,
                scope=PublicationScope(
                    dataset_id=dataset_id,
                    market="CN",
                    asset_type="FUTURES",
                    frequency=frequency,
                    purpose=purpose,
                    environment="internal_server",
                    exchanges=scope_exchanges,
                    products=scope_products,
                    actual_contract_data=actual_contract_data,
                    requires_authoritative_dynamic_rules=requires_authoritative_dynamic_rules,
                ),
                request_reference=f"fixture://pit/{artifact_id}",
                requested_at=requested_at,
            ),
            acquired_at=acquired_at,
            normalized_available_at=normalized_available_at,
            checked_at=checked_at,
            decision_at=checked_at,
            raw_artifact_id=f"{artifact_id}.raw",
            normalized_artifact_id=artifact_id,
            quality_request_builder=_QualityRequestBuilder(
                key_columns=key_columns,
                event_time_column=event_time_column,
                available_at_column=available_at_column,
                value_columns=value_columns,
            ),
            dataset_transform_version=(
                dataset_transform_version or f"dataset.{artifact_id}"
            ),
        ),
        released_at=normalized_available_at,
    )
    return store, publication.dataset.dataset_version
