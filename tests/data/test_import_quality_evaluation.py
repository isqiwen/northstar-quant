"""Critical behavior of the current paged import-quality evaluator."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from northstar_quant.data.catalog.models import (
    CanonicalBar,
    ImportQualityEvaluation,
    ImportQualityFinding,
    ImportRecord,
    ImportRun,
    SourceReceipt,
)
from northstar_quant.data.quality import import_service
from northstar_quant.data.quality.evaluations import (
    ImportQualityEvaluationCommand,
    ImportQualityEvaluationError,
    ImportQualityEvaluationResult,
)
from northstar_quant.data.quality.import_applicability_service import (
    ImportQualityApplicabilityService,
)
from northstar_quant.data.quality.import_service import ImportQualityEvaluationService

from .catalog_support import SyntheticCatalog, seed_synthetic_catalog

_SOURCE_NAME = "synthetic-import-quality"
_SOURCE_TIMEZONE = "Asia/Shanghai"
_TRADING_DAY = date(2026, 1, 7)
_BASE_EVENT_TIME = datetime(2026, 1, 7, tzinfo=UTC)


@dataclass(frozen=True)
class _ImportEvidence:
    import_run_id: UUID
    last_record_id: UUID


def _command(import_run_id: UUID, idempotency_key: str) -> ImportQualityEvaluationCommand:
    return ImportQualityEvaluationCommand(
        import_run_id=import_run_id,
        idempotency_key=idempotency_key,
        correlation_id="import-quality-test",
        causation_id="import-quality-cause",
    )


def _evaluate(
    session: Session, command: ImportQualityEvaluationCommand
) -> ImportQualityEvaluationResult:
    with Session(session.get_bind(), autoflush=False, expire_on_commit=False) as dedicated:
        return ImportQualityEvaluationService(dedicated).evaluate(command)


def _seed_success(
    session: Session,
    catalog: SyntheticCatalog,
    record_count: int,
) -> _ImportEvidence:
    unique_seed = uuid4().hex
    content_hash = hashlib.sha256(f"receipt:{unique_seed}".encode()).hexdigest()
    receipt = SourceReceipt(
        source_name=_SOURCE_NAME,
        content_hash=content_hash,
        media_type="text/csv",
        byte_count=record_count * 128,
        input_kind="CSV",
        source_timezone_name=_SOURCE_TIMEZONE,
        retention_policy="TRANSIENT",
    )
    run = ImportRun(
        series_id=catalog.minute_series.id,
        source_receipt=receipt,
        source_name=_SOURCE_NAME,
        request_fingerprint=hashlib.sha256(f"request:{unique_seed}".encode()).hexdigest(),
        mapping_version="canonical_ohlcv_v1",
        mapping_hash="a" * 64,
        mapping={"profile": "canonical_ohlcv_v1"},
        source_timezone_name=_SOURCE_TIMEZONE,
        status="SUCCEEDED",
        effect="APPLIED",
        rows_read=record_count,
        rows_accepted=record_count,
        rows_rejected=0,
        rows_inserted=record_count,
        rows_duplicate_identical=0,
        rows_conflicted=0,
        event_time_from=_BASE_EVENT_TIME,
        event_time_to=_BASE_EVENT_TIME + timedelta(minutes=record_count - 1),
        trading_day_from=_TRADING_DAY,
        trading_day_to=_TRADING_DAY,
        available_at_from=_BASE_EVENT_TIME + timedelta(minutes=1),
        available_at_to=_BASE_EVENT_TIME + timedelta(minutes=record_count),
    )
    session.add(run)
    session.flush()

    bars: list[dict[str, object]] = []
    records: list[dict[str, object]] = []
    last_record_id = uuid4()
    for index in range(record_count):
        event_time = _BASE_EVENT_TIME + timedelta(minutes=index)
        payload_hash = hashlib.sha256(f"record:{unique_seed}:{index}".encode()).hexdigest()
        bar_id = uuid4()
        record_id = last_record_id if index == record_count - 1 else uuid4()
        source_record_id = f"import-quality-{index:06d}"
        bars.append(
            {
                "id": bar_id,
                "series_id": catalog.minute_series.id,
                "import_run_id": run.id,
                "event_time": event_time,
                "trading_day": _TRADING_DAY,
                "available_at": event_time + timedelta(minutes=1),
                "source_timezone_name": _SOURCE_TIMEZONE,
                "source_name": _SOURCE_NAME,
                "source_record_id": source_record_id,
                "source_content_hash": content_hash,
                "normalized_payload_hash": payload_hash,
                "open_price": Decimal("3500"),
                "high_price": Decimal("3510"),
                "low_price": Decimal("3490"),
                "close_price": Decimal("3505"),
                "volume": Decimal("1"),
                "turnover": Decimal("3505"),
                "open_interest": Decimal("1"),
            }
        )
        records.append(
            {
                "id": record_id,
                "import_run_id": run.id,
                "source_row_number": index + 2,
                "source_record_id": source_record_id,
                "normalized_payload_hash": payload_hash,
                "event_time": event_time,
                "disposition": "INSERTED",
                "canonical_bar_id": bar_id,
            }
        )
    session.execute(insert(CanonicalBar), bars)
    session.execute(insert(ImportRecord), records)
    session.commit()
    return _ImportEvidence(run.id, last_record_id)


def test_import_quality_persists_replays_and_remains_applicable(db_session: Session) -> None:
    evidence = _seed_success(db_session, seed_synthetic_catalog(db_session), 3)
    first = _evaluate(db_session, _command(evidence.import_run_id, "import-quality-001"))
    replay = _evaluate(db_session, _command(evidence.import_run_id, "import-quality-001"))

    assert first.outcome == "PASS"
    assert first.delivery_gate == "ELIGIBLE"
    assert replay.import_quality_evaluation_id == first.import_quality_evaluation_id
    assert replay.replayed
    evaluation = db_session.get(ImportQualityEvaluation, first.import_quality_evaluation_id)
    assert evaluation is not None
    assert len(evaluation.input_fingerprint) == 64
    finding = db_session.scalar(
        select(ImportQualityFinding).where(
            ImportQualityFinding.import_quality_evaluation_id == evaluation.id
        )
    )
    assert finding is not None
    assert (finding.rule_code, finding.outcome) == ("IMPORT_INTEGRITY_CONFIRMED", "PASS")
    with Session(db_session.get_bind(), autoflush=False, expire_on_commit=False) as dedicated:
        applicability = ImportQualityApplicabilityService(dedicated).assess(evaluation.id)
    assert applicability.applicable
    assert applicability.reason_code == "IMPORT_QUALITY_INPUT_CURRENT"


def test_import_quality_hash_is_page_size_independent(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = _seed_success(db_session, seed_synthetic_catalog(db_session), 9)
    monkeypatch.setattr(import_service, "IMPORT_QUALITY_PAGE_SIZE", 3)
    first = _evaluate(db_session, _command(evidence.import_run_id, "import-quality-pages-3"))
    monkeypatch.setattr(import_service, "IMPORT_QUALITY_PAGE_SIZE", 7)
    second = _evaluate(db_session, _command(evidence.import_run_id, "import-quality-pages-7"))
    first_row = db_session.get(ImportQualityEvaluation, first.import_quality_evaluation_id)
    second_row = db_session.get(ImportQualityEvaluation, second.import_quality_evaluation_id)
    assert first_row is not None and second_row is not None
    assert first_row.input_fingerprint == second_row.input_fingerprint


def test_import_quality_detects_persisted_evidence_drift(db_session: Session) -> None:
    evidence = _seed_success(db_session, seed_synthetic_catalog(db_session), 3)
    db_session.execute(
        update(ImportRecord)
        .where(ImportRecord.id == evidence.last_record_id)
        .values(normalized_payload_hash="f" * 64)
    )
    db_session.commit()

    result = _evaluate(db_session, _command(evidence.import_run_id, "import-quality-drift"))
    assert result.outcome == "UNKNOWN"
    assert result.delivery_gate == "BLOCKED"


def test_import_quality_rejects_over_cap_without_evidence(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(import_service, "MAX_IMPORT_QUALITY_RECORDS", 2)
    evidence = _seed_success(db_session, seed_synthetic_catalog(db_session), 3)
    with pytest.raises(ImportQualityEvaluationError) as error:
        _evaluate(db_session, _command(evidence.import_run_id, "import-quality-over-cap"))
    assert error.value.code == "IMPORT_QUALITY_EVALUATION_INPUT_TOO_LARGE"
    assert db_session.scalar(select(func.count()).select_from(ImportQualityEvaluation)) == 0
