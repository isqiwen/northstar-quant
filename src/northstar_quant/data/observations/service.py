"""Transactional append-only supersession and point-in-time revision selection."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import TypedDict
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from northstar_quant.data.catalog.models import (
    CanonicalBar,
    DataSeries,
    ImportRecord,
    ImportRun,
    JobRun,
)
from northstar_quant.data.observations.revisions import (
    MAX_OBSERVATION_REVISIONS,
    OBSERVATION_SUPERSESSION_MAPPING_VERSION,
    ObservationRevisionError,
    SupersedeObservationCommand,
    SupersedeObservationResult,
    canonical_observation_payload_hash,
    render_utc_timestamp,
    stable_json_sha256,
    validate_supersede_observation_command,
)

_DATABASE_SCALE = 12


class _ValidatedValues(TypedDict):
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    turnover: Decimal | None
    open_interest: Decimal | None


class ObservationRevisionService:
    """Append a verified correction without exposing raw database mutation."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def supersede(self, command: SupersedeObservationCommand) -> SupersedeObservationResult:
        command = validate_supersede_observation_command(command)
        existing = self._session.scalar(
            select(CanonicalBar).where(
                CanonicalBar.revision_idempotency_key == command.idempotency_key
            )
        )
        if existing is not None:
            return self._replay_or_reject(existing, command)

        self._session.rollback()
        try:
            prior = self._session.scalar(
                select(CanonicalBar)
                .where(CanonicalBar.id == command.supersedes_observation_id)
                .with_for_update()
            )
            if prior is None:
                raise ObservationRevisionError(
                    "OBSERVATION_REVISION_PREDECESSOR_NOT_FOUND",
                    "the exact predecessor observation does not exist",
                )
            successor = self._session.scalar(
                select(CanonicalBar.id).where(CanonicalBar.supersedes_canonical_bar_id == prior.id)
            )
            if successor is not None:
                raise ObservationRevisionError(
                    "OBSERVATION_REVISION_PREDECESSOR_NOT_HEAD",
                    "the selected predecessor already has a retained successor",
                )
            if prior.revision_number >= MAX_OBSERVATION_REVISIONS:
                raise ObservationRevisionError(
                    "OBSERVATION_REVISION_LIMIT_EXCEEDED",
                    f"one observation may contain at most {MAX_OBSERVATION_REVISIONS} revisions",
                )

            conflict = self._load_conflict(command.conflict_import_record_id, prior)
            conflict_run = conflict.import_run
            receipt = conflict_run.source_receipt
            if receipt is None or conflict_run.source_timezone_name is None:
                raise ObservationRevisionError(
                    "OBSERVATION_REVISION_SOURCE_PIN_MISSING",
                    "supersession evidence must retain an exact source receipt and timezone",
                )
            if conflict_run.source_timezone_name != prior.source_timezone_name:
                raise ObservationRevisionError(
                    "OBSERVATION_REVISION_TIMEZONE_INCOMPATIBLE",
                    "a supersession must retain the predecessor's source timezone semantics",
                )
            source_timezone = _timezone(conflict_run.source_timezone_name)
            _assert_source_time_unambiguous(command.available_at, source_timezone)

            series = self._session.scalar(
                select(DataSeries).where(DataSeries.id == prior.series_id).with_for_update()
            )
            if series is None or series.volume_unit is None or series.turnover_currency is None:
                raise ObservationRevisionError(
                    "OBSERVATION_REVISION_SERIES_INVALID",
                    "the predecessor's series lacks exact active unit metadata",
                )
            if series.status != "ACTIVE":
                raise ObservationRevisionError(
                    "OBSERVATION_REVISION_SERIES_INACTIVE",
                    "a correction may be appended only to an active series",
                )

            event_time = _canonical_timestamp(prior.event_time)
            prior_available_at = _canonical_timestamp(prior.available_at)
            if command.trading_day != prior.trading_day:
                raise ObservationRevisionError(
                    "OBSERVATION_REVISION_TRADING_DAY_MISMATCH",
                    "a supersession must retain the predecessor's explicit trading_day",
                )
            if command.available_at <= prior_available_at:
                raise ObservationRevisionError(
                    "OBSERVATION_REVISION_TIME_NOT_MONOTONIC",
                    "a supersession must become available strictly after its predecessor",
                )
            authority_now = self._session.scalar(select(func.current_timestamp()))
            if not isinstance(authority_now, datetime):
                raise ObservationRevisionError(
                    "OBSERVATION_REVISION_AUTHORITY_TIME_UNAVAILABLE",
                    "the authority store did not return an interpretable current timestamp",
                )
            if command.available_at > _as_utc(authority_now):
                raise ObservationRevisionError(
                    "OBSERVATION_REVISION_TIME_FUTURE",
                    "a supersession cannot become available after authority-store time",
                )

            values = _validate_values(command, series)
            normalized_payload_hash = canonical_observation_payload_hash(
                series_id=prior.series_id,
                event_time=event_time,
                trading_day=command.trading_day,
                available_at=command.available_at,
                **values,
            )
            if conflict.normalized_payload_hash != normalized_payload_hash:
                raise ObservationRevisionError(
                    "OBSERVATION_REVISION_CANONICAL_HASH_DRIFT",
                    "the correction no longer matches the retained conflict payload hash",
                )

            request_fingerprint = _request_fingerprint(
                command,
                predecessor=prior,
                normalized_payload_hash=normalized_payload_hash,
            )
            storage_event_time = event_time.astimezone(source_timezone)
            storage_available_at = command.available_at.astimezone(source_timezone)
            job = JobRun(
                job_kind="canonical_observation_supersession",
                idempotency_key=command.idempotency_key,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
                status="SUCCEEDED",
                started_at=authority_now,
                finished_at=authority_now,
            )
            self._session.add(job)
            self._session.flush()
            mapping = {
                "mapping_version": OBSERVATION_SUPERSESSION_MAPPING_VERSION,
                "supersedes_observation_id": str(prior.id),
                "conflict_import_record_id": str(conflict.id),
                "reason": command.reason,
            }
            import_run = ImportRun(
                job_run_id=job.id,
                series_id=prior.series_id,
                source_receipt_id=receipt.id,
                source_name=receipt.source_name,
                request_fingerprint=request_fingerprint,
                mapping_version=OBSERVATION_SUPERSESSION_MAPPING_VERSION,
                mapping_hash=stable_json_sha256(mapping),
                mapping=mapping,
                source_timezone_name=conflict_run.source_timezone_name,
                status="SUCCEEDED",
                effect="APPLIED",
                rows_read=1,
                rows_accepted=1,
                rows_rejected=0,
                rows_inserted=1,
                rows_duplicate_identical=0,
                rows_conflicted=0,
                event_time_from=storage_event_time,
                event_time_to=storage_event_time,
                trading_day_from=command.trading_day,
                trading_day_to=command.trading_day,
                available_at_from=storage_available_at,
                available_at_to=storage_available_at,
                started_at=authority_now,
                finished_at=authority_now,
            )
            self._session.add(import_run)
            self._session.flush()
            source_record = ImportRecord(
                import_run_id=import_run.id,
                # ImportRecord row numbers are local to their owning run and use
                # the existing header-offset convention.  The original source
                # position remains reachable through the retained conflict FK.
                source_row_number=2,
                source_record_id=conflict.source_record_id,
                normalized_payload_hash=normalized_payload_hash,
                event_time=storage_event_time,
                disposition="INSERTED",
                evidence="explicit supersession retained by observation revision interface",
            )
            self._session.add(source_record)
            self._session.flush()
            revision = CanonicalBar(
                series_id=prior.series_id,
                import_run_id=import_run.id,
                event_time=storage_event_time,
                trading_day=command.trading_day,
                available_at=storage_available_at,
                source_timezone_name=conflict_run.source_timezone_name,
                source_name=receipt.source_name,
                source_record_id=conflict.source_record_id,
                source_content_hash=receipt.content_hash,
                normalized_payload_hash=normalized_payload_hash,
                revision_number=prior.revision_number + 1,
                supersedes_canonical_bar_id=prior.id,
                revision_source_import_record_id=source_record.id,
                supersession_evidence_import_record_id=conflict.id,
                supersession_reason=command.reason,
                revision_idempotency_key=command.idempotency_key,
                revision_request_fingerprint=request_fingerprint,
                revision_correlation_id=command.correlation_id,
                revision_causation_id=command.causation_id,
                **values,
            )
            self._session.add(revision)
            self._session.flush()
            source_record.canonical_bar_id = revision.id
            self._session.commit()
            return _result(revision, replayed=False)
        except IntegrityError as error:
            self._session.rollback()
            existing = self._session.scalar(
                select(CanonicalBar).where(
                    CanonicalBar.revision_idempotency_key == command.idempotency_key
                )
            )
            if existing is not None:
                return self._replay_or_reject(existing, command)
            self._session.rollback()
            raise ObservationRevisionError(
                "OBSERVATION_REVISION_CONCURRENT_CONFLICT",
                "a concurrent correction changed this revision chain; retry exact intent",
            ) from error
        except BaseException:
            self._session.rollback()
            raise

    def _load_conflict(self, record_id: UUID, prior: CanonicalBar) -> ImportRecord:
        conflict = self._session.scalar(
            select(ImportRecord)
            .where(ImportRecord.id == record_id)
            .options(joinedload(ImportRecord.import_run).joinedload(ImportRun.source_receipt))
        )
        if (
            conflict is None
            or conflict.disposition != "CONFLICT"
            or conflict.error_code != "CANONICAL_BAR_CONFLICT"
            or conflict.conflicting_bar_id != prior.id
            or conflict.import_run.status != "QUARANTINED"
            or conflict.import_run.effect != "REJECTED"
            or conflict.import_run.series_id != prior.series_id
        ):
            raise ObservationRevisionError(
                "OBSERVATION_REVISION_CONFLICT_EVIDENCE_INVALID",
                "supersession requires the exact retained conflict against its predecessor",
            )
        conflict_event_time = _canonical_timestamp(conflict.event_time)
        prior_event_time = _canonical_timestamp(prior.event_time)
        if conflict_event_time != prior_event_time:
            raise ObservationRevisionError(
                "OBSERVATION_REVISION_CONFLICT_EVIDENCE_INVALID",
                "the retained conflict does not identify the predecessor event_time",
            )
        return conflict

    def _replay_or_reject(
        self,
        revision: CanonicalBar,
        command: SupersedeObservationCommand,
    ) -> SupersedeObservationResult:
        revision_available_at = _canonical_timestamp(revision.available_at)
        command_matches = (
            revision.supersedes_canonical_bar_id == command.supersedes_observation_id
            and revision.supersession_evidence_import_record_id == command.conflict_import_record_id
            and revision_available_at == command.available_at
            and revision.trading_day == command.trading_day
            and revision.open_price == command.open_price
            and revision.high_price == command.high_price
            and revision.low_price == command.low_price
            and revision.close_price == command.close_price
            and revision.volume == command.volume
            and revision.turnover == command.turnover
            and revision.open_interest == command.open_interest
            and revision.supersession_reason == command.reason
        )
        if not command_matches or revision.normalized_payload_hash is None:
            self._session.rollback()
            raise ObservationRevisionError(
                "OBSERVATION_REVISION_IDEMPOTENCY_KEY_REUSED",
                "the idempotency key was already used for different supersession intent",
            )
        expected_fingerprint = _request_fingerprint(
            command,
            predecessor_id=command.supersedes_observation_id,
            predecessor_revision=revision.revision_number - 1,
            normalized_payload_hash=revision.normalized_payload_hash,
        )
        if revision.revision_request_fingerprint != expected_fingerprint:
            self._session.rollback()
            raise ObservationRevisionError(
                "OBSERVATION_REVISION_IDEMPOTENCY_KEY_REUSED",
                "the idempotency key was already used for different supersession intent",
            )
        result = _result(revision, replayed=True)
        self._session.rollback()
        return result


def select_point_in_time_revisions(
    bars: Iterable[CanonicalBar],
    *,
    as_of: datetime,
    session: Session,
    retain_earliest_future: bool,
) -> tuple[CanonicalBar, ...]:
    """Resolve one unbranched revision per series/event key at an exact cutoff.

    Quality evaluation retains an earliest future first revision so it can
    report late visibility. Snapshot publication uses ``False`` and includes
    only observations actually visible at the cutoff.
    """

    cutoff = _as_utc(as_of)
    by_key: dict[tuple[UUID, datetime], list[CanonicalBar]] = defaultdict(list)
    for bar in bars:
        event_time = _canonical_timestamp(bar.event_time)
        by_key[(bar.series_id, event_time)].append(bar)

    selected: list[CanonicalBar] = []
    for (_series_id, _event_time), revisions in by_key.items():
        ordered = sorted(revisions, key=lambda item: (item.revision_number, str(item.id)))
        _validate_chain(ordered, session)
        visible = [item for item in ordered if _canonical_timestamp(item.available_at) <= cutoff]
        if visible:
            selected.append(visible[-1])
        elif retain_earliest_future:
            selected.append(ordered[0])
    return tuple(
        sorted(
            selected,
            key=lambda item: (
                item.trading_day,
                _canonical_timestamp(item.event_time),
                str(item.id),
            ),
        )
    )


def _validate_chain(revisions: list[CanonicalBar], session: Session) -> None:
    if not revisions or len(revisions) > MAX_OBSERVATION_REVISIONS:
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_CHAIN_INVALID",
            "the observation revision chain is empty or exceeds its fixed bound",
        )
    for index, revision in enumerate(revisions):
        expected_number = index + 1
        if revision.revision_number != expected_number:
            raise ObservationRevisionError(
                "OBSERVATION_REVISION_CHAIN_INVALID",
                "observation revision numbers must be contiguous from one",
            )
        if index == 0:
            if revision.supersedes_canonical_bar_id is not None:
                raise ObservationRevisionError(
                    "OBSERVATION_REVISION_CHAIN_INVALID",
                    "the first observation revision cannot supersede another revision",
                )
            continue
        previous = revisions[index - 1]
        if (
            revision.supersedes_canonical_bar_id != previous.id
            or revision.trading_day != previous.trading_day
            or _canonical_timestamp(revision.available_at)
            <= _canonical_timestamp(previous.available_at)
        ):
            raise ObservationRevisionError(
                "OBSERVATION_REVISION_CHAIN_INVALID",
                "every correction must explicitly and monotonically supersede its predecessor",
            )


def _validate_values(command: SupersedeObservationCommand, series: DataSeries) -> _ValidatedValues:
    open_price = _exact_decimal(command.open_price, series.price_scale, 24, "open_price")
    high_price = _exact_decimal(command.high_price, series.price_scale, 24, "high_price")
    low_price = _exact_decimal(command.low_price, series.price_scale, 24, "low_price")
    close_price = _exact_decimal(command.close_price, series.price_scale, 24, "close_price")
    volume = _exact_decimal(command.volume, series.quantity_scale, 28, "volume")
    turnover = (
        _exact_decimal(command.turnover, _DATABASE_SCALE, 32, "turnover")
        if command.turnover is not None
        else None
    )
    open_interest = (
        _exact_decimal(command.open_interest, series.quantity_scale, 28, "open_interest")
        if command.open_interest is not None
        else None
    )
    if high_price < low_price or not (
        low_price <= open_price <= high_price and low_price <= close_price <= high_price
    ):
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_OHLC_INVALID",
            "correction OHLC values violate the canonical range relationship",
        )
    if (
        volume < 0
        or (turnover is not None and turnover < 0)
        or (open_interest is not None and open_interest < 0)
    ):
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_QUANTITY_INVALID",
            "correction volume, turnover and open_interest must not be negative",
        )
    return {
        "open_price": open_price,
        "high_price": high_price,
        "low_price": low_price,
        "close_price": close_price,
        "volume": volume,
        "turnover": turnover,
        "open_interest": open_interest,
    }


def _exact_decimal(value: Decimal, scale: int, precision: int, field_name: str) -> Decimal:
    quantum = Decimal(1).scaleb(-scale)
    with localcontext() as context:
        context.prec = max(precision + 1, len(value.as_tuple().digits) + 1)
        try:
            canonical = value.quantize(quantum)
        except InvalidOperation as error:
            raise ObservationRevisionError(
                "OBSERVATION_REVISION_DECIMAL_INVALID",
                f"{field_name} cannot be represented by the canonical decimal domain",
            ) from error
    if canonical != value:
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_PRECISION_INVALID",
            f"{field_name} exceeds the series' explicit fixed-decimal scale",
        )
    integer_digits = 1 if canonical == 0 else max(1, canonical.adjusted() + 1)
    if integer_digits > precision - _DATABASE_SCALE:
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_DECIMAL_INVALID",
            f"{field_name} exceeds the canonical decimal precision",
        )
    return canonical


def _request_fingerprint(
    command: SupersedeObservationCommand,
    *,
    normalized_payload_hash: str,
    predecessor: CanonicalBar | None = None,
    predecessor_id: UUID | None = None,
    predecessor_revision: int | None = None,
) -> str:
    resolved_id = predecessor.id if predecessor is not None else predecessor_id
    resolved_revision = (
        predecessor.revision_number if predecessor is not None else predecessor_revision
    )
    assert resolved_id is not None and resolved_revision is not None
    return stable_json_sha256(
        {
            "protocol": OBSERVATION_SUPERSESSION_MAPPING_VERSION,
            "supersedes_observation_id": str(resolved_id),
            "supersedes_revision_number": resolved_revision,
            "conflict_import_record_id": str(command.conflict_import_record_id),
            "trading_day": command.trading_day.isoformat(),
            "available_at": render_utc_timestamp(command.available_at),
            "normalized_payload_hash": normalized_payload_hash,
            "reason": command.reason,
            "correlation_id": command.correlation_id,
            "causation_id": command.causation_id,
        }
    )


def _result(revision: CanonicalBar, *, replayed: bool) -> SupersedeObservationResult:
    if (
        revision.supersedes_canonical_bar_id is None
        or revision.import_run_id is None
        or revision.normalized_payload_hash is None
    ):
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_INTEGRITY_FAILURE",
            "the persisted supersession is missing required identity pins",
        )
    return SupersedeObservationResult(
        observation_id=revision.id,
        supersedes_observation_id=revision.supersedes_canonical_bar_id,
        revision_number=revision.revision_number,
        import_run_id=revision.import_run_id,
        normalized_payload_hash=revision.normalized_payload_hash,
        replayed=replayed,
    )


def _canonical_timestamp(value: datetime) -> datetime:
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(UTC)
    raise ObservationRevisionError(
        "OBSERVATION_REVISION_TIME_AMBIGUOUS",
        "a canonical revision contains an ambiguous timezone-naive timestamp",
    )


def _timezone(value: str) -> ZoneInfo:
    try:
        timezone_value = ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_TIMEZONE_INVALID",
            "source_timezone_name must identify an IANA timezone",
        ) from error
    if timezone_value.key != value:
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_TIMEZONE_INVALID",
            "source_timezone_name must use its canonical IANA name",
        )
    return timezone_value


def _assert_source_time_unambiguous(value: datetime, timezone_value: ZoneInfo) -> None:
    localized = value.astimezone(timezone_value)
    round_trip = localized.astimezone(UTC).astimezone(timezone_value)
    if localized.replace(fold=round_trip.fold) != round_trip:
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_TIME_AMBIGUOUS",
            "available_at does not map unambiguously to the pinned source timezone",
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ObservationRevisionError(
            "OBSERVATION_REVISION_TIME_AMBIGUOUS",
            "authority timestamps must be timezone-aware",
        )
    return value.astimezone(UTC)
