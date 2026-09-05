"""Offline evidence tests for the narrow SHFE official-daily retrieval path."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from northstar_quant.data.catalog.models import (
    CanonicalBar,
    ImportRun,
    JobRun,
    ProviderRetrieval,
    ProviderRetrievalRecovery,
    ShfeDailyRetrievalSourceAdmissionReview,
    ShfeDailySourceAdmissionReview,
    SourceReceipt,
)
from northstar_quant.data.core.config import Settings
from northstar_quant.data.ingestion import provider_service
from northstar_quant.data.ingestion.imports import ImportInProgressError, OhlcvImportError
from northstar_quant.data.ingestion.provider_commands import (
    RecoverShfeDailyRetrievalCommand,
    ShfeDailyFetchCommand,
)
from northstar_quant.data.ingestion.provider_http import ShfeDailyHttpClient
from northstar_quant.data.ingestion.provider_service import ShfeDailyRetrievalService
from northstar_quant.data.ingestion.providers.shfe import (
    SHFE_DAILY_MAPPING_VERSION,
    ShfeDailyJsonAdapter,
)
from northstar_quant.data.ingestion.source_admission import (
    SHFE_DAILY_ACQUISITION_USE,
    SHFE_DAILY_ADAPTER_NAME,
    SHFE_DAILY_ADAPTER_VERSION,
    SHFE_DAILY_AVAILABLE_AT_BASIS,
    SHFE_DAILY_ENDPOINT_ID,
    SHFE_DAILY_REDISTRIBUTION_POLICY,
    SHFE_DAILY_RETENTION_POLICY,
    SHFE_DAILY_SOURCE_NAME,
)
from northstar_quant.data.ingestion.source_admission import (
    SHFE_DAILY_MAPPING_VERSION as SHFE_DAILY_REVIEW_MAPPING_VERSION,
)

from .catalog_support import SyntheticCatalog, at_local, seed_synthetic_catalog

_REQUEST_URL = "https://www.shfe.com.cn/data/dailydata/kx/pm20260107.dat"
_TEST_SOURCE_ADMISSION_REVIEW_ID = UUID("00000000-0000-0000-0000-000000000101")


def _settings() -> Settings:
    """Keep network/parser bounds small while preserving production validation."""

    return Settings(
        max_csv_bytes=1_000_000,
        max_csv_rows=100,
        max_csv_field_bytes=1_024,
        max_provider_response_bytes=1_000_000,
        max_provider_response_rows=100,
        provider_timeout_seconds=1.0,
    )


def _command(
    catalog: SyntheticCatalog,
    *,
    idempotency_key: str = "shfe-daily-001",
) -> ShfeDailyFetchCommand:
    return ShfeDailyFetchCommand(
        series_id=catalog.daily_series.id,
        source_symbol="RB2605",
        trading_day=date(2026, 1, 7),
        available_at=at_local(2026, 1, 7, 15),
        idempotency_key=idempotency_key,
        correlation_id="shfe-provider-test",
        source_admission_review_id=_TEST_SOURCE_ADMISSION_REVIEW_ID,
        causation_id="shfe-provider-test-cause",
    )


def _service(
    session: Session,
    handler: Callable[[httpx.Request], httpx.Response],
) -> ShfeDailyRetrievalService:
    _ensure_approved_source_admission_review(session)
    return ShfeDailyRetrievalService(
        session,
        settings=_settings(),
        client=ShfeDailyHttpClient(
            timeout_seconds=1.0,
            max_bytes=1_000_000,
            transport=httpx.MockTransport(handler),
        ),
    )


def _ensure_approved_source_admission_review(session: Session) -> None:
    """Seed only the synthetic, non-secret approval needed by retrieval behavior."""

    if session.get(ShfeDailySourceAdmissionReview, _TEST_SOURCE_ADMISSION_REVIEW_ID) is not None:
        return
    session.add(
        ShfeDailySourceAdmissionReview(
            id=_TEST_SOURCE_ADMISSION_REVIEW_ID,
            review_sequence=_next_source_admission_review_sequence(session),
            source_name=SHFE_DAILY_SOURCE_NAME,
            adapter_name=SHFE_DAILY_ADAPTER_NAME,
            adapter_version=SHFE_DAILY_ADAPTER_VERSION,
            mapping_version=SHFE_DAILY_REVIEW_MAPPING_VERSION,
            endpoint_id=SHFE_DAILY_ENDPOINT_ID,
            status="APPROVED",
            acquisition_use=SHFE_DAILY_ACQUISITION_USE,
            retention_policy=SHFE_DAILY_RETENTION_POLICY,
            redistribution_policy=SHFE_DAILY_REDISTRIBUTION_POLICY,
            available_at_basis=SHFE_DAILY_AVAILABLE_AT_BASIS,
            evidence_ref="SYNTHETIC-SHFE-REVIEW-001",
            evidence_sha256="a" * 64,
            reviewer_id="synthetic-reviewer",
            valid_until=datetime.now(UTC) + timedelta(days=3650),
            idempotency_key="synthetic-shfe-review-001",
            correlation_id="synthetic-shfe-review-001",
        )
    )
    session.commit()


def _record_synthetic_source_admission_review(
    session: Session,
    *,
    status: str = "APPROVED",
    review_sequence: int | None = None,
    created_at: datetime | None = None,
    valid_until: datetime | None = None,
) -> ShfeDailySourceAdmissionReview:
    """Add a fixed-scope synthetic review without any external source claim.

    ``review_sequence`` is the durable current-conclusion order. ``created_at``
    is audit time only and stays configurable here to test that it cannot alter
    admission after an advisory-lock wait.
    """

    nonce = uuid4().hex
    review = ShfeDailySourceAdmissionReview(
        review_sequence=(
            _next_source_admission_review_sequence(session)
            if review_sequence is None
            else review_sequence
        ),
        source_name=SHFE_DAILY_SOURCE_NAME,
        adapter_name=SHFE_DAILY_ADAPTER_NAME,
        adapter_version=SHFE_DAILY_ADAPTER_VERSION,
        mapping_version=SHFE_DAILY_REVIEW_MAPPING_VERSION,
        endpoint_id=SHFE_DAILY_ENDPOINT_ID,
        status=status,
        acquisition_use=SHFE_DAILY_ACQUISITION_USE,
        retention_policy=SHFE_DAILY_RETENTION_POLICY,
        redistribution_policy=SHFE_DAILY_REDISTRIBUTION_POLICY,
        available_at_basis=SHFE_DAILY_AVAILABLE_AT_BASIS,
        evidence_ref=f"SYNTHETIC-SHFE-REVIEW-{nonce}",
        evidence_sha256="b" * 64,
        reviewer_id="synthetic-reviewer",
        valid_until=valid_until or datetime.now(UTC) + timedelta(days=3650),
        idempotency_key=f"synthetic-shfe-review-{nonce}",
        correlation_id=f"synthetic-shfe-review-{nonce}",
        created_at=created_at,
    )
    session.add(review)
    session.commit()
    return review


def _next_source_admission_review_sequence(session: Session) -> int:
    """Allocate synthetic test order using the same durable current-review key."""

    maximum = session.scalar(select(func.max(ShfeDailySourceAdmissionReview.review_sequence)))
    return int(maximum or 0) + 1


def _recovery_command(
    retrieval_id: UUID,
    *,
    reason: str = "QDH-20260902",
    idempotency_key: str = "shfe-stale-recovery-test",
) -> RecoverShfeDailyRetrievalCommand:
    return RecoverShfeDailyRetrievalCommand(
        retrieval_id=retrieval_id,
        operator_id="synthetic-operator",
        reason=reason,
        idempotency_key=idempotency_key,
        correlation_id="shfe-stale-recovery-test",
        causation_id="shfe-stale-recovery-cause",
    )


def _replace_recovery_audit_input(
    command: RecoverShfeDailyRetrievalCommand,
    *,
    field_name: str,
    value: str,
) -> RecoverShfeDailyRetrievalCommand:
    """Replace one audited recovery input without erasing dataclass field types."""

    if field_name == "operator_id":
        return replace(command, operator_id=value)
    if field_name == "reason":
        return replace(command, reason=value)
    if field_name == "idempotency_key":
        return replace(command, idempotency_key=value)
    if field_name == "correlation_id":
        return replace(command, correlation_id=value)
    if field_name == "causation_id":
        return replace(command, causation_id=value)
    raise AssertionError(f"unexpected recovery audit input: {field_name}")


def _reserve_running_retrieval(
    db_session: Session,
    catalog: SyntheticCatalog,
    requests: list[httpx.Request],
    *,
    command: ShfeDailyFetchCommand | None = None,
) -> tuple[ShfeDailyFetchCommand, ProviderRetrieval, ShfeDailyRetrievalService]:
    """Leave an outer retrieval active exactly as an interrupted worker would."""

    def interrupted_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise RuntimeError("synthetic worker interruption")

    command = command or _command(catalog)
    service = _service(db_session, interrupted_handler)
    with pytest.raises(RuntimeError, match="worker interruption"):
        service.retrieve(command)

    retrieval = db_session.scalar(
        select(ProviderRetrieval)
        .join(JobRun, ProviderRetrieval.job_run_id == JobRun.id)
        .where(JobRun.idempotency_key == command.idempotency_key)
    )
    assert retrieval is not None
    assert retrieval.status == "RUNNING"
    return command, retrieval, service


def _mark_retrieval_stale(
    db_session: Session,
    retrieval_id: UUID,
) -> ProviderRetrieval:
    """Backdate only synthetic test evidence beyond the configured minimum threshold."""

    retrieval = db_session.get(ProviderRetrieval, retrieval_id)
    assert retrieval is not None
    retrieval.started_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.commit()
    db_session.refresh(retrieval)
    return retrieval


def _valid_daily_payload() -> bytes:
    """Return synthetic, exact-decimal SHFE-shaped data for RB2605 only."""

    return json.dumps(
        {
            "o_curinstrument": [
                {
                    "DELIVERYMONTH": "RB2605",
                    "OPENPRICE": "3500.00",
                    "HIGHESTPRICE": "3510.00",
                    "LOWESTPRICE": "3490.00",
                    "CLOSEPRICE": "3505.00",
                    "VOLUME": "1000",
                    "TURNOVER": "35050000.00",
                    "OPENINTEREST": "120000",
                }
            ]
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_shfe_daily_success_preserves_retrieval_receipt_import_and_bar_lineage(
    db_session: Session,
) -> None:
    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=_valid_daily_payload(),
            headers={
                "content-type": "application/json",
                "etag": '"synthetic-shfe-etag"',
                "last-modified": "Wed, 07 Jan 2026 07:00:00 GMT",
                "x-request-id": "synthetic-request-001",
            },
        )

    result = _service(db_session, handler).retrieve(_command(catalog))

    assert result.status == "SUCCEEDED"
    assert not result.replayed
    assert result.import_run_id is not None
    assert result.source_receipt_id is not None
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == _REQUEST_URL

    retrieval = db_session.get(ProviderRetrieval, result.retrieval_id)
    import_run = db_session.get(ImportRun, result.import_run_id)
    receipt = db_session.get(SourceReceipt, result.source_receipt_id)
    job = db_session.get(JobRun, result.job_run_id)
    bar = db_session.scalar(select(CanonicalBar))
    review_link = db_session.scalar(
        select(ShfeDailyRetrievalSourceAdmissionReview).where(
            ShfeDailyRetrievalSourceAdmissionReview.provider_retrieval_id == result.retrieval_id
        )
    )

    assert retrieval is not None
    assert retrieval.status == "SUCCEEDED"
    assert retrieval.import_run_id == result.import_run_id
    assert retrieval.source_receipt_id == result.source_receipt_id
    assert retrieval.response_http_status == 200
    assert retrieval.response_etag == '"synthetic-shfe-etag"'
    assert retrieval.response_last_modified == "Wed, 07 Jan 2026 07:00:00 GMT"
    assert retrieval.provider_request_id == "synthetic-request-001"
    assert retrieval.request_descriptor["endpoint_id"] == "shfe_daily_data_v1"
    assert "https://" not in json.dumps(retrieval.request_descriptor, sort_keys=True)
    assert import_run is not None
    assert import_run.status == "SUCCEEDED"
    assert import_run.mapping_version == SHFE_DAILY_MAPPING_VERSION
    assert receipt is not None
    assert receipt.media_type == "application/json"
    assert receipt.input_kind == "PROVIDER_RESPONSE"
    assert receipt.retention_policy == "TRANSIENT"
    assert receipt.acquisition_use == SHFE_DAILY_ACQUISITION_USE
    assert receipt.redistribution_policy == SHFE_DAILY_REDISTRIBUTION_POLICY
    assert job is not None
    assert job.status == "SUCCEEDED"
    assert bar is not None
    assert bar.import_run_id == result.import_run_id
    assert bar.source_content_hash == receipt.content_hash
    assert bar.source_record_id == "SHFE:20260107:RB2605"
    assert bar.event_time == at_local(2026, 1, 6, 21)
    assert bar.available_at == at_local(2026, 1, 7, 15)
    assert bar.close_price == Decimal("3505.00")
    assert bar.volume == Decimal("1000")
    assert review_link is not None
    assert review_link.source_admission_review_id == _TEST_SOURCE_ADMISSION_REVIEW_ID


def test_shfe_daily_requires_a_review_before_new_reservation_or_network_request(
    db_session: Session,
) -> None:
    """A missing admission review fails before creating retrieval state."""

    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_valid_daily_payload())

    service = _service(db_session, handler)
    with pytest.raises(
        OhlcvImportError, match="source-admission review UUID is required"
    ) as rejected:
        service.retrieve(replace(_command(catalog), source_admission_review_id=None))

    assert rejected.value.code == "SOURCE_ADMISSION_REVIEW_REQUIRED"
    assert db_session.in_transaction() is False
    assert requests == []
    assert db_session.scalar(select(func.count()).select_from(ProviderRetrieval)) == 0
    assert db_session.scalar(select(func.count()).select_from(JobRun)) == 0


@pytest.mark.parametrize("status", ["RESTRICTED", "UNKNOWN"])
def test_shfe_daily_rejects_current_nonapproved_review_before_new_get(
    db_session: Session,
    status: str,
) -> None:
    """Every closed non-approval outcome fails before new retrieval state exists."""

    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_valid_daily_payload())

    service = _service(db_session, handler)
    review = _record_synthetic_source_admission_review(
        db_session,
        status=status,
        created_at=datetime.now(UTC) + timedelta(seconds=1),
    )
    with pytest.raises(OhlcvImportError, match="does not approve") as rejected:
        service.retrieve(replace(_command(catalog), source_admission_review_id=review.id))

    assert rejected.value.code == "SOURCE_ADMISSION_REVIEW_NOT_APPROVED"
    assert requests == []
    assert db_session.scalar(select(func.count()).select_from(ProviderRetrieval)) == 0
    assert db_session.scalar(select(func.count()).select_from(JobRun)) == 0


def test_shfe_daily_rechecks_review_expiry_immediately_before_reservation_commit(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A review expiring during reservation cannot produce a durable GET permit."""

    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_valid_daily_payload())

    service = _service(db_session, handler)
    review = db_session.get(ShfeDailySourceAdmissionReview, _TEST_SOURCE_ADMISSION_REVIEW_ID)
    assert review is not None
    valid_until = review.valid_until.replace(tzinfo=UTC)
    clock_values = iter(
        (
            valid_until - timedelta(seconds=2),
            valid_until - timedelta(seconds=1),
            valid_until + timedelta(seconds=1),
        )
    )
    monkeypatch.setattr(provider_service, "_utc_now", lambda: next(clock_values))

    with pytest.raises(OhlcvImportError, match="has expired") as expired:
        service.retrieve(_command(catalog))

    assert expired.value.code == "SOURCE_ADMISSION_REVIEW_EXPIRED"
    assert requests == []
    assert db_session.in_transaction() is False
    assert db_session.scalar(select(func.count()).select_from(ProviderRetrieval)) == 0
    assert db_session.scalar(select(func.count()).select_from(JobRun)) == 0


def test_shfe_daily_replay_returns_the_existing_evidence_without_a_second_fetch(
    db_session: Session,
) -> None:
    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_valid_daily_payload())

    service = _service(db_session, handler)
    command = _command(catalog)
    first = service.retrieve(command)
    replay = service.retrieve(command)

    assert first.status == "SUCCEEDED"
    assert replay.status == "SUCCEEDED"
    assert not first.replayed
    assert replay.replayed
    assert replay.retrieval_id == first.retrieval_id
    assert replay.job_run_id == first.job_run_id
    assert replay.import_run_id == first.import_run_id
    assert replay.source_receipt_id == first.source_receipt_id
    assert len(requests) == 1
    assert db_session.scalar(select(func.count()).select_from(ProviderRetrieval)) == 1
    assert db_session.scalar(select(func.count()).select_from(ImportRun)) == 1
    assert db_session.scalar(select(func.count()).select_from(CanonicalBar)) == 1


def test_shfe_daily_changed_source_publication_time_never_reuses_old_import_semantics(
    db_session: Session,
) -> None:
    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_valid_daily_payload())

    service = _service(db_session, handler)
    first = service.retrieve(_command(catalog))
    second = service.retrieve(
        replace(
            _command(catalog, idempotency_key="shfe-daily-002"),
            available_at=at_local(2026, 1, 7, 15, 30),
        )
    )

    assert first.status == "SUCCEEDED"
    # Same response bytes but a different asserted source-publication time must
    # create a new import attempt, then fail closed against the old bar rather
    # than falsely linking the second retrieval to first availability semantics.
    assert second.status == "QUARANTINED"
    assert second.import_run_id is not None
    assert second.import_run_id != first.import_run_id
    assert len(requests) == 2
    assert db_session.scalar(select(func.count()).select_from(ProviderRetrieval)) == 2
    second_import = db_session.get(ImportRun, second.import_run_id)
    assert second_import is not None
    assert second_import.status == "QUARANTINED"
    assert second_import.error_code == "CANONICAL_BAR_CONFLICT"


def test_shfe_daily_http_failure_is_a_durable_auditable_failed_retrieval(
    db_session: Session,
) -> None:
    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, content=b"synthetic temporary provider failure")

    result = _service(db_session, handler).retrieve(_command(catalog))

    assert result.status == "FAILED"
    assert result.error_code == "PROVIDER_HTTP_STATUS"
    assert result.import_run_id is None
    assert result.source_receipt_id is None
    assert len(requests) == 1

    retrieval = db_session.get(ProviderRetrieval, result.retrieval_id)
    job = db_session.get(JobRun, result.job_run_id)
    assert retrieval is not None
    assert retrieval.status == "FAILED"
    assert retrieval.error_code == "PROVIDER_HTTP_STATUS"
    assert retrieval.response_http_status == 503
    assert retrieval.error_detail == "the SHFE endpoint returned HTTP 503"
    assert retrieval.finished_at is not None
    assert retrieval.source_receipt_id is None
    assert retrieval.import_run_id is None
    assert job is not None
    assert job.status == "FAILED"
    assert job.error_code == "PROVIDER_HTTP_STATUS"
    assert db_session.scalar(select(func.count()).select_from(SourceReceipt)) == 0
    assert db_session.scalar(select(func.count()).select_from(ImportRun)) == 0
    assert db_session.scalar(select(func.count()).select_from(CanonicalBar)) == 0


def test_shfe_daily_staging_failure_is_a_terminal_failed_retrieval(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_valid_daily_payload())

    def reject_private_file(*_args: object, **_kwargs: object) -> tuple[int, str]:
        raise OSError("synthetic temporary directory failure")

    monkeypatch.setattr(tempfile, "mkstemp", reject_private_file)
    result = _service(db_session, handler).retrieve(_command(catalog))

    assert result.status == "FAILED"
    assert result.error_code == "PROVIDER_RESPONSE_STAGING_FAILED"
    assert result.import_run_id is None
    assert result.source_receipt_id is None
    assert len(requests) == 1

    retrieval = db_session.get(ProviderRetrieval, result.retrieval_id)
    job = db_session.get(JobRun, result.job_run_id)
    assert retrieval is not None
    assert retrieval.status == "FAILED"
    assert retrieval.error_code == "PROVIDER_RESPONSE_STAGING_FAILED"
    assert retrieval.error_detail == "the provider response could not be staged transiently"
    assert job is not None
    assert job.status == "FAILED"
    assert job.error_code == "PROVIDER_RESPONSE_STAGING_FAILED"
    assert db_session.scalar(select(func.count()).select_from(SourceReceipt)) == 0
    assert db_session.scalar(select(func.count()).select_from(ImportRun)) == 0
    assert db_session.scalar(select(func.count()).select_from(CanonicalBar)) == 0


def test_shfe_daily_active_reservation_is_not_reported_as_a_terminal_replay(
    db_session: Session,
) -> None:
    catalog = seed_synthetic_catalog(db_session)

    def crashing_handler(_request: httpx.Request) -> httpx.Response:
        raise RuntimeError("synthetic process interruption")

    with pytest.raises(RuntimeError, match="process interruption"):
        _service(db_session, crashing_handler).retrieve(_command(catalog))

    with pytest.raises(ImportInProgressError, match="still active") as error:
        _service(db_session, crashing_handler).retrieve(_command(catalog))

    assert error.value.code == "RETRIEVAL_IN_PROGRESS"
    retrieval = db_session.scalar(select(ProviderRetrieval))
    assert retrieval is not None
    assert retrieval.status == "RUNNING"


def test_shfe_daily_recovery_refuses_a_fresh_active_retrieval_without_mutation(
    db_session: Session,
) -> None:
    """An operator cannot terminalize a request merely because its worker is slow."""

    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []
    _command_value, retrieval, service = _reserve_running_retrieval(db_session, catalog, requests)
    descriptor_before = json.loads(json.dumps(retrieval.request_descriptor, sort_keys=True))

    with pytest.raises(OhlcvImportError, match="has not reached") as error:
        service.recover_stale(_recovery_command(retrieval.id))

    assert error.value.code == "RETRIEVAL_NOT_STALE"
    assert len(requests) == 1
    db_session.refresh(retrieval)
    assert retrieval.status == "RUNNING"
    assert retrieval.finished_at is None
    assert retrieval.request_descriptor == descriptor_before
    assert retrieval.job_run.status == "RUNNING"
    assert db_session.scalar(select(func.count()).select_from(ProviderRetrievalRecovery)) == 0


@pytest.mark.parametrize(
    ("field_name", "unsafe_value", "expected_code"),
    [
        pytest.param(
            "reason",
            "https://tickets.example.test/QDH-20260902",
            "INVALID_RECOVERY_INCIDENT_REFERENCE",
            id="reason-url",
        ),
        pytest.param(
            "reason",
            "QDH-20260902\noperator-note",
            "INVALID_RECOVERY_INCIDENT_REFERENCE",
            id="reason-newline",
        ),
        pytest.param(
            "operator_id",
            "https://operators.example.test/synthetic-operator",
            "INVALID_RECOVERY_IDENTIFIER",
            id="operator-url",
        ),
        pytest.param(
            "correlation_id",
            "shfe recovery correlation",
            "INVALID_RECOVERY_IDENTIFIER",
            id="correlation-whitespace",
        ),
    ],
)
def test_shfe_daily_recovery_rejects_unsafe_audit_input_without_writing_recovery_evidence(
    db_session: Session,
    field_name: str,
    unsafe_value: str,
    expected_code: str,
) -> None:
    """Recovery metadata is a safe reference plane, never a source-material sink."""

    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []
    _command_value, retrieval, service = _reserve_running_retrieval(db_session, catalog, requests)
    retrieval = _mark_retrieval_stale(db_session, retrieval.id)

    with pytest.raises(OhlcvImportError) as error:
        service.recover_stale(
            _replace_recovery_audit_input(
                _recovery_command(retrieval.id),
                field_name=field_name,
                value=unsafe_value,
            )
        )

    assert error.value.code == expected_code
    assert len(requests) == 1
    db_session.refresh(retrieval)
    assert retrieval.status == "RUNNING"
    assert retrieval.finished_at is None
    assert db_session.scalar(select(func.count()).select_from(ProviderRetrievalRecovery)) == 0


def test_shfe_daily_recovery_terminalizes_stale_outer_reservation_without_second_get(
    db_session: Session,
) -> None:
    """A stale outer reservation becomes durable terminal/audit evidence, not a retry."""

    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []
    _command_value, retrieval, service = _reserve_running_retrieval(db_session, catalog, requests)
    descriptor_before = json.loads(json.dumps(retrieval.request_descriptor, sort_keys=True))
    retrieval = _mark_retrieval_stale(db_session, retrieval.id)
    stale_started_at = retrieval.started_at

    result = service.recover_stale(_recovery_command(retrieval.id))

    assert result.action == "TERMINALIZED"
    assert result.retrieval.retrieval_id == retrieval.id
    assert result.retrieval.status == "STALE"
    assert result.retrieval.import_run_id is None
    assert result.retrieval.source_receipt_id is None
    assert result.retrieval.error_code == "RETRIEVAL_STALE_RECOVERED"
    assert len(requests) == 1

    db_session.refresh(retrieval)
    assert retrieval.status == "STALE"
    assert retrieval.error_code == "RETRIEVAL_STALE_RECOVERED"
    assert retrieval.error_retryable is False
    assert retrieval.finished_at is not None
    assert retrieval.request_descriptor == descriptor_before
    assert retrieval.import_run_id is None
    assert retrieval.source_receipt_id is None
    assert retrieval.job_run.status == "FAILED"
    assert retrieval.job_run.error_code == "RETRIEVAL_STALE_RECOVERED"

    recovery = db_session.scalar(select(ProviderRetrievalRecovery))
    assert recovery is not None
    assert recovery.provider_retrieval_id == retrieval.id
    assert recovery.action == "TERMINALIZED"
    assert recovery.prior_status == "RUNNING"
    assert recovery.prior_attempt_count == 1
    assert recovery.prior_started_at == stale_started_at
    assert recovery.operator_id == "synthetic-operator"
    assert recovery.reason == "QDH-20260902"
    assert recovery.idempotency_key == "shfe-stale-recovery-test"
    assert recovery.correlation_id == "shfe-stale-recovery-test"
    assert recovery.causation_id == "shfe-stale-recovery-cause"

    with pytest.raises(OhlcvImportError, match="only a PENDING or RUNNING") as repeated:
        service.recover_stale(
            _recovery_command(
                retrieval.id,
                idempotency_key="shfe-stale-recovery-repeat",
            )
        )

    assert repeated.value.code == "RETRIEVAL_NOT_ACTIVE"
    assert len(requests) == 1
    assert db_session.scalar(select(func.count()).select_from(ProviderRetrievalRecovery)) == 1


def test_shfe_daily_delayed_worker_cannot_overwrite_a_recovered_stale_parent(
    db_session: Session,
) -> None:
    """A late original worker cannot replace a committed STALE decision with its failure."""

    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []
    recovery_results = []
    service: ShfeDailyRetrievalService

    def delayed_failure_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        parent = db_session.scalar(select(ProviderRetrieval))
        assert parent is not None
        parent = _mark_retrieval_stale(db_session, parent.id)
        recovery_results.append(service.recover_stale(_recovery_command(parent.id)))
        raise httpx.ConnectError("synthetic late provider failure", request=request)

    service = _service(db_session, delayed_failure_handler)
    with pytest.raises(OhlcvImportError, match="terminalized before this worker") as error:
        service.retrieve(_command(catalog))

    assert error.value.code == "RETRIEVAL_ATTEMPT_SUPERSEDED"
    assert len(requests) == 1
    assert len(recovery_results) == 1
    assert recovery_results[0].retrieval.status == "STALE"
    parent = db_session.scalar(select(ProviderRetrieval))
    assert parent is not None
    assert parent.status == "STALE"
    assert parent.error_code == "RETRIEVAL_STALE_RECOVERED"
    assert parent.error_retryable is False
    assert parent.response_http_status is None
    assert parent.import_run_id is None
    assert parent.source_receipt_id is None
    assert parent.job_run.status == "FAILED"
    assert parent.job_run.error_code == "RETRIEVAL_STALE_RECOVERED"
    assert db_session.scalar(select(func.count()).select_from(ProviderRetrievalRecovery)) == 1
    assert db_session.scalar(select(func.count()).select_from(SourceReceipt)) == 0
    assert db_session.scalar(select(func.count()).select_from(ImportRun)) == 0
    assert db_session.scalar(select(func.count()).select_from(CanonicalBar)) == 0


def test_shfe_daily_delayed_worker_cannot_begin_import_after_parent_becomes_stale(
    db_session: Session,
) -> None:
    """The inner receipt/import writer rechecks outer ownership before any durable write."""

    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []
    recovery_results = []
    service: ShfeDailyRetrievalService

    def delayed_success_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        parent = db_session.scalar(select(ProviderRetrieval))
        assert parent is not None
        parent = _mark_retrieval_stale(db_session, parent.id)
        recovery_results.append(service.recover_stale(_recovery_command(parent.id)))
        return httpx.Response(200, content=_valid_daily_payload())

    service = _service(db_session, delayed_success_handler)
    with pytest.raises(OhlcvImportError, match="terminalized before this worker") as error:
        service.retrieve(_command(catalog))

    assert error.value.code == "RETRIEVAL_ATTEMPT_SUPERSEDED"
    assert len(requests) == 1
    assert len(recovery_results) == 1
    parent = db_session.scalar(select(ProviderRetrieval))
    assert parent is not None
    assert parent.status == "STALE"
    assert parent.error_code == "RETRIEVAL_STALE_RECOVERED"
    assert parent.response_http_status is None
    assert parent.import_run_id is None
    assert parent.source_receipt_id is None
    assert db_session.scalar(select(func.count()).select_from(SourceReceipt)) == 0
    assert db_session.scalar(select(func.count()).select_from(ImportRun)) == 0
    assert db_session.scalar(select(func.count()).select_from(CanonicalBar)) == 0


def test_shfe_daily_recovery_idempotency_replays_only_the_same_accountable_action(
    db_session: Session,
) -> None:
    """A recovery key is a durable reservation, not permission to change its intent."""

    catalog = seed_synthetic_catalog(db_session)
    first_requests: list[httpx.Request] = []
    _first_fetch, first_parent, first_service = _reserve_running_retrieval(
        db_session, catalog, first_requests
    )
    first_parent = _mark_retrieval_stale(db_session, first_parent.id)
    recovery_command = _recovery_command(
        first_parent.id,
        idempotency_key="shfe-stale-recovery-idempotency-001",
    )

    first = first_service.recover_stale(recovery_command)
    replay = first_service.recover_stale(recovery_command)

    assert first.action == "TERMINALIZED"
    assert not first.retrieval.replayed
    assert replay.recovery_id == first.recovery_id
    assert replay.action == first.action
    assert replay.retrieval.replayed
    assert replay.retrieval.retrieval_id == first_parent.id
    assert len(first_requests) == 1
    assert db_session.scalar(select(func.count()).select_from(ProviderRetrievalRecovery)) == 1

    with pytest.raises(
        OhlcvImportError, match="already used for a different intent"
    ) as changed_intent:
        first_service.recover_stale(replace(recovery_command, reason="QDH-20260903"))

    assert changed_intent.value.code == "IDEMPOTENCY_KEY_REUSED"
    db_session.rollback()

    second_requests: list[httpx.Request] = []
    second_fetch_command = replace(
        _command(catalog, idempotency_key="shfe-daily-second-active-parent"),
        trading_day=date(2026, 1, 12),
    )
    _second_fetch, second_parent, second_service = _reserve_running_retrieval(
        db_session,
        catalog,
        second_requests,
        command=second_fetch_command,
    )
    second_parent = _mark_retrieval_stale(db_session, second_parent.id)
    with pytest.raises(
        OhlcvImportError, match="already used for a different intent"
    ) as other_parent:
        second_service.recover_stale(replace(recovery_command, retrieval_id=second_parent.id))

    assert other_parent.value.code == "IDEMPOTENCY_KEY_REUSED"
    assert len(second_requests) == 1
    db_session.refresh(second_parent)
    assert second_parent.status == "RUNNING"
    assert db_session.scalar(select(func.count()).select_from(ProviderRetrievalRecovery)) == 1


def test_shfe_daily_recovery_child_requires_stale_parent_fresh_key_and_exact_semantics(
    db_session: Session,
) -> None:
    """Only one explicit, exact child request may follow a terminal stale parent."""

    catalog = seed_synthetic_catalog(db_session)
    parent_requests: list[httpx.Request] = []
    parent_command, parent, service = _reserve_running_retrieval(
        db_session, catalog, parent_requests
    )
    parent_descriptor = json.loads(json.dumps(parent.request_descriptor, sort_keys=True))

    child_before_stale = replace(
        parent_command,
        idempotency_key="shfe-daily-recovery-before-stale",
        recovery_of_retrieval_id=parent.id,
    )
    with pytest.raises(OhlcvImportError, match="explicitly stale") as early_child:
        service.retrieve(child_before_stale)

    assert early_child.value.code == "RECOVERY_PARENT_NOT_STALE"
    assert len(parent_requests) == 1
    assert db_session.scalar(select(func.count()).select_from(ProviderRetrieval)) == 1

    parent = _mark_retrieval_stale(db_session, parent.id)
    recovery = service.recover_stale(_recovery_command(parent.id))
    assert recovery.action == "TERMINALIZED"

    same_parent_key = replace(
        parent_command,
        recovery_of_retrieval_id=parent.id,
    )
    with pytest.raises(OhlcvImportError, match="idempotency key is reserved") as reused_key:
        service.retrieve(same_parent_key)

    assert reused_key.value.code == "IDEMPOTENCY_KEY_UNAVAILABLE"
    assert len(parent_requests) == 1

    child_requests: list[httpx.Request] = []

    def child_handler(request: httpx.Request) -> httpx.Response:
        child_requests.append(request)
        return httpx.Response(200, content=_valid_daily_payload())

    child_command = replace(
        parent_command,
        idempotency_key="shfe-daily-recovery-child-001",
        correlation_id="shfe-recovery-child",
        causation_id="shfe-recovery-child-cause",
        recovery_of_retrieval_id=parent.id,
    )
    child_result = _service(db_session, child_handler).retrieve(child_command)

    assert child_result.status == "SUCCEEDED"
    assert not child_result.replayed
    assert len(child_requests) == 1
    assert child_requests[0].method == "GET"
    assert str(child_requests[0].url) == _REQUEST_URL

    db_session.refresh(parent)
    child = db_session.get(ProviderRetrieval, child_result.retrieval_id)
    assert child is not None
    assert parent.status == "STALE"
    assert parent.error_code == "RETRIEVAL_STALE_RECOVERED"
    assert parent.request_descriptor == parent_descriptor
    assert parent.import_run_id is None
    assert parent.source_receipt_id is None
    assert child.recovery_of_provider_retrieval_id == parent.id
    assert child.attempt_count == parent.attempt_count + 1
    assert child.job_run.idempotency_key == "shfe-daily-recovery-child-001"
    child_descriptor = dict(child.request_descriptor)
    assert child_descriptor.pop("recovery_of_provider_retrieval_id") == str(parent.id)
    assert child_descriptor == parent_descriptor
    parent_review_link = db_session.scalar(
        select(ShfeDailyRetrievalSourceAdmissionReview).where(
            ShfeDailyRetrievalSourceAdmissionReview.provider_retrieval_id == parent.id
        )
    )
    child_review_link = db_session.scalar(
        select(ShfeDailyRetrievalSourceAdmissionReview).where(
            ShfeDailyRetrievalSourceAdmissionReview.provider_retrieval_id == child.id
        )
    )
    assert parent_review_link is not None
    assert child_review_link is not None
    assert child_review_link.source_admission_review_id == (
        parent_review_link.source_admission_review_id
    )


def test_shfe_daily_recovery_child_rejects_a_newer_review_even_when_it_is_approved(
    db_session: Session,
) -> None:
    """A newly approved review cannot relabel a stale parent's source admission."""

    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []
    _ensure_approved_source_admission_review(db_session)
    parent_review = _record_synthetic_source_admission_review(
        db_session,
    )
    parent_command = replace(
        _command(catalog),
        idempotency_key="shfe-daily-review-parent-001",
        source_admission_review_id=parent_review.id,
    )
    _parent_command, parent, service = _reserve_running_retrieval(
        db_session,
        catalog,
        requests,
        command=parent_command,
    )
    parent = _mark_retrieval_stale(db_session, parent.id)
    service.recover_stale(_recovery_command(parent.id))
    newer_review = _record_synthetic_source_admission_review(
        db_session,
    )
    child_command = replace(
        parent_command,
        idempotency_key="shfe-daily-review-child-001",
        correlation_id="shfe-daily-review-child",
        source_admission_review_id=newer_review.id,
        recovery_of_retrieval_id=parent.id,
    )

    with pytest.raises(OhlcvImportError, match="same current source-admission review") as rejected:
        service.retrieve(child_command)

    assert rejected.value.code == "RECOVERY_SOURCE_ADMISSION_REVIEW_MISMATCH"
    assert len(requests) == 1
    assert db_session.scalar(select(func.count()).select_from(ProviderRetrieval)) == 1


def test_shfe_daily_recovery_child_fails_if_its_parent_review_was_superseded(
    db_session: Session,
) -> None:
    """The required parent review must still be current when the child is admitted."""

    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []
    _ensure_approved_source_admission_review(db_session)
    parent_review = _record_synthetic_source_admission_review(
        db_session,
    )
    parent_command = replace(
        _command(catalog),
        idempotency_key="shfe-daily-superseded-parent-001",
        source_admission_review_id=parent_review.id,
    )
    _parent_command, parent, service = _reserve_running_retrieval(
        db_session,
        catalog,
        requests,
        command=parent_command,
    )
    parent = _mark_retrieval_stale(db_session, parent.id)
    service.recover_stale(_recovery_command(parent.id))
    _record_synthetic_source_admission_review(
        db_session,
        status="APPROVED",
    )
    child_command = replace(
        parent_command,
        idempotency_key="shfe-daily-superseded-child-001",
        correlation_id="shfe-daily-superseded-child",
        recovery_of_retrieval_id=parent.id,
    )

    with pytest.raises(OhlcvImportError, match="not the current review") as rejected:
        service.retrieve(child_command)

    assert rejected.value.code == "SOURCE_ADMISSION_REVIEW_SUPERSEDED"
    assert len(requests) == 1
    assert db_session.scalar(select(func.count()).select_from(ProviderRetrieval)) == 1


def test_shfe_daily_recovery_child_rejects_changed_source_availability_semantics(
    db_session: Session,
) -> None:
    """Recovery cannot rewrite a source-publication assertion or silently refetch."""

    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []
    parent_command, parent, service = _reserve_running_retrieval(db_session, catalog, requests)
    parent = _mark_retrieval_stale(db_session, parent.id)
    service.recover_stale(_recovery_command(parent.id))

    changed_available_at = replace(
        parent_command,
        idempotency_key="shfe-daily-recovery-changed-availability",
        available_at=at_local(2026, 1, 7, 15, 30),
        recovery_of_retrieval_id=parent.id,
    )
    with pytest.raises(OhlcvImportError, match="preserve the parent request semantics") as mismatch:
        service.retrieve(changed_available_at)

    assert mismatch.value.code == "RECOVERY_REQUEST_MISMATCH"
    assert len(requests) == 1
    assert db_session.scalar(select(func.count()).select_from(ProviderRetrieval)) == 1


def test_shfe_daily_recovery_reconciles_terminal_inner_import_after_parent_crash_without_get(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal inner import is attached rather than fetched/imported a second time."""

    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_valid_daily_payload())

    service = _service(db_session, handler)

    def crash_before_outer_finalize(
        _service: ShfeDailyRetrievalService,
        _retrieval: ProviderRetrieval,
        _result: object,
    ) -> None:
        raise RuntimeError("synthetic crash after terminal inner import")

    with monkeypatch.context() as patched:
        patched.setattr(
            ShfeDailyRetrievalService,
            "_finish_import_result",
            crash_before_outer_finalize,
        )
        with pytest.raises(RuntimeError, match="after terminal inner import"):
            service.retrieve(_command(catalog))

    db_session.expire_all()
    parent = db_session.scalar(select(ProviderRetrieval))
    inner_import = db_session.scalar(select(ImportRun))
    assert parent is not None
    assert inner_import is not None
    assert parent.status == "RUNNING"
    assert parent.import_run_id is None
    assert parent.source_receipt_id is None
    assert inner_import.status == "SUCCEEDED"
    assert inner_import.source_receipt_id is not None
    parent_descriptor = json.loads(json.dumps(parent.request_descriptor, sort_keys=True))

    parent = _mark_retrieval_stale(db_session, parent.id)
    recovery = service.recover_stale(_recovery_command(parent.id))

    assert recovery.action == "RECONCILED_IMPORT"
    assert recovery.retrieval.status == "SUCCEEDED"
    assert recovery.retrieval.import_run_id == inner_import.id
    assert recovery.retrieval.source_receipt_id == inner_import.source_receipt_id
    assert len(requests) == 1

    db_session.refresh(parent)
    assert parent.status == "SUCCEEDED"
    assert parent.import_run_id == inner_import.id
    assert parent.source_receipt_id == inner_import.source_receipt_id
    assert parent.error_code is None
    assert parent.error_retryable is None
    assert parent.request_descriptor == parent_descriptor
    assert parent.job_run.status == "SUCCEEDED"
    recovery_event = db_session.scalar(select(ProviderRetrievalRecovery))
    assert recovery_event is not None
    assert recovery_event.action == "RECONCILED_IMPORT"
    assert recovery_event.prior_status == "RUNNING"


def test_shfe_daily_recovery_fails_closed_while_inner_import_is_active(
    db_session: Session,
) -> None:
    """An active inner import is never relabeled stale or retried by recovery."""

    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []
    _command_value, parent, service = _reserve_running_retrieval(db_session, catalog, requests)
    inner_job = JobRun(
        job_kind=ShfeDailyJsonAdapter.job_kind,
        idempotency_key=f"provider-import-{parent.id}",
        correlation_id="synthetic-active-inner-import",
        status="RUNNING",
        started_at=parent.started_at,
    )
    db_session.add(inner_job)
    db_session.flush()
    inner_import = ImportRun(
        job_run_id=inner_job.id,
        series_id=catalog.daily_series.id,
        source_name="SHFE_OFFICIAL_DAILY",
        request_fingerprint=f"synthetic-active-inner-import-{parent.id}",
        mapping_version=SHFE_DAILY_MAPPING_VERSION,
        source_timezone_name="Asia/Shanghai",
        status="RUNNING",
    )
    db_session.add(inner_import)
    db_session.commit()

    parent = _mark_retrieval_stale(db_session, parent.id)
    with pytest.raises(OhlcvImportError, match="inner provider import is still active") as error:
        service.recover_stale(_recovery_command(parent.id))

    assert error.value.code == "RECOVERY_RECONCILIATION_REQUIRED"
    assert len(requests) == 1
    db_session.refresh(parent)
    db_session.refresh(inner_import)
    assert parent.status == "RUNNING"
    assert parent.import_run_id is None
    assert parent.source_receipt_id is None
    assert inner_import.status == "RUNNING"
    assert db_session.scalar(select(func.count()).select_from(ProviderRetrievalRecovery)) == 0


def test_shfe_daily_recovery_fails_closed_for_terminal_inner_import_from_another_series(
    db_session: Session,
) -> None:
    """Reconciliation cannot attach otherwise valid evidence from another series."""

    catalog = seed_synthetic_catalog(db_session)
    requests: list[httpx.Request] = []
    _command_value, parent, service = _reserve_running_retrieval(db_session, catalog, requests)
    receipt = SourceReceipt(
        source_name="SHFE_OFFICIAL_DAILY",
        content_hash="a" * 64,
        media_type="application/json",
        byte_count=1,
        input_kind="PROVIDER_RESPONSE",
        source_timezone_name="Asia/Shanghai",
        retention_policy="TRANSIENT",
    )
    db_session.add(receipt)
    db_session.flush()
    inner_job = JobRun(
        job_kind=ShfeDailyJsonAdapter.job_kind,
        idempotency_key=f"provider-import-{parent.id}",
        correlation_id="synthetic-wrong-series-inner-import",
        status="SUCCEEDED",
        started_at=parent.started_at,
        finished_at=datetime.now(UTC),
    )
    db_session.add(inner_job)
    db_session.flush()
    inner_import = ImportRun(
        job_run_id=inner_job.id,
        series_id=catalog.minute_series.id,
        source_receipt_id=receipt.id,
        source_name="SHFE_OFFICIAL_DAILY",
        request_fingerprint=f"synthetic-wrong-series-inner-import-{parent.id}",
        mapping_version=SHFE_DAILY_MAPPING_VERSION,
        source_timezone_name="Asia/Shanghai",
        status="SUCCEEDED",
        effect="APPLIED",
        finished_at=inner_job.finished_at,
    )
    db_session.add(inner_import)
    db_session.commit()

    parent = _mark_retrieval_stale(db_session, parent.id)
    with pytest.raises(OhlcvImportError, match="different data series") as error:
        service.recover_stale(_recovery_command(parent.id))

    assert error.value.code == "RECOVERY_RECONCILIATION_REQUIRED"
    assert len(requests) == 1
    db_session.refresh(parent)
    assert parent.status == "RUNNING"
    assert parent.import_run_id is None
    assert parent.source_receipt_id is None
    assert db_session.scalar(select(func.count()).select_from(ProviderRetrievalRecovery)) == 0


def test_shfe_daily_invalid_json_response_is_retained_as_rejected_import_evidence(
    db_session: Session,
) -> None:
    catalog = seed_synthetic_catalog(db_session)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"o_curinstrument":[{"DELIVERYMONTH":"RB2605"}]}',
        )

    result = _service(db_session, handler).retrieve(_command(catalog))

    assert result.status == "FAILED"
    assert result.error_code == "MISSING_PROVIDER_FIELD"
    assert result.import_run_id is not None
    assert result.source_receipt_id is not None

    retrieval = db_session.get(ProviderRetrieval, result.retrieval_id)
    import_run = db_session.get(ImportRun, result.import_run_id)
    receipt = db_session.get(SourceReceipt, result.source_receipt_id)
    job = db_session.get(JobRun, result.job_run_id)
    assert retrieval is not None
    assert retrieval.status == "FAILED"
    assert retrieval.error_code == "MISSING_PROVIDER_FIELD"
    assert retrieval.error_retryable is False
    assert retrieval.response_http_status == 200
    assert retrieval.import_run_id == result.import_run_id
    assert retrieval.source_receipt_id == result.source_receipt_id
    assert import_run is not None
    assert import_run.status == "FAILED"
    assert import_run.effect == "REJECTED"
    assert import_run.error_code == "MISSING_PROVIDER_FIELD"
    assert receipt is not None
    assert receipt.input_kind == "PROVIDER_RESPONSE"
    assert job is not None
    assert job.status == "FAILED"
    assert job.error_code == "MISSING_PROVIDER_FIELD"
    assert db_session.scalar(select(func.count()).select_from(CanonicalBar)) == 0


def test_shfe_daily_rejects_duplicate_json_keys_as_ambiguous_source_evidence(
    db_session: Session,
) -> None:
    catalog = seed_synthetic_catalog(db_session)
    response = (
        b'{"o_curinstrument":[{"DELIVERYMONTH":"RB2605","OPENPRICE":"3500",'
        b'"HIGHESTPRICE":"3510","LOWESTPRICE":"3490","CLOSEPRICE":"3504",'
        b'"CLOSEPRICE":"3505","VOLUME":"1000"}]}'
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=response)

    result = _service(db_session, handler).retrieve(_command(catalog))

    assert result.status == "FAILED"
    assert result.import_run_id is not None
    import_run = db_session.get(ImportRun, result.import_run_id)
    assert import_run is not None
    assert import_run.error_code == "DUPLICATE_PROVIDER_FIELD"


def test_shfe_daily_contains_json_integer_parse_limit_as_failure_evidence(
    db_session: Session,
) -> None:
    catalog = seed_synthetic_catalog(db_session)
    oversized_integer = b"9" * 4_400
    response = (
        b'{"o_curinstrument":[{"DELIVERYMONTH":"RB2605","OPENPRICE":'
        + oversized_integer
        + b',"HIGHESTPRICE":"3510","LOWESTPRICE":"3490","CLOSEPRICE":"3505",'
        b'"VOLUME":"1000"}]}'
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=response)

    result = _service(db_session, handler).retrieve(_command(catalog))

    assert result.status == "FAILED"
    assert result.import_run_id is not None
    import_run = db_session.get(ImportRun, result.import_run_id)
    assert import_run is not None
    assert import_run.error_code == "INVALID_PROVIDER_RESPONSE"
