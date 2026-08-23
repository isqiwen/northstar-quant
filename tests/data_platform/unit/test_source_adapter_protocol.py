"""P1-WP06 数据源协议值对象与发布授权的单元测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib

import polars as pl
import pytest

from northstar_quant.data_platform.contracts.data_domain import DataSource, QualityStatus
from northstar_quant.data_platform.sources import (
    AdapterMetadata,
    CANONICAL_NORMALIZED_FORMAT,
    DataSourceAdapter,
    DataSourceProtocolError,
    NormalizedTable,
    PublicationPurpose,
    PublicationScope,
    RawCapture,
    SourceFetchRequest,
    build_publication_authorization,
    validate_publication_authorization,
)
from northstar_quant.platform.config.data_sources import (
    DataSourceConfig,
    DataSourceLicense,
    DataSourceSupport,
    ExchangeAuthorizationEvidence,
)


AUTHORIZED_AT = datetime(2026, 8, 19, 9, 30, tzinfo=UTC)


def _license(
    *,
    permitted_purposes: tuple[str, ...] = ("historical_backtest", "internal_research"),
    prohibited_purposes: tuple[str, ...] = ("live_trading", "redistribution"),
    allows_live_trading: bool = False,
) -> DataSourceLicense:
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
        permitted_purposes=permitted_purposes,
        prohibited_purposes=prohibited_purposes,
        allows_internal_storage=True,
        retention_days=3650,
        allows_derived_data_storage=True,
        allows_model_training=False,
        allows_redistribution=False,
        allows_public_display=False,
        allows_live_trading=allows_live_trading,
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


def _source_config(*, license: DataSourceLicense | None = None) -> DataSourceConfig:
    return DataSourceConfig(
        source_id="fixture_market_source_v1",
        adapter_id="fixture_adapter",
        name="Fixture market source",
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
        license=license or _license(),
    )


def _scope(
    *,
    purpose: PublicationPurpose = PublicationPurpose.HISTORICAL_BACKTEST,
) -> PublicationScope:
    return PublicationScope(
        dataset_id="actual_contract_daily",
        market="cn",
        asset_type="futures",
        frequency="1D",
        purpose=purpose,
        environment="INTERNAL_SERVER",
        exchanges=("shfe", "dce"),
        products=("rb", "cu"),
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


def test_scope_and_metadata_are_canonical_and_authorization_freezes_source_config() -> None:
    scope = _scope()
    metadata = _metadata()
    authorization = build_publication_authorization(
        _source_config(),
        scope,
        metadata,
        authorized_at=AUTHORIZED_AT,
    )

    assert scope.market == "CN"
    assert scope.asset_type == "FUTURES"
    assert scope.frequency == "1d"
    assert scope.environment == "internal_server"
    assert scope.exchanges == ("DCE", "SHFE")
    assert scope.products == ("CU", "RB")
    assert authorization.source == DataSource.from_config(_source_config())
    assert authorization.adapter_metadata == metadata
    assert authorization.authorization_hash == build_publication_authorization(
        _source_config(),
        scope,
        metadata,
        authorized_at=AUTHORIZED_AT,
    ).authorization_hash
    assert (
        validate_publication_authorization(
            authorization,
            _source_config(),
            metadata,
            authorized_at=AUTHORIZED_AT,
        )
        is authorization
    )


def test_build_rejects_non_active_or_unlicensed_source_before_any_fetch() -> None:
    inactive = replace(_source_config(), status="research_only")
    expired = replace(
        _source_config(),
        license=replace(_license(), expires_on="2026-08-18"),
    )

    with pytest.raises(DataSourceProtocolError, match="不是 active"):
        build_publication_authorization(inactive, _scope(), _metadata(), authorized_at=AUTHORIZED_AT)
    with pytest.raises(DataSourceProtocolError, match="有效期"):
        build_publication_authorization(expired, _scope(), _metadata(), authorized_at=AUTHORIZED_AT)


def test_build_rejects_adapter_scope_and_license_mismatches() -> None:
    with pytest.raises(DataSourceProtocolError, match="adapter_id"):
        build_publication_authorization(
            _source_config(),
            _scope(),
            _metadata(adapter_id="other_adapter"),
            authorized_at=AUTHORIZED_AT,
        )

    with pytest.raises(DataSourceProtocolError, match="product"):
        build_publication_authorization(
            _source_config(),
            replace(_scope(), products=("AL",)),
            _metadata(),
            authorized_at=AUTHORIZED_AT,
        )

    with pytest.raises(DataSourceProtocolError, match="environment"):
        build_publication_authorization(
            _source_config(),
            replace(_scope(), environment="development"),
            _metadata(),
            authorized_at=AUTHORIZED_AT,
        )


def test_live_signal_requires_explicit_live_authorization() -> None:
    scope = _scope(purpose=PublicationPurpose.LIVE_SIGNAL)
    license = _license(
        permitted_purposes=("internal_research", "live_signal"),
        prohibited_purposes=("redistribution",),
        allows_live_trading=False,
    )

    with pytest.raises(DataSourceProtocolError, match="live trading"):
        build_publication_authorization(
            _source_config(license=license),
            scope,
            _metadata(),
            authorized_at=AUTHORIZED_AT,
        )


def test_validation_rejects_config_metadata_or_time_drift() -> None:
    authorization = build_publication_authorization(
        _source_config(),
        _scope(),
        _metadata(),
        authorized_at=AUTHORIZED_AT,
    )

    with pytest.raises(DataSourceProtocolError, match="不一致"):
        validate_publication_authorization(
            authorization,
            replace(_source_config(), name="Changed source name"),
            _metadata(),
            authorized_at=AUTHORIZED_AT,
        )
    with pytest.raises(DataSourceProtocolError, match="不一致"):
        validate_publication_authorization(
            authorization,
            _source_config(),
            _metadata(),
            authorized_at=AUTHORIZED_AT + timedelta(seconds=1),
        )


def test_fetch_request_and_raw_capture_are_secret_free_and_hash_bound() -> None:
    request = SourceFetchRequest(
        source_id="fixture_market_source_v1",
        scope=_scope(),
        request_reference="fixture://daily-contracts/20260819",
        requested_at=AUTHORIZED_AT,
        request_parameters=(("end", "2026-08-19"), ("start", "2026-01-01")),
    )
    capture = RawCapture(
        payload=b'{"rows": []}',
        raw_format="application/json",
        source_reference="fixture://daily-contracts/20260819",
        collection_method="fixture-import",
        available_at=AUTHORIZED_AT,
        capture_quality_status=QualityStatus.PASS,
        provenance_attributes=(("batch", "fixture-20260819"),),
    )

    assert request.request_parameters == (("end", "2026-08-19"), ("start", "2026-01-01"))
    assert capture.content_hash == hashlib.sha256(capture.payload).hexdigest()
    with pytest.raises(DataSourceProtocolError, match="凭据"):
        RawCapture(
            payload=b"raw",
            raw_format="application/json",
            source_reference="https://example.test/feed?token=plain-secret",  # secret-scan: allow; reason: disposable test fixture
            collection_method="fixture-import",
            available_at=AUTHORIZED_AT,
            capture_quality_status=QualityStatus.PASS,
        )
    with pytest.raises(DataSourceProtocolError, match="绝对路径"):
        SourceFetchRequest(
            source_id="fixture_market_source_v1",
            scope=_scope(),
            request_reference="/private/import.csv",
            requested_at=AUTHORIZED_AT,
        )


def test_normalized_table_requires_exact_canonical_payload_and_isolates_input_frame() -> None:
    frame = pl.DataFrame({"symbol": ["RB"], "price": [3500.0]})
    normalized = NormalizedTable.from_frame(frame)
    frame[0, "price"] = 1.0

    assert normalized.frame.get_column("price").to_list() == [3500.0]
    assert normalized.content_hash == hashlib.sha256(normalized.payload).hexdigest()
    with pytest.raises(DataSourceProtocolError, match="逐字节"):
        NormalizedTable(frame=normalized.frame, payload=b'{"forged":true}')


class _FixtureAdapter:
    @property
    def adapter_id(self) -> str:
        return "fixture_adapter"

    def metadata(self, scope: PublicationScope) -> AdapterMetadata:
        return _metadata()

    def fetch(self, request: SourceFetchRequest) -> RawCapture:
        return RawCapture(
            payload=b'{"fixture":true}',
            raw_format="application/json",
            source_reference=request.request_reference,
            collection_method="fixture",
            available_at=request.requested_at,
            capture_quality_status=QualityStatus.PASS,
        )

    def normalize(
        self,
        raw_payload: bytes,
        *,
        metadata: AdapterMetadata,
    ) -> NormalizedTable:
        assert raw_payload == b'{"fixture":true}'
        assert metadata.adapter_id == self.adapter_id
        return NormalizedTable.from_frame(pl.DataFrame({"symbol": ["RB"], "price": [3500.0]}))


def test_protocol_is_runtime_checkable_for_explicit_adapter_implementations() -> None:
    assert isinstance(_FixtureAdapter(), DataSourceAdapter)
