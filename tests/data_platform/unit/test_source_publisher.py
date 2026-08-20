"""P1-WP06 受控数据源发布器的离线端到端单元测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
from typing import Callable

import polars as pl
import pytest

from northstar_quant.data_platform.artifacts.fingerprints import (
    content_sha256,
    normalization_identity_hash,
)
from northstar_quant.data_platform.artifacts.immutable_store import (
    ArtifactStore,
    ArtifactStoreError,
)
from northstar_quant.data_platform.contracts.data_domain import (
    ArtifactMetadata,
    ArtifactProvenance,
    NormalizedArtifact,
    QualityStatus,
    RawArtifact,
)
from northstar_quant.data_platform.quality import (
    CompletenessRule,
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
from northstar_quant.data_platform.sources.protocol import (
    AdapterMetadata,
    CANONICAL_NORMALIZED_FORMAT,
    NormalizedTable,
    PublicationPurpose,
    PublicationScope,
    RawCapture,
    SourceFetchRequest,
)
from northstar_quant.data_platform.sources.publisher import (
    DataSourcePublisher,
    DataSourcePublisherError,
    SourcePublicationSpec,
)
from northstar_quant.platform.config.data_sources import (
    DataSourceConfig,
    DataSourceLicense,
    DataSourceSupport,
    ExchangeAuthorizationEvidence,
    get_data_source,
)
from tests.helpers.paths import PROJECT_ROOT


REQUESTED_AT = datetime(2026, 8, 19, 9, tzinfo=UTC)
ACQUIRED_AT = REQUESTED_AT
RAW_AVAILABLE_AT = REQUESTED_AT + timedelta(minutes=5)
NORMALIZED_AVAILABLE_AT = REQUESTED_AT + timedelta(minutes=15)
QUALITY_AT = REQUESTED_AT + timedelta(minutes=10)
RELEASED_AT = NORMALIZED_AVAILABLE_AT + timedelta(minutes=1)
_RAW_PAYLOAD = b'{"fixture":"authorized-source-publisher"}'


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _license() -> DataSourceLicense:
    return DataSourceLicense(
        status="active",
        legal_entity="Northstar Fixture Ltd",
        contract_ref="FIXTURE-CONTRACT-2026",
        order_form_ref="FIXTURE-ORDER-2026",
        effective_from="2026-01-01",
        expires_on="2026-12-31",
        last_verified_at="2026-08-01",
        verified_by="fixture-reviewer",
        authorized_exchanges=("DCE", "SHFE"),
        authorized_products=("CU", "RB"),
        authorized_datasets=("actual_contract_daily",),
        authorized_frequencies=("1d",),
        authorized_environments=("internal_server",),
        permitted_purposes=("historical_backtest", "internal_research"),
        prohibited_purposes=("live_trading", "redistribution"),
        allows_internal_storage=True,
        retention_days=3650,
        allows_derived_data_storage=True,
        allows_model_training=False,
        allows_redistribution=False,
        allows_public_display=False,
        allows_live_trading=False,
        credential_env_var=None,
        vendor_terms_url="https://example.test/terms",
        contract_document_sha256="a" * 64,
        exchange_authorization_evidence=(
            ExchangeAuthorizationEvidence(
                exchange="DCE",
                evidence_ref="FIXTURE-DCE-EVIDENCE",
                evidence_url="https://example.test/dce-evidence",
                document_sha256="b" * 64,
                verified_at="2026-08-01",
            ),
            ExchangeAuthorizationEvidence(
                exchange="SHFE",
                evidence_ref="FIXTURE-SHFE-EVIDENCE",
                evidence_url="https://example.test/shfe-evidence",
                document_sha256="c" * 64,
                verified_at="2026-08-01",
            ),
        ),
        request_rate_limit_per_minute=60,
    )


def _source_config() -> DataSourceConfig:
    return DataSourceConfig(
        source_id="fixture_market_source_v1",
        adapter_id="fixture_adapter",
        name="Fixture licensed market source",
        tier="commercial_licensed",
        status="active",
        official_references=("https://example.test/catalog",),
        supported=DataSourceSupport(
            markets=("CN",),
            asset_types=("FUTURES",),
            frequencies=("1d",),
            actual_contract_data=True,
            authoritative_calendar=False,
            authoritative_dynamic_rules=False,
        ),
        license=_license(),
    )


def _scope() -> PublicationScope:
    return PublicationScope(
        dataset_id="actual_contract_daily",
        market="CN",
        asset_type="FUTURES",
        frequency="1d",
        purpose=PublicationPurpose.HISTORICAL_BACKTEST,
        environment="internal_server",
        exchanges=("DCE", "SHFE"),
        products=("CU", "RB"),
        actual_contract_data=True,
    )


def _metadata(*, adapter_id: str = "fixture_adapter") -> AdapterMetadata:
    return AdapterMetadata(
        adapter_id=adapter_id,
        implementation_version="fixture-adapter.v1",
        raw_format="application/json",
        normalized_schema_version="market.fixture.v1",
        transform_version="fixture-normalize.v1",
        normalized_format=CANONICAL_NORMALIZED_FORMAT,
    )


def _frame(*, price: float = 100.0) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["RB", "RB", "RB"],
            "timestamp": [
                REQUESTED_AT - timedelta(minutes=30),
                REQUESTED_AT - timedelta(minutes=15),
                REQUESTED_AT,
            ],
            "price": [price, price + 1.0, price + 2.0],
            "volume": [10, 11, 12],
        }
    )


def _prior_normalized(frame: pl.DataFrame) -> NormalizedArtifact:
    """构造已可见、同 identity 的离线 prior baseline。"""

    raw_payload = b"prior-fixture-raw"
    provenance = ArtifactProvenance(
        source_id="fixture_market_source_v1",
        source_reference="fixture://source-publisher/prior",
        collection_method="fixture-import",
    )
    raw = RawArtifact(
        metadata=ArtifactMetadata(
            artifact_id="prior-raw-v1",
            source_id="fixture_market_source_v1",
            acquired_at=REQUESTED_AT - timedelta(minutes=50),
            available_at=REQUESTED_AT - timedelta(minutes=45),
            schema_version="raw.fixture-adapter.v1",
            content_hash=content_sha256(raw_payload),
            transform_version="capture.fixture-adapter.v1",
            quality_status=QualityStatus.PASS,
            provenance=provenance,
        ),
        raw_format="application/json",
    )
    payload = canonical_frame_payload(frame)
    metadata = ArtifactMetadata(
        artifact_id="prior-normalized-v1",
        source_id="fixture_market_source_v1",
        acquired_at=REQUESTED_AT - timedelta(minutes=40),
        available_at=REQUESTED_AT - timedelta(minutes=35),
        schema_version="market.fixture.v1",
        content_hash=content_sha256(payload),
        transform_version="fixture-normalize.v1",
        quality_status=QualityStatus.PASS,
        provenance=provenance,
    )
    return NormalizedArtifact(
        metadata=metadata,
        raw_artifact=raw,
        normalization_identity=normalization_identity_hash(
            raw.content_hash,
            metadata.content_hash,
            metadata.transform_version,
            metadata.schema_version,
        ),
    )


def _reference(
    name: str,
    *,
    expected_observation: bool | None = None,
) -> QualityReferenceDecision:
    return QualityReferenceDecision(
        status=QualityStatus.PASS,
        reason_code="FIXTURE_REFERENCE_CONFIRMED",
        summary="离线 fixture 的可审计质量事实。",
        available_at=QUALITY_AT - timedelta(minutes=1),
        reference_hash=_hash(name),
        evidence=QualityEvidence.from_mapping({"fixture": name}),
        expected_observation=expected_observation,
    )


class _CalendarResolver:
    def assess_calendar_consistency(self, **_: object) -> QualityReferenceDecision:
        return _reference("calendar")


class _ContractResolver:
    def assess_contract_consistency(self, **_: object) -> QualityReferenceDecision:
        return _reference("contract")


class _CoverageResolver:
    def assess_expected_observation(self, **_: object) -> QualityReferenceDecision:
        return _reference("coverage", expected_observation=False)


class _QualityBuilder:
    def __init__(self, *, force_range_failure: bool = False) -> None:
        self._force_range_failure = force_range_failure

    def build(
        self,
        *,
        candidate: NormalizedArtifact,
        normalized: NormalizedTable,
        checked_at: datetime,
        decision_at: datetime,
    ) -> QualityRequest:
        frame = normalized.frame
        prior = _prior_normalized(frame)
        return QualityRequest(
            artifact=candidate,
            frame=frame,
            checked_at=checked_at,
            decision_at=decision_at,
            completeness=CompletenessRule(("symbol", "timestamp", "price", "volume"), 3, 0.0),
            uniqueness=UniquenessRule(("symbol", "timestamp")),
            ordering=OrderingRule(("timestamp",), ("symbol",)),
            schema=tuple(
                SchemaField(name, str(frame.schema[name]), False) for name in frame.columns
            ),
            expected_artifact_schema_version=candidate.schema_version,
            allow_additional_columns=False,
            ranges=(
                RangeRule("price", 1.0, 99.0 if self._force_range_failure else 200.0),
            ),
            staleness=StalenessRule(timedelta(minutes=30), timedelta(hours=2)),
            gap=GapRule(
                "timestamp",
                timedelta(minutes=15),
                ("symbol",),
                coverage_start=REQUESTED_AT - timedelta(minutes=30),
                coverage_end=REQUESTED_AT,
            ),
            revision=RevisionRule(
                ("symbol", "timestamp"),
                ("price", "volume"),
                QualityStatus.WARN,
                RevisionBaseline.from_frame(
                    artifact=prior,
                    frame=frame,
                    key_columns=("symbol", "timestamp"),
                    content_columns=("price", "volume"),
                ),
            ),
            policy_id="fixture-source-publisher-quality",
            policy_version="v1",
            evaluated_payload=normalized.payload,
            calendar_resolver=_CalendarResolver(),
            contract_resolver=_ContractResolver(),
            calendar_coverage_resolver=_CoverageResolver(),
            calendar_resolver_identity="fixture-calendar-v1",
            contract_resolver_identity="fixture-contract-v1",
            calendar_coverage_resolver_identity="fixture-coverage-v1",
            critical_rules=frozenset(QualityRule),
        )


class _FixtureAdapter:
    def __init__(
        self,
        *,
        adapter_id: str = "fixture_adapter",
        non_deterministic: bool = False,
        on_fetch: Callable[[], None] | None = None,
    ) -> None:
        self._adapter_id = adapter_id
        self._non_deterministic = non_deterministic
        self._on_fetch = on_fetch
        self.fetch_calls = 0
        self.normalize_calls = 0

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    def metadata(self, scope: PublicationScope) -> AdapterMetadata:
        assert isinstance(scope, PublicationScope)
        return _metadata(adapter_id=self.adapter_id)

    def fetch(self, request: SourceFetchRequest) -> RawCapture:
        self.fetch_calls += 1
        if self._on_fetch is not None:
            self._on_fetch()
        return RawCapture(
            payload=_RAW_PAYLOAD,
            raw_format="application/json",
            source_reference=request.request_reference,
            collection_method="fixture-import",
            available_at=RAW_AVAILABLE_AT,
            capture_quality_status=QualityStatus.PASS,
            provenance_attributes=(("batch", "fixture-20260819"),),
        )

    def normalize(
        self,
        raw_payload: bytes,
        *,
        metadata: AdapterMetadata,
    ) -> NormalizedTable:
        assert raw_payload == _RAW_PAYLOAD
        assert metadata.adapter_id == self.adapter_id
        self.normalize_calls += 1
        if self._non_deterministic and self.normalize_calls % 2 == 0:
            return NormalizedTable.from_frame(_frame(price=200.0))
        return NormalizedTable.from_frame(_frame())


def _spec(
    *,
    source_id: str = "fixture_market_source_v1",
    scope: PublicationScope | None = None,
    quality_builder: _QualityBuilder | None = None,
) -> SourcePublicationSpec:
    return SourcePublicationSpec(
        request=SourceFetchRequest(
            source_id=source_id,
            scope=scope or _scope(),
            request_reference="fixture://source-publisher/20260819",
            requested_at=REQUESTED_AT,
            request_parameters=(("date", "2026-08-19"),),
        ),
        acquired_at=ACQUIRED_AT,
        normalized_available_at=NORMALIZED_AVAILABLE_AT,
        checked_at=QUALITY_AT,
        decision_at=QUALITY_AT,
        raw_artifact_id="fixture-raw-v1",
        normalized_artifact_id="fixture-normalized-v1",
        quality_request_builder=quality_builder or _QualityBuilder(),
        dataset_transform_version="fixture-dataset.v1",
    )


def _snapshot_records(store: ArtifactStore) -> list[Path]:
    return list((store.root / "snapshots" / "sha256").rglob("*.json"))


def _dataset_manifests(store: ArtifactStore) -> list[Path]:
    return list((store.root / "datasets" / "sha256").rglob("*.json"))


def test_publisher_persists_authorized_raw_normalized_quality_and_replay(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    adapter = _FixtureAdapter()
    publisher = DataSourcePublisher(
        store=store,
        source_config_loader=lambda source_id: _source_config(),
    )

    published = publisher.publish(
        adapter=adapter,
        spec=_spec(),
        released_at=RELEASED_AT,
    )

    assessed = published.assessed
    assert adapter.fetch_calls == 1
    assert adapter.normalize_calls == 2
    assert all(result.quality_status is QualityStatus.PASS for result in assessed.quality_results)
    assert assessed.normalized.quality_assessment_hash == assessed.quality_assessment.assessment.assessment_hash
    assert (
        assessed.raw.publication_authorization_hash
        == assessed.authorization.authorization_hash
        == assessed.normalized.publication_authorization_hash
    )
    assert dict(assessed.raw_artifact.provenance.attributes)[
        "publication_receipt_hash"
    ] == assessed.authorization.authorization_hash
    assert (
        store.load_quality_assessment(assessed.normalized.snapshot.snapshot_hash).assessment
        == assessed.quality_assessment.assessment
    )
    assert (
        store.load_publication_authorization(assessed.authorization.authorization_hash).authorization
        == assessed.authorization.as_mapping()
    )
    assert published.dataset.quality_assessment_hashes == (
        assessed.quality_assessment.assessment.assessment_hash,
    )

    replay = store.replay_dataset_version(published.dataset.dataset_version.version_hash)

    assert replay.dataset_version == published.dataset.dataset_version
    assert len(replay.artifacts) == 1
    assert replay.artifacts[0].stored.snapshot == assessed.normalized.snapshot
    assert replay.artifacts[0].payload == NormalizedTable.from_frame(_frame()).payload


def test_unlicensed_real_source_is_rejected_before_adapter_fetch(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    adapter = _FixtureAdapter(adapter_id="akshare")
    publisher = DataSourcePublisher(
        store=store,
        source_config_loader=lambda source_id: get_data_source(
            source_id,
            path=PROJECT_ROOT / "configs" / "data" / "sources.yaml",
        ),
    )
    scope = PublicationScope(
        dataset_id="unlicensed_fixture_dataset",
        market="CN",
        asset_type="FUTURES",
        frequency="1d",
        purpose=PublicationPurpose.HISTORICAL_BACKTEST,
        environment="internal_server",
    )

    with pytest.raises(DataSourcePublisherError, match="授权预检失败"):
        publisher.capture_and_assess(
            adapter=adapter,
            spec=_spec(source_id="akshare_continuous_public_v1", scope=scope),
        )

    assert adapter.fetch_calls == 0
    assert _snapshot_records(store) == []


def test_revocation_after_fetch_does_not_write_raw_or_dataset(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    current_config = [_source_config()]

    def revoke_source() -> None:
        current_config[0] = replace(current_config[0], status="research_only")

    adapter = _FixtureAdapter(on_fetch=revoke_source)
    publisher = DataSourcePublisher(
        store=store,
        source_config_loader=lambda source_id: current_config[0],
    )

    with pytest.raises(DataSourcePublisherError, match="授权已失效"):
        publisher.capture_and_assess(adapter=adapter, spec=_spec())

    assert adapter.fetch_calls == 1
    assert adapter.normalize_calls == 0
    assert _snapshot_records(store) == []
    assert _dataset_manifests(store) == []


def test_non_deterministic_normalize_keeps_raw_audit_evidence_but_never_dataset(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    adapter = _FixtureAdapter(non_deterministic=True)
    publisher = DataSourcePublisher(
        store=store,
        source_config_loader=lambda source_id: _source_config(),
    )

    with pytest.raises(DataSourcePublisherError, match="确定性"):
        publisher.capture_and_assess(adapter=adapter, spec=_spec())

    assert adapter.fetch_calls == 1
    assert adapter.normalize_calls == 2
    assert len(_snapshot_records(store)) == 1
    assert _dataset_manifests(store) == []


def test_failed_quality_assessment_is_retained_but_cannot_release_dataset(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    publisher = DataSourcePublisher(
        store=store,
        source_config_loader=lambda source_id: _source_config(),
    )

    assessed = publisher.capture_and_assess(
        adapter=_FixtureAdapter(),
        spec=_spec(quality_builder=_QualityBuilder(force_range_failure=True)),
    )

    assert assessed.quality_evaluation.aggregate_status is QualityStatus.FAIL
    assert assessed.quality_assessment.assessment.aggregate_status is QualityStatus.FAIL
    assert assessed.normalized.quality_assessment_hash is not None
    with pytest.raises(DataSourcePublisherError, match="质量门禁拒绝"):
        publisher.publish_dataset(assessed, released_at=RELEASED_AT)

    assert _dataset_manifests(store) == []


def test_publisher_rejects_impossible_publication_times_before_fetch(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    publisher = DataSourcePublisher(
        store=store,
        source_config_loader=lambda source_id: _source_config(),
    )

    with pytest.raises(DataSourcePublisherError, match="decision_at 不能早于质量 checked_at"):
        replace(_spec(), decision_at=QUALITY_AT - timedelta(seconds=1))

    class _LateRawAdapter(_FixtureAdapter):
        def fetch(self, request: SourceFetchRequest) -> RawCapture:
            return RawCapture(
                payload=_RAW_PAYLOAD,
                raw_format="application/json",
                source_reference=request.request_reference,
                collection_method="fixture-import",
                available_at=QUALITY_AT + timedelta(seconds=1),
                capture_quality_status=QualityStatus.PASS,
            )

    with pytest.raises(DataSourcePublisherError, match="不能早于 raw 制品可用时间"):
        publisher.capture_and_assess(adapter=_LateRawAdapter(), spec=_spec())
    assert _snapshot_records(store) == []


def test_tampered_quality_or_authorization_receipt_makes_replay_fail_closed(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    publisher = DataSourcePublisher(
        store=store,
        source_config_loader=lambda source_id: _source_config(),
    )
    published = publisher.publish(
        adapter=_FixtureAdapter(),
        spec=_spec(),
        released_at=RELEASED_AT,
    )
    assessed = published.assessed

    authorization_path = store.publication_authorization_path(
        assessed.authorization.authorization_hash
    )
    authorization_path.chmod(0o600)
    authorization_path.write_bytes(b'{"forged":true}\n')
    with pytest.raises(ArtifactStoreError):
        store.load_artifact(assessed.raw.snapshot.snapshot_hash)

    # 用独立 root 避免前一个篡改遮蔽 assessment relation 的校验路径。
    other_store = ArtifactStore(tmp_path / "other-artifacts")
    other_publisher = DataSourcePublisher(
        store=other_store,
        source_config_loader=lambda source_id: _source_config(),
    )
    other_published = other_publisher.publish(
        adapter=_FixtureAdapter(),
        spec=_spec(),
        released_at=RELEASED_AT,
    )
    assessment_path = other_store.quality_assessment_path(
        other_published.assessed.quality_assessment.assessment.assessment_hash
    )
    assessment_path.chmod(0o600)
    assessment_path.write_bytes(b'{"forged":true}\n')
    with pytest.raises(ArtifactStoreError):
        other_store.replay_dataset_version(other_published.dataset.dataset_version.version_hash)
