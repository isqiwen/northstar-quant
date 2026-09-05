"""Manual, append-only daily quality and coverage evaluation."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from northstar_quant.data.catalog.models import (
    CalendarTradingDay,
    CanonicalBar,
    DataSeries,
    QualityEvaluation,
    QualityFinding,
    TradingSession,
)
from northstar_quant.data.observations.revisions import ObservationRevisionError
from northstar_quant.data.observations.service import select_point_in_time_revisions
from northstar_quant.data.quality.evaluations import (
    DAILY_QUALITY_EVALUATION_SCOPE,
    DAILY_QUALITY_RULE_SET_NAME,
    DAILY_QUALITY_RULE_SET_VERSION,
    DailyQualityEvaluationCommand,
    DailyQualityEvaluationError,
    DailyQualityEvaluationResult,
    validate_daily_quality_evaluation_command,
)

_MAX_EVIDENCE_SAMPLE_DAYS = 20
_MAX_EVIDENCE_SAMPLE_IDENTIFIERS = 8
_MAX_EVIDENCE_BYTES = 2048


@dataclass(frozen=True)
class _FindingSpec:
    rule_code: str
    outcome: str
    severity: str
    trading_days: tuple[date, ...]
    occurrence_count: int
    evidence_reason: str
    bars: tuple[CanonicalBar, ...] = ()


@dataclass(frozen=True)
class _EvaluationAnalysis:
    findings: tuple[_FindingSpec, ...]
    expected_observation_count: int
    covered_observation_count: int
    missing_observation_count: int
    unknown_day_count: int

    @property
    def outcome(self) -> str:
        outcomes = {finding.outcome for finding in self.findings}
        if "UNKNOWN" in outcomes:
            return "UNKNOWN"
        if "FAIL" in outcomes:
            return "FAIL"
        if "WARN" in outcomes:
            return "WARN"
        return "PASS"

    @property
    def delivery_gate(self) -> str:
        return "ELIGIBLE" if self.outcome in {"PASS", "WARN"} else "BLOCKED"


class DailyQualityEvaluationService:
    """Evaluate one bounded daily series view without ingesting or publishing data.

    The service writes a new immutable ``QualityEvaluation`` and linked
    aggregate ``QualityFinding`` rows.  It never updates ``ImportRun`` or
    canonical observations, never sends network traffic, and never creates a
    snapshot. Snapshot publication decides how to consume the persisted
    ``delivery_gate``; this service does not expose data to a research client.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def evaluate(self, command: DailyQualityEvaluationCommand) -> DailyQualityEvaluationResult:
        """Persist or safely replay one exact bounded evaluation command."""

        command = validate_daily_quality_evaluation_command(command)
        existing = self._session.scalar(
            select(QualityEvaluation).where(
                QualityEvaluation.idempotency_key == command.idempotency_key
            )
        )
        if existing is not None:
            return self._replay_or_reject(existing, command)

        # The optimistic idempotency lookup has opened a transaction.  End it
        # before requesting the consistent evaluation snapshot used below.
        self._session.rollback()
        self._begin_consistent_snapshot()
        try:
            existing = self._session.scalar(
                select(QualityEvaluation).where(
                    QualityEvaluation.idempotency_key == command.idempotency_key
                )
            )
            if existing is not None:
                return self._replay_or_reject(existing, command)

            series = self._load_daily_series(command.series_id)
            calendar = series.calendar
            timezone_value = _calendar_timezone(calendar.timezone_name)
            calendar_days = self._load_calendar_days(
                series.calendar_id,
                command.from_trading_day,
                command.to_trading_day,
            )
            sessions = self._load_sessions(
                series.calendar_id,
                command.from_trading_day,
                command.to_trading_day,
            )
            bars = self._load_bars(
                series.id,
                command.from_trading_day,
                command.to_trading_day,
                as_of=command.as_of,
            )
            analysis = _analyze_daily_quality(
                command=command,
                calendar_days=calendar_days,
                sessions=sessions,
                bars=bars,
                timezone_value=timezone_value,
                session=self._session,
            )
            input_fingerprint = _input_fingerprint(
                command=command,
                series=series,
                calendar_days=calendar_days,
                sessions=sessions,
                bars=bars,
                timezone_value=timezone_value,
                session=self._session,
            )
            evaluation = QualityEvaluation(
                series_id=series.id,
                calendar_id=calendar.id,
                calendar_revision=calendar.revision,
                evaluation_scope=DAILY_QUALITY_EVALUATION_SCOPE,
                rule_set_name=DAILY_QUALITY_RULE_SET_NAME,
                rule_set_version=DAILY_QUALITY_RULE_SET_VERSION,
                trading_day_from=command.from_trading_day,
                trading_day_to=command.to_trading_day,
                as_of=command.as_of,
                input_fingerprint=input_fingerprint,
                outcome=analysis.outcome,
                delivery_gate=analysis.delivery_gate,
                expected_observation_count=analysis.expected_observation_count,
                covered_observation_count=analysis.covered_observation_count,
                missing_observation_count=analysis.missing_observation_count,
                unknown_day_count=analysis.unknown_day_count,
                finding_count=len(analysis.findings),
                idempotency_key=command.idempotency_key,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )
            self._session.add(evaluation)
            self._session.flush()
            for finding in analysis.findings:
                self._session.add(
                    QualityFinding(
                        quality_evaluation_id=evaluation.id,
                        import_run_id=_single_import_run_id(finding.bars),
                        series_id=series.id,
                        rule_code=finding.rule_code,
                        outcome=finding.outcome,
                        severity=finding.severity,
                        trading_day_from=min(finding.trading_days)
                        if finding.trading_days
                        else None,
                        trading_day_to=max(finding.trading_days) if finding.trading_days else None,
                        occurrence_count=finding.occurrence_count,
                        evidence=_render_evidence(
                            finding,
                            as_of=command.as_of,
                        ),
                    )
                )
            self._session.commit()
            return _result_from_evaluation(evaluation, replayed=False)
        except IntegrityError as error:
            self._session.rollback()
            concurrent = self._session.scalar(
                select(QualityEvaluation).where(
                    QualityEvaluation.idempotency_key == command.idempotency_key
                )
            )
            if concurrent is not None:
                return self._replay_or_reject(concurrent, command)
            raise DailyQualityEvaluationError(
                "QUALITY_EVALUATION_RESERVATION_CONFLICT",
                "the quality evaluation reservation conflicted; retry the same command safely",
            ) from error
        except BaseException:
            self._session.rollback()
            raise

    def _begin_consistent_snapshot(self) -> None:
        """Use one repeatable PostgreSQL view without requiring a write lock."""

        self._session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))

    def _load_daily_series(self, series_id: UUID) -> DataSeries:
        series = self._session.scalar(
            select(DataSeries)
            .where(DataSeries.id == series_id)
            .options(joinedload(DataSeries.calendar))
        )
        if series is None:
            raise DailyQualityEvaluationError(
                "QUALITY_EVALUATION_SERIES_NOT_FOUND",
                "the requested data series does not exist",
            )
        if series.interval != "1d":
            raise DailyQualityEvaluationError(
                "DAILY_QUALITY_REQUIRES_DAILY_SERIES",
                "daily quality evaluation requires a one-day BAR_START series",
            )
        return series

    def _load_calendar_days(
        self,
        calendar_id: UUID,
        from_trading_day: date,
        to_trading_day: date,
    ) -> tuple[CalendarTradingDay, ...]:
        return tuple(
            self._session.scalars(
                select(CalendarTradingDay)
                .where(
                    CalendarTradingDay.calendar_id == calendar_id,
                    CalendarTradingDay.trading_day.between(from_trading_day, to_trading_day),
                )
                .order_by(CalendarTradingDay.trading_day)
            ).all()
        )

    def _load_sessions(
        self,
        calendar_id: UUID,
        from_trading_day: date,
        to_trading_day: date,
    ) -> tuple[TradingSession, ...]:
        return tuple(
            self._session.scalars(
                select(TradingSession)
                .where(
                    TradingSession.calendar_id == calendar_id,
                    TradingSession.trading_day.between(from_trading_day, to_trading_day),
                )
                .order_by(TradingSession.trading_day, TradingSession.sequence)
            ).all()
        )

    def _load_bars(
        self,
        series_id: UUID,
        from_trading_day: date,
        to_trading_day: date,
        *,
        as_of: datetime,
    ) -> tuple[CanonicalBar, ...]:
        loaded = tuple(
            self._session.scalars(
                select(CanonicalBar)
                .where(
                    CanonicalBar.series_id == series_id,
                    CanonicalBar.trading_day.between(from_trading_day, to_trading_day),
                )
                .order_by(CanonicalBar.trading_day, CanonicalBar.event_time, CanonicalBar.id)
            ).all()
        )
        try:
            return select_point_in_time_revisions(
                loaded,
                as_of=as_of,
                session=self._session,
                retain_earliest_future=True,
            )
        except ObservationRevisionError as error:
            raise DailyQualityEvaluationError(
                "DAILY_QUALITY_REVISION_CHAIN_INVALID",
                "the canonical observation revision chain cannot be interpreted safely",
            ) from error

    def _replay_or_reject(
        self,
        evaluation: QualityEvaluation,
        command: DailyQualityEvaluationCommand,
    ) -> DailyQualityEvaluationResult:
        if not _evaluation_matches_command(evaluation, command):
            self._session.rollback()
            raise DailyQualityEvaluationError(
                "IDEMPOTENCY_KEY_REUSED",
                "the quality evaluation idempotency key was already used for a different intent",
            )
        result = _result_from_evaluation(evaluation, replayed=True)
        self._session.rollback()
        return result


def _analyze_daily_quality(
    *,
    command: DailyQualityEvaluationCommand,
    calendar_days: Sequence[CalendarTradingDay],
    sessions: Sequence[TradingSession],
    bars: Sequence[CanonicalBar],
    timezone_value: ZoneInfo,
    session: Session,
) -> _EvaluationAnalysis:
    """Derive deterministic daily-quality findings from a committed snapshot."""

    calendar_by_day = {calendar_day.trading_day: calendar_day for calendar_day in calendar_days}
    sessions_by_day: dict[date, list[TradingSession]] = defaultdict(list)
    for trading_session in sessions:
        sessions_by_day[trading_session.trading_day].append(trading_session)
    bars_by_day: dict[date, list[CanonicalBar]] = defaultdict(list)
    for bar in bars:
        bars_by_day[bar.trading_day].append(bar)

    calendar_unknown: list[date] = []
    open_without_session: list[date] = []
    incomplete_at_cutoff: list[date] = []
    missing: list[date] = []
    available_after_cutoff: list[date] = []
    bar_start_mismatch_days: list[date] = []
    bar_start_mismatch_bars: list[CanonicalBar] = []
    closed_observation_days: list[date] = []
    closed_observation_bars: list[CanonicalBar] = []
    complete_covered_days: list[date] = []
    complete_covered_bars: list[CanonicalBar] = []
    expected_count = 0
    covered_count = 0

    for trading_day in _inclusive_days(command.from_trading_day, command.to_trading_day):
        calendar_day = calendar_by_day.get(trading_day)
        day_bars = bars_by_day.get(trading_day, [])
        if calendar_day is None:
            calendar_unknown.append(trading_day)
            continue
        if calendar_day.status == "CLOSED":
            if day_bars:
                closed_observation_days.append(trading_day)
                closed_observation_bars.extend(day_bars)
            continue

        day_sessions = sessions_by_day.get(trading_day, [])
        if not day_sessions:
            open_without_session.append(trading_day)
            continue

        expected_count += 1
        expected_start = min(_calendar_datetime(item.opens_at) for item in day_sessions)
        completion = max(_calendar_datetime(item.closes_at) for item in day_sessions)
        expected_bars = [
            bar for bar in day_bars if _calendar_datetime(bar.event_time) == expected_start
        ]
        mismatched = [bar for bar in day_bars if bar not in expected_bars]
        if mismatched:
            bar_start_mismatch_days.append(trading_day)
            bar_start_mismatch_bars.extend(mismatched)

        if command.as_of < completion:
            incomplete_at_cutoff.append(trading_day)
            continue
        visible_expected = [
            bar for bar in expected_bars if _canonical_available_at(bar) <= command.as_of
        ]
        if visible_expected:
            covered_count += 1
            complete_covered_days.append(trading_day)
            complete_covered_bars.extend(visible_expected)
        elif expected_bars:
            available_after_cutoff.append(trading_day)
        else:
            missing.append(trading_day)

    findings: list[_FindingSpec] = []
    if calendar_unknown:
        findings.append(
            _FindingSpec(
                rule_code="CALENDAR_DAY_UNKNOWN",
                outcome="UNKNOWN",
                severity="ERROR",
                trading_days=tuple(calendar_unknown),
                occurrence_count=len(calendar_unknown),
                evidence_reason="calendar_day_not_materialized",
            )
        )
    if open_without_session:
        findings.append(
            _FindingSpec(
                rule_code="OPEN_DAY_SESSION_UNKNOWN",
                outcome="UNKNOWN",
                severity="ERROR",
                trading_days=tuple(open_without_session),
                occurrence_count=len(open_without_session),
                evidence_reason="open_calendar_day_has_no_materialized_session",
            )
        )
    if incomplete_at_cutoff:
        findings.append(
            _FindingSpec(
                rule_code="DAILY_AS_OF_BEFORE_SESSION_CLOSE",
                outcome="UNKNOWN",
                severity="ERROR",
                trading_days=tuple(incomplete_at_cutoff),
                occurrence_count=len(incomplete_at_cutoff),
                evidence_reason="evaluation_cutoff_precedes_daily_session_completion",
            )
        )
    if closed_observation_days:
        findings.append(
            _FindingSpec(
                rule_code="CLOSED_DAY_HAS_OBSERVATION",
                outcome="FAIL",
                severity="ERROR",
                trading_days=tuple(closed_observation_days),
                occurrence_count=len(closed_observation_bars),
                evidence_reason="canonical_observation_exists_for_explicit_closed_day",
                bars=tuple(closed_observation_bars),
            )
        )
    if bar_start_mismatch_days:
        findings.append(
            _FindingSpec(
                rule_code="DAILY_BAR_START_MISMATCH",
                outcome="FAIL",
                severity="ERROR",
                trading_days=tuple(bar_start_mismatch_days),
                occurrence_count=len(bar_start_mismatch_bars),
                evidence_reason="daily_observation_does_not_match_first_materialized_session_open",
                bars=tuple(bar_start_mismatch_bars),
            )
        )
    if available_after_cutoff:
        late_bars = tuple(
            bar for trading_day in available_after_cutoff for bar in bars_by_day[trading_day]
        )
        findings.append(
            _FindingSpec(
                rule_code="DAILY_BAR_AVAILABLE_AFTER_CUTOFF",
                outcome="FAIL",
                severity="ERROR",
                trading_days=tuple(available_after_cutoff),
                occurrence_count=len(available_after_cutoff),
                evidence_reason="expected_daily_observation_is_not_visible_at_evaluation_cutoff",
                bars=late_bars,
            )
        )
    if missing:
        findings.append(
            _FindingSpec(
                rule_code="DAILY_COVERAGE_GAP",
                outcome="FAIL",
                severity="ERROR",
                trading_days=tuple(missing),
                occurrence_count=len(missing),
                evidence_reason="expected_daily_observation_is_absent_after_session_completion",
            )
        )
    if not findings:
        findings.append(
            _FindingSpec(
                rule_code="DAILY_COVERAGE_COMPLETE",
                outcome="PASS",
                severity="INFO",
                trading_days=tuple(complete_covered_days),
                occurrence_count=len(complete_covered_days),
                evidence_reason="all_known_complete_open_days_have_visible_daily_observations",
                bars=tuple(complete_covered_bars),
            )
        )

    return _EvaluationAnalysis(
        findings=tuple(findings),
        expected_observation_count=expected_count,
        covered_observation_count=covered_count,
        missing_observation_count=len(missing) + len(available_after_cutoff),
        unknown_day_count=(
            len(calendar_unknown) + len(open_without_session) + len(incomplete_at_cutoff)
        ),
    )


def _input_fingerprint(
    *,
    command: DailyQualityEvaluationCommand,
    series: DataSeries,
    calendar_days: Sequence[CalendarTradingDay],
    sessions: Sequence[TradingSession],
    bars: Sequence[CanonicalBar],
    timezone_value: ZoneInfo,
    session: Session,
) -> str:
    """Hash only deterministic, non-secret facts used by this rule set."""

    rendered = json.dumps(
        {
            "rule_set": f"{DAILY_QUALITY_RULE_SET_NAME}/{DAILY_QUALITY_RULE_SET_VERSION}",
            "series_id": str(series.id),
            "calendar_id": str(series.calendar_id),
            "calendar_revision": series.calendar.revision,
            "from_trading_day": command.from_trading_day.isoformat(),
            "to_trading_day": command.to_trading_day.isoformat(),
            "as_of": _render_timestamp(command.as_of),
            "calendar_days": [
                [item.trading_day.isoformat(), item.status]
                for item in sorted(calendar_days, key=lambda item: item.trading_day)
            ],
            "sessions": [
                [
                    str(item.id),
                    item.trading_day.isoformat(),
                    item.sequence,
                    _render_timestamp(_calendar_datetime(item.opens_at)),
                    _render_timestamp(_calendar_datetime(item.closes_at)),
                ]
                for item in sorted(
                    sessions, key=lambda item: (item.trading_day, item.sequence, str(item.id))
                )
            ],
            "bars": [
                [
                    str(item.id),
                    item.trading_day.isoformat(),
                    _render_timestamp(_calendar_datetime(item.event_time)),
                    _render_timestamp(_canonical_available_at(item)),
                    str(item.import_run_id) if item.import_run_id is not None else None,
                    item.normalized_payload_hash,
                ]
                for item in sorted(
                    bars,
                    key=lambda item: (
                        item.trading_day,
                        _calendar_datetime(item.event_time),
                        str(item.id),
                    ),
                )
            ],
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _render_evidence(finding: _FindingSpec, *, as_of: datetime) -> str:
    """Render bounded aggregate evidence; no raw source values are retained."""

    bar_ids = sorted({str(bar.id) for bar in finding.bars})[:_MAX_EVIDENCE_SAMPLE_IDENTIFIERS]
    import_run_ids = sorted(
        {str(bar.import_run_id) for bar in finding.bars if bar.import_run_id is not None}
    )[:_MAX_EVIDENCE_SAMPLE_IDENTIFIERS]
    payload: dict[str, object] = {
        "evidence_version": f"{DAILY_QUALITY_RULE_SET_NAME}/{DAILY_QUALITY_RULE_SET_VERSION}",
        "reason": finding.evidence_reason,
        "occurrence_count": finding.occurrence_count,
        "sample_trading_days": [
            item.isoformat() for item in finding.trading_days[:_MAX_EVIDENCE_SAMPLE_DAYS]
        ],
        "as_of": _render_timestamp(as_of),
    }
    if bar_ids:
        payload["sample_canonical_bar_ids"] = bar_ids
    if import_run_ids:
        payload["sample_import_run_ids"] = import_run_ids
    rendered = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(rendered) > _MAX_EVIDENCE_BYTES:  # pragma: no cover - fixed samples make this defensive
        raise DailyQualityEvaluationError(
            "QUALITY_EVIDENCE_TOO_LARGE",
            "the daily quality evidence exceeded its bounded storage limit",
        )
    return rendered


def _single_import_run_id(bars: Iterable[CanonicalBar]) -> UUID | None:
    import_run_ids = {bar.import_run_id for bar in bars if bar.import_run_id is not None}
    return next(iter(import_run_ids)) if len(import_run_ids) == 1 else None


def _inclusive_days(from_trading_day: date, to_trading_day: date) -> Iterable[date]:
    current = from_trading_day
    while current <= to_trading_day:
        yield current
        current += timedelta(days=1)


def _calendar_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise DailyQualityEvaluationError(
            "QUALITY_CALENDAR_TIMEZONE_INVALID",
            "the pinned calendar does not contain a usable IANA timezone",
        ) from error


def _calendar_datetime(value: datetime) -> datetime:
    """Require an unambiguous timestamp from the PostgreSQL authority."""

    if value.tzinfo is not None and value.utcoffset() is not None:
        return value
    raise DailyQualityEvaluationError(
        "QUALITY_CALENDAR_TIMESTAMP_INVALID",
        "the pinned calendar or canonical data contains a timezone-naive timestamp",
    )


def _canonical_available_at(bar: CanonicalBar) -> datetime:
    """Return a canonical observation's unambiguous publication instant."""

    value = bar.available_at
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value
    raise DailyQualityEvaluationError(
        "QUALITY_CANONICAL_TIMESTAMP_INVALID",
        "the canonical observation contains a timezone-naive available_at timestamp",
    )


def _evaluation_matches_command(
    evaluation: QualityEvaluation,
    command: DailyQualityEvaluationCommand,
) -> bool:
    return (
        evaluation.series_id == command.series_id
        and evaluation.evaluation_scope == DAILY_QUALITY_EVALUATION_SCOPE
        and evaluation.rule_set_name == DAILY_QUALITY_RULE_SET_NAME
        and evaluation.rule_set_version == DAILY_QUALITY_RULE_SET_VERSION
        and evaluation.trading_day_from == command.from_trading_day
        and evaluation.trading_day_to == command.to_trading_day
        and _as_utc(evaluation.as_of) == command.as_of
        and evaluation.correlation_id == command.correlation_id
        and evaluation.causation_id == command.causation_id
    )


def _result_from_evaluation(
    evaluation: QualityEvaluation, *, replayed: bool
) -> DailyQualityEvaluationResult:
    return DailyQualityEvaluationResult(
        quality_evaluation_id=evaluation.id,
        series_id=evaluation.series_id,
        outcome=evaluation.outcome,
        delivery_gate=evaluation.delivery_gate,
        expected_observation_count=evaluation.expected_observation_count,
        covered_observation_count=evaluation.covered_observation_count,
        missing_observation_count=evaluation.missing_observation_count,
        unknown_day_count=evaluation.unknown_day_count,
        finding_count=evaluation.finding_count,
        replayed=replayed,
    )


def current_daily_quality_input_fingerprint(session: Session, evaluation: QualityEvaluation) -> str:
    """Recompute one stored daily conclusion's policy input fingerprint.

    This is read-only support for applicability checks. It uses the named
    evaluation's rule version and cutoff rather than selecting another policy
    or rewriting persisted evidence.
    """

    if (
        evaluation.evaluation_scope != DAILY_QUALITY_EVALUATION_SCOPE
        or evaluation.rule_set_name != DAILY_QUALITY_RULE_SET_NAME
        or evaluation.rule_set_version != DAILY_QUALITY_RULE_SET_VERSION
    ):
        raise DailyQualityEvaluationError(
            "UNSUPPORTED_DAILY_QUALITY_EVALUATION",
            "the stored quality evaluation does not use the supported daily rule set",
        )
    service = DailyQualityEvaluationService(session)
    series = service._load_daily_series(evaluation.series_id)
    timezone_value = _calendar_timezone(series.calendar.timezone_name)
    calendar_days = service._load_calendar_days(
        series.calendar_id,
        evaluation.trading_day_from,
        evaluation.trading_day_to,
    )
    sessions = service._load_sessions(
        series.calendar_id,
        evaluation.trading_day_from,
        evaluation.trading_day_to,
    )
    bars = service._load_bars(
        series.id,
        evaluation.trading_day_from,
        evaluation.trading_day_to,
        as_of=_as_utc(evaluation.as_of),
    )
    command = DailyQualityEvaluationCommand(
        series_id=evaluation.series_id,
        from_trading_day=evaluation.trading_day_from,
        to_trading_day=evaluation.trading_day_to,
        as_of=_as_utc(evaluation.as_of),
        idempotency_key=evaluation.idempotency_key,
        correlation_id=evaluation.correlation_id,
        causation_id=evaluation.causation_id,
    )
    return _input_fingerprint(
        command=command,
        series=series,
        calendar_days=calendar_days,
        sessions=sessions,
        bars=bars,
        timezone_value=timezone_value,
        session=session,
    )


def _as_utc(value: datetime) -> datetime:
    """Normalize one PostgreSQL authority timestamp to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise DailyQualityEvaluationError(
            "QUALITY_AUTHORITY_TIMESTAMP_INVALID",
            "authority timestamps must be timezone-aware",
        )
    return value.astimezone(UTC)


def _render_timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")
