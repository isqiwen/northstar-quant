"""P1-WP08：离线受控来源到研究消费者的 Data Platform 闭环验收。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import socket

import polars as pl

import northstar_quant.application.backtest as backtest_app
from northstar_quant.data.artifacts.fingerprints import content_sha256
from northstar_quant.data.artifacts.immutable_store import ArtifactStore
from northstar_quant.data.contracts.data_domain import (
    ArtifactKind,
    NormalizedArtifact,
    QualityStatus,
)
from northstar_quant.data.contracts.instrument_universes import load_instrument_universe
from northstar_quant.data.market.pit import (
    MarketDataKind,
    MarketDataPITSelector,
    MarketDataPITSpec,
)
from northstar_quant.data.quality import (
    CompletenessRule,
    DataQualityEngine,
    GapRule,
    OrderingRule,
    QualityEvidence,
    QualityReferenceDecision,
    QualityRequest,
    QualityRule,
    RangeRule,
    RevisionBaseline,
    RevisionRule,
    SchemaField,
    StalenessRule,
    UniquenessRule,
    canonical_frame_payload,
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
from northstar_quant.foundation.config.trading_profile import load_trading_profile


_AS_OF_BASE = datetime(2026, 1, 5, 16, tzinfo=UTC)
_RAW_AVAILABLE_AT = _AS_OF_BASE + timedelta(minutes=1)
_NORMALIZED_AVAILABLE_AT = _AS_OF_BASE + timedelta(minutes=4)
_RAW_FORMAT = "application/vnd.northstar.wp08+json"
_RAW_PAYLOAD_FORMAT = "northstar.wp08.raw-market.v1"
_FRAME_COLUMNS = (
    "date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "adjusted_close",
    "volume",
    "available_at",
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _offline_full_profile_frame(symbols: tuple[str, ...]) -> pl.DataFrame:
    """构造足以驱动真实趋势回测的确定性、无网络行情输入。"""

    start = date(2024, 1, 2)
    rows: list[dict[str, object]] = []
    for day_offset in range(70):
        trading_day = start + timedelta(days=day_offset)
        for symbol_offset, symbol in enumerate(symbols):
            close = 100.0 + day_offset + symbol_offset * 10.0
            rows.append(
                {
                    "date": trading_day,
                    "symbol": symbol,
                    "open": close - 1.0,
                    "high": close + 1.0,
                    "low": close - 2.0,
                    "close": close,
                    "adjusted_close": close,
                    "volume": 1_000.0 + day_offset,
                    "available_at": _RAW_AVAILABLE_AT,
                }
            )
    return pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(pl.Datetime("us", "UTC"))
    )


def _raw_payload_from_frame(frame: pl.DataFrame) -> bytes:
    """把测试行情编码为 adapter 必须重新解析的 raw bytes。"""

    if tuple(frame.columns) != _FRAME_COLUMNS:
        raise AssertionError("P1-WP08 fixture 的 normalized schema 发生意外变化")
    rows: list[dict[str, object]] = []
    for row in frame.iter_rows(named=True):
        trading_day = row["date"]
        available_at = row["available_at"]
        if not isinstance(trading_day, date) or not isinstance(available_at, datetime):
            raise AssertionError("P1-WP08 fixture 的日期类型不符合预期")
        rows.append(
            {
                "date": trading_day.isoformat(),
                "symbol": str(row["symbol"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "adjusted_close": float(row["adjusted_close"]),
                "volume": float(row["volume"]),
                "available_at": available_at.astimezone(UTC).isoformat(),
            }
        )
    return json.dumps(
        {
            "format": _RAW_PAYLOAD_FORMAT,
            "normalized_frame_sha256": content_sha256(canonical_frame_payload(frame)),
            "rows": rows,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class _OfflineRawAdapter:
    """只从 raw bytes 解析 normalized 表，禁止借用调用方预置的 DataFrame。"""

    def __init__(
        self,
        *,
        adapter_id: str,
        raw_payload: bytes,
        raw_available_at: datetime,
        schema_version: str,
    ) -> None:
        self._adapter_id = adapter_id
        self._raw_payload = raw_payload
        self._raw_available_at = raw_available_at.astimezone(UTC)
        self._schema_version = schema_version
        self.normalized_raw_hashes: list[str] = []

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    def metadata(self, scope: PublicationScope) -> AdapterMetadata:
        assert isinstance(scope, PublicationScope)
        return AdapterMetadata(
            adapter_id=self._adapter_id,
            implementation_version="wp08-offline-adapter.v1",
            raw_format=_RAW_FORMAT,
            normalized_schema_version=self._schema_version,
            transform_version="normalize.wp08-offline.v1",
            normalized_format=CANONICAL_NORMALIZED_FORMAT,
        )

    def fetch(self, request: SourceFetchRequest) -> RawCapture:
        return RawCapture(
            payload=self._raw_payload,
            raw_format=_RAW_FORMAT,
            source_reference=request.request_reference,
            collection_method="fixture-import",
            available_at=self._raw_available_at,
            capture_quality_status=QualityStatus.PASS,
        )

    def normalize(self, raw_payload: bytes, *, metadata: AdapterMetadata) -> NormalizedTable:
        if metadata.adapter_id != self._adapter_id:
            raise ValueError("adapter metadata 不匹配")
        self.normalized_raw_hashes.append(content_sha256(raw_payload))
        try:
            document = json.loads(raw_payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("raw fixture 不是合法 JSON") from exc
        if not isinstance(document, dict) or set(document) != {
            "format",
            "normalized_frame_sha256",
            "rows",
        }:
            raise ValueError("raw fixture 字段不完整或包含未知字段")
        if document["format"] != _RAW_PAYLOAD_FORMAT:
            raise ValueError("raw fixture 格式不受支持")
        expected_normalized_hash = document["normalized_frame_sha256"]
        raw_rows = document["rows"]
        if not isinstance(expected_normalized_hash, str) or not isinstance(raw_rows, list):
            raise ValueError("raw fixture 的规范化哈希或 rows 无效")

        rows: list[dict[str, object]] = []
        for index, raw_row in enumerate(raw_rows):
            if not isinstance(raw_row, dict) or set(raw_row) != set(_FRAME_COLUMNS):
                raise ValueError(f"raw fixture 第 {index} 行 schema 无效")
            raw_date = raw_row["date"]
            raw_available_at = raw_row["available_at"]
            if not isinstance(raw_date, str) or not isinstance(raw_available_at, str):
                raise ValueError(f"raw fixture 第 {index} 行时间字段无效")
            parsed_available_at = datetime.fromisoformat(raw_available_at)
            if parsed_available_at.tzinfo is None or parsed_available_at.utcoffset() is None:
                raise ValueError(f"raw fixture 第 {index} 行 available_at 缺少时区")
            numeric_values: dict[str, float] = {}
            for column in ("open", "high", "low", "close", "adjusted_close", "volume"):
                value = raw_row[column]
                if type(value) not in {int, float}:
                    raise ValueError(f"raw fixture 第 {index} 行 {column} 必须是数值")
                numeric_values[column] = float(value)
            symbol = raw_row["symbol"]
            if not isinstance(symbol, str) or not symbol:
                raise ValueError(f"raw fixture 第 {index} 行 symbol 无效")
            rows.append(
                {
                    "date": date.fromisoformat(raw_date),
                    "symbol": symbol,
                    **numeric_values,
                    "available_at": parsed_available_at.astimezone(UTC),
                }
            )

        frame = pl.DataFrame(rows).select(_FRAME_COLUMNS).with_columns(
            pl.col("date").cast(pl.Date),
            pl.col("symbol").cast(pl.String),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("adjusted_close").cast(pl.Float64),
            pl.col("volume").cast(pl.Float64),
            pl.col("available_at").cast(pl.Datetime("us", "UTC")),
        )
        actual_normalized_hash = content_sha256(canonical_frame_payload(frame))
        if actual_normalized_hash != expected_normalized_hash:
            raise ValueError("raw fixture 与声明的 canonical normalized payload 不一致")
        return NormalizedTable.from_frame(frame)


class _OfflineQualityReferences:
    """测试专用、显式可见的 immutable reference 决策。"""

    def __init__(self, *, available_at: datetime) -> None:
        self._available_at = available_at.astimezone(UTC)

    def _decision(
        self,
        *,
        kind: str,
        expected_observation: bool | None = None,
    ) -> QualityReferenceDecision:
        return QualityReferenceDecision(
            status=QualityStatus.PASS,
            reason_code="WP08_REFERENCE_CONFIRMED",
            summary="离线 E2E 的固定引用事实已在当前 PIT 可见。",
            available_at=self._available_at,
            reference_hash=_hash(f"wp08-reference:{kind}:{self._available_at.isoformat()}"),
            evidence=QualityEvidence.from_mapping(
                {"fixture": "wp08", "reference_kind": kind}
            ),
            expected_observation=expected_observation,
        )

    def assess_calendar_consistency(self, **_kwargs: object) -> QualityReferenceDecision:
        return self._decision(kind="calendar")

    def assess_contract_consistency(self, **_kwargs: object) -> QualityReferenceDecision:
        return self._decision(kind="contract")

    def assess_expected_observation(self, **_kwargs: object) -> QualityReferenceDecision:
        return self._decision(kind="coverage", expected_observation=True)


class _RealQualityRequestBuilder:
    """为真实 DataQualityEngine 构造十项均可审计的离线请求。"""

    def __init__(self, *, schema_version: str) -> None:
        self._schema_version = schema_version

    def build(
        self,
        *,
        candidate: NormalizedArtifact,
        normalized: NormalizedTable,
        checked_at: datetime,
        decision_at: datetime,
    ) -> QualityRequest:
        frame = normalized.frame
        # revision 不能把缺少 baseline 伪装成 PASS；这里明确构造一个可见、同内容的 prior
        # immutable artifact snapshot，证明真实引擎会执行 revision 规则而非跳过它。
        prior = replace(
            candidate,
            metadata=replace(
                candidate.metadata,
                artifact_id=f"{candidate.artifact_id}.revision-baseline",
                available_at=checked_at - timedelta(seconds=1),
                quality_status=QualityStatus.PASS,
            ),
        )
        baseline = RevisionBaseline.from_frame(
            artifact=prior,
            frame=frame,
            key_columns=("date", "symbol"),
            content_columns=("open", "high", "low", "close", "adjusted_close", "volume"),
        )
        references = _OfflineQualityReferences(
            available_at=checked_at - timedelta(seconds=30)
        )
        return QualityRequest(
            artifact=candidate,
            frame=frame,
            checked_at=checked_at,
            decision_at=decision_at,
            completeness=CompletenessRule(tuple(frame.columns), 1, 0.0),
            uniqueness=UniquenessRule(("date", "symbol")),
            ordering=OrderingRule(("date",), ("symbol",)),
            schema=tuple(
                SchemaField(name, str(frame.schema[name]), False) for name in frame.columns
            ),
            expected_artifact_schema_version=self._schema_version,
            allow_additional_columns=False,
            ranges=(RangeRule("low", 0.0, None), RangeRule("volume", 0.0, None)),
            staleness=StalenessRule(None, timedelta(hours=1)),
            gap=GapRule(
                "available_at",
                timedelta(hours=1),
                ("symbol",),
                coverage_start=_RAW_AVAILABLE_AT,
                coverage_end=checked_at,
            ),
            revision=RevisionRule(
                ("date", "symbol"),
                ("open", "high", "low", "close", "adjusted_close", "volume"),
                QualityStatus.WARN,
                baseline,
            ),
            policy_id="wp08-real-quality-policy",
            policy_version="v1",
            evaluated_payload=normalized.payload,
            calendar_resolver=references,
            contract_resolver=references,
            calendar_coverage_resolver=references,
            calendar_resolver_identity="wp08-calendar-reference-v1",
            contract_resolver_identity="wp08-contract-reference-v1",
            calendar_coverage_resolver_identity="wp08-coverage-reference-v1",
            critical_rules=frozenset(QualityRule),
        )


def _authorized_source_config(
    *,
    source_id: str,
    adapter_id: str,
    dataset_id: str,
    exchanges: tuple[str, ...],
    products: tuple[str, ...],
) -> DataSourceConfig:
    return DataSourceConfig(
        source_id=source_id,
        adapter_id=adapter_id,
        name="P1-WP08 Offline Licensed Fixture Source",
        tier="commercial_licensed",
        status="active",
        official_references=("https://example.test/wp08-offline-source",),
        supported=DataSourceSupport(
            markets=("CN",),
            asset_types=("FUTURES",),
            frequencies=("1d",),
            actual_contract_data=False,
            authoritative_calendar=False,
            authoritative_dynamic_rules=False,
        ),
        license=DataSourceLicense(
            status="active",
            legal_entity="Northstar WP08 Fixture Ltd",
            contract_ref="WP08-OFFLINE-CONTRACT",
            order_form_ref="WP08-OFFLINE-ORDER",
            effective_from="2020-01-01",
            expires_on="2099-12-31",
            last_verified_at="2026-01-01",
            verified_by="wp08-fixture-reviewer",
            authorized_exchanges=exchanges,
            authorized_products=products,
            authorized_datasets=(dataset_id,),
            authorized_frequencies=("1d",),
            authorized_environments=("internal_server",),
            permitted_purposes=("internal_research", "historical_backtest"),
            prohibited_purposes=("live_trading",),
            allows_internal_storage=True,
            retention_days=3650,
            allows_derived_data_storage=True,
            allows_model_training=False,
            allows_redistribution=False,
            allows_public_display=False,
            allows_live_trading=False,
            credential_env_var=None,
            vendor_terms_url="https://example.test/wp08-terms",
            contract_document_sha256=_hash("wp08-offline-contract"),
            exchange_authorization_evidence=tuple(
                ExchangeAuthorizationEvidence(
                    exchange=exchange,
                    evidence_ref=f"WP08-{exchange}",
                    evidence_url=f"https://example.test/wp08-{exchange.lower()}",
                    document_sha256=_hash(f"wp08-{exchange}"),
                    verified_at="2026-01-01",
                )
                for exchange in exchanges
            ),
            request_rate_limit_per_minute=60,
        ),
    )


def _pit_spec() -> MarketDataPITSpec:
    return MarketDataPITSpec(
        kind=MarketDataKind.BAR,
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("open", "high", "low", "close", "adjusted_close", "volume"),
        schema_version="market_data_v2",
    )


def test_offline_controlled_source_reaches_research_consumer_with_immutable_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Source → Raw → Normalize → Validate → DatasetVersion → Research Consumer。"""

    def reject_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("P1-WP08 离线 fixture 不得建立网络连接")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    profile = load_trading_profile("cn_futures_daily_trend_offline")
    universe = load_instrument_universe(profile.universe_id)
    exchanges = tuple(sorted({member.exchange for member in universe.members}))
    products = tuple(member.product for member in universe.members)
    frame = _offline_full_profile_frame(tuple(profile.data.download.symbols))
    raw_payload = _raw_payload_from_frame(frame)
    source_config = _authorized_source_config(
        source_id=profile.data.source_id,
        adapter_id=profile.data.provider,
        dataset_id=profile.data.dataset_id,
        exchanges=exchanges,
        products=products,
    )
    store = ArtifactStore(tmp_path / "artifacts")
    adapter = _OfflineRawAdapter(
        adapter_id=profile.data.provider,
        raw_payload=raw_payload,
        raw_available_at=_RAW_AVAILABLE_AT,
        schema_version="market_data_v2",
    )

    def source_config_loader(source_id: str) -> DataSourceConfig:
        if source_id != source_config.source_id:
            raise ValueError("未知 P1-WP08 fixture source")
        return source_config

    publisher = DataSourcePublisher(
        store=store,
        source_config_loader=source_config_loader,
        quality_engine=DataQualityEngine(),
    )
    publication = publisher.publish(
        adapter=adapter,
        spec=SourcePublicationSpec(
            request=SourceFetchRequest(
                source_id=profile.data.source_id,
                scope=PublicationScope(
                    dataset_id=profile.data.dataset_id,
                    market="CN",
                    asset_type="FUTURES",
                    frequency="1d",
                    purpose=PublicationPurpose.HISTORICAL_BACKTEST,
                    environment="internal_server",
                    exchanges=exchanges,
                    products=products,
                ),
                request_reference="fixture://wp08/offline-controlled-source",
                requested_at=_NORMALIZED_AVAILABLE_AT - timedelta(minutes=5),
            ),
            acquired_at=_NORMALIZED_AVAILABLE_AT - timedelta(minutes=4),
            normalized_available_at=_NORMALIZED_AVAILABLE_AT,
            checked_at=_NORMALIZED_AVAILABLE_AT - timedelta(minutes=1),
            decision_at=_NORMALIZED_AVAILABLE_AT - timedelta(minutes=1),
            raw_artifact_id="wp08-offline-raw",
            normalized_artifact_id="wp08-offline-normalized",
            quality_request_builder=_RealQualityRequestBuilder(schema_version="market_data_v2"),
            dataset_transform_version="dataset.wp08-offline.v1",
        ),
        released_at=_NORMALIZED_AVAILABLE_AT,
    )
    dataset = publication.dataset.dataset_version

    # DatasetVersion 回放会递归检查 normalized、raw、lineage、quality 和授权收据。
    replay = store.replay_dataset_version(dataset.version_hash)
    assert replay.dataset_version == dataset
    assert len(replay.artifacts) == 1
    normalized = replay.artifacts[0].stored
    assert normalized.snapshot.kind is ArtifactKind.NORMALIZED
    assert normalized.snapshot.quality_status is QualityStatus.PASS
    assert normalized.quality_assessment_hash is not None
    assert normalized.publication_authorization_hash is not None
    assert len(normalized.parent_snapshot_hashes) == 1
    raw = store.load_artifact(normalized.parent_snapshot_hashes[0])
    assert raw.snapshot.kind is ArtifactKind.RAW
    assert store.read_payload(raw.snapshot.snapshot_hash) == raw_payload
    raw_document = json.loads(raw_payload)
    assert normalized.snapshot.content_hash == raw_document["normalized_frame_sha256"]
    assert adapter.normalized_raw_hashes == [content_sha256(raw_payload)] * 2

    assessment = store.load_quality_assessment(normalized.snapshot.snapshot_hash).assessment
    assert assessment.aggregate_status is QualityStatus.PASS
    assert {finding.status for finding in assessment.findings} == {QualityStatus.PASS}
    authorization = store.load_publication_authorization(
        normalized.publication_authorization_hash
    ).authorization
    assert authorization["scope"]["purpose"] == "historical_backtest"

    selector = MarketDataPITSelector(store)
    snapshot = selector.select(
        dataset_version_hash=dataset.version_hash,
        spec=_pit_spec(),
        as_of=_NORMALIZED_AVAILABLE_AT + timedelta(minutes=1),
    )
    run = backtest_app.run_profile_backtest_from_pit_snapshot(
        profile.profile_id,
        market_snapshot=snapshot,
        pit_selector=selector,
    )

    data_manifest = run.manifest_mapping()["data"]
    assert isinstance(data_manifest, dict)
    point_in_time = data_manifest["point_in_time"]
    assert isinstance(point_in_time, dict)
    assert point_in_time["dataset_version_hash"] == dataset.version_hash
    assert point_in_time["snapshot_id"] == snapshot.snapshot_id
    assert point_in_time["publication_authorization_hash"] == normalized.publication_authorization_hash
    assert point_in_time["publication_scope"]["purpose"] == "historical_backtest"
    # 单一静态 as-of 视图可复现，但不会被准入逻辑误报为逐决策无前视回放。
    assert point_in_time["decision_time_safe"] is False
    assert point_in_time["selection_mode"] == "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY"
