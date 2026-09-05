"""Manual, append-only minute-session coverage evaluation.

The evaluator intentionally measures only historical point-in-time visibility:
whether committed one-minute BAR_START facts were available by an explicit
``as_of`` cutoff.  It neither polls a provider nor claims a live-data SLA.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.sql.elements import ColumnElement

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
    MAX_MINUTE_QUALITY_EXPECTED_SLOTS,
    MAX_MINUTE_QUALITY_OBSERVED_BARS,
    MAX_MINUTE_QUALITY_SESSION_ROWS,
    MINUTE_QUALITY_EVALUATION_SCOPE,
    MINUTE_QUALITY_RULE_SET_NAME,
    MINUTE_QUALITY_RULE_SET_VERSION,
    MinuteQualityEvaluationCommand,
    MinuteQualityEvaluationError,
    MinuteQualityEvaluationResult,
    validate_minute_quality_evaluation_command,
)

_ONE_MINUTE = timedelta(minutes=1)
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
    sessions: tuple[TradingSession, ...] = ()


@dataclass(frozen=True)
class _DayGrid:
    trading_day: date
    slots: tuple[datetime, ...]
    sessions: tuple[TradingSession, ...]
    final_close: datetime


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


class MinuteQualityEvaluationService:
    """Append one immutable complete-session 1m quality conclusion.

    The service reads only the committed canonical series window and its pinned
    calendar.  It does not fetch, parse, repair, publish, schedule, or expose
    data.  A later operator revalidation uses a new idempotency key and writes a
    new historical conclusion rather than changing this one.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def evaluate(self, command: MinuteQualityEvaluationCommand) -> MinuteQualityEvaluationResult:
        """Persist or safely replay one exact bounded minute-quality command."""

        command = validate_minute_quality_evaluation_command(command)
        if (
            self._session.in_transaction()
            or self._session.new
            or self._session.dirty
            or self._session.deleted
        ):
            raise MinuteQualityEvaluationError(
                "MINUTE_QUALITY_SESSION_NOT_CLEAN",
                "minute quality evaluation requires a clean, idle dedicated database session",
            )
        existing = self._session.scalar(
            select(QualityEvaluation).where(
                QualityEvaluation.idempotency_key == command.idempotency_key
            )
        )
        if existing is not None:
            return self._replay_or_reject(existing, command)

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

            _assert_cutoff_is_not_after_snapshot(command.as_of, self._session)
            series = self._load_minute_series(command.series_id)
            calendar = series.calendar
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
            try:
                interpreted_timezone = _calendar_timezone(calendar.timezone_name)
            except MinuteQualityEvaluationError:
                fingerprint_timezone: ZoneInfo | None = None
                bars = self._load_bars(
                    series.id,
                    command.from_trading_day,
                    command.to_trading_day,
                    sessions=sessions,
                    timezone_value=None,
                    as_of=command.as_of,
                )
                analysis = _calendar_timezone_unknown_analysis(command, sessions)
            else:
                fingerprint_timezone = interpreted_timezone
                bars = self._load_bars(
                    series.id,
                    command.from_trading_day,
                    command.to_trading_day,
                    sessions=sessions,
                    timezone_value=interpreted_timezone,
                    as_of=command.as_of,
                )
                analysis = _analyze_minute_quality(
                    command=command,
                    calendar_days=calendar_days,
                    sessions=sessions,
                    bars=bars,
                    timezone_value=interpreted_timezone,
                    db_session=self._session,
                )
            input_fingerprint = _input_fingerprint(
                command=command,
                series=series,
                calendar_days=calendar_days,
                sessions=sessions,
                bars=bars,
                timezone_value=fingerprint_timezone,
                db_session=self._session,
            )
            evaluation = QualityEvaluation(
                series_id=series.id,
                calendar_id=calendar.id,
                calendar_revision=calendar.revision,
                evaluation_scope=MINUTE_QUALITY_EVALUATION_SCOPE,
                rule_set_name=MINUTE_QUALITY_RULE_SET_NAME,
                rule_set_version=MINUTE_QUALITY_RULE_SET_VERSION,
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
                        evidence=_render_evidence(finding, as_of=command.as_of),
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
            raise MinuteQualityEvaluationError(
                "MINUTE_QUALITY_EVALUATION_RESERVATION_CONFLICT",
                "the minute quality evaluation reservation conflicted; "
                "retry the same command safely",
            ) from error
        except BaseException:
            self._session.rollback()
            raise

    def _begin_consistent_snapshot(self) -> None:
        """Use one repeatable PostgreSQL view without requiring a write lock."""

        self._session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))

    def _load_minute_series(self, series_id: UUID) -> DataSeries:
        series = self._session.scalar(
            select(DataSeries)
            .where(DataSeries.id == series_id)
            .options(joinedload(DataSeries.calendar))
        )
        if series is None:
            raise MinuteQualityEvaluationError(
                "MINUTE_QUALITY_EVALUATION_SERIES_NOT_FOUND",
                "the requested data series does not exist",
            )
        if series.interval != "1m" or series.timestamp_convention != "BAR_START":
            raise MinuteQualityEvaluationError(
                "MINUTE_QUALITY_REQUIRES_ONE_MINUTE_BAR_START_SERIES",
                "minute quality evaluation requires a one-minute BAR_START series",
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
        loaded = tuple(
            self._session.scalars(
                select(TradingSession)
                .where(
                    TradingSession.calendar_id == calendar_id,
                    TradingSession.trading_day.between(from_trading_day, to_trading_day),
                )
                .order_by(TradingSession.trading_day, TradingSession.sequence, TradingSession.id)
                .limit(MAX_MINUTE_QUALITY_SESSION_ROWS + 1)
            ).all()
        )
        if len(loaded) > MAX_MINUTE_QUALITY_SESSION_ROWS:
            raise MinuteQualityEvaluationError(
                "MINUTE_QUALITY_EVALUATION_INPUT_TOO_LARGE",
                "the minute quality evaluation exceeds its trading-session input bound",
            )
        return loaded

    def _load_bars(
        self,
        series_id: UUID,
        from_trading_day: date,
        to_trading_day: date,
        *,
        sessions: Sequence[TradingSession],
        timezone_value: ZoneInfo | None,
        as_of: datetime,
    ) -> tuple[CanonicalBar, ...]:
        conditions: list[ColumnElement[bool]] = [
            CanonicalBar.trading_day.between(from_trading_day, to_trading_day)
        ]
        event_bounds = (
            _session_event_bounds(sessions, timezone_value, self._session)
            if timezone_value is not None
            else None
        )
        if event_bounds is not None:
            lower, upper = event_bounds
            conditions.append(
                (CanonicalBar.event_time >= lower) & (CanonicalBar.event_time <= upper)
            )
        loaded = tuple(
            self._session.scalars(
                select(CanonicalBar)
                .where(CanonicalBar.series_id == series_id, or_(*conditions))
                .order_by(CanonicalBar.trading_day, CanonicalBar.event_time, CanonicalBar.id)
                .limit(MAX_MINUTE_QUALITY_OBSERVED_BARS + 1)
            ).all()
        )
        if len(loaded) > MAX_MINUTE_QUALITY_OBSERVED_BARS:
            raise MinuteQualityEvaluationError(
                "MINUTE_QUALITY_EVALUATION_INPUT_TOO_LARGE",
                "the minute quality evaluation exceeds its canonical-bar input bound",
            )
        try:
            return select_point_in_time_revisions(
                loaded,
                as_of=as_of,
                session=self._session,
                retain_earliest_future=True,
            )
        except ObservationRevisionError as error:
            raise MinuteQualityEvaluationError(
                "MINUTE_QUALITY_REVISION_CHAIN_INVALID",
                "the canonical observation revision chain cannot be interpreted safely",
            ) from error

    def _replay_or_reject(
        self,
        evaluation: QualityEvaluation,
        command: MinuteQualityEvaluationCommand,
    ) -> MinuteQualityEvaluationResult:
        if not _evaluation_matches_command(evaluation, command):
            self._session.rollback()
            raise MinuteQualityEvaluationError(
                "IDEMPOTENCY_KEY_REUSED",
                "the minute quality idempotency key was already used for a different intent",
            )
        result = _result_from_evaluation(evaluation, replayed=True)
        self._session.rollback()
        return result


def _analyze_minute_quality(
    *,
    command: MinuteQualityEvaluationCommand,
    calendar_days: Sequence[CalendarTradingDay],
    sessions: Sequence[TradingSession],
    bars: Sequence[CanonicalBar],
    timezone_value: ZoneInfo,
    db_session: Session,
) -> _EvaluationAnalysis:
    """Derive deterministic minute-quality findings from one committed snapshot."""

    calendar_by_day = {item.trading_day: item for item in calendar_days}
    sessions_by_day: dict[date, list[TradingSession]] = defaultdict(list)
    for trading_session in sessions:
        sessions_by_day[trading_session.trading_day].append(trading_session)
    bars_by_day: dict[date, list[CanonicalBar]] = defaultdict(list)
    for canonical_bar in bars:
        if command.from_trading_day <= canonical_bar.trading_day <= command.to_trading_day:
            bars_by_day[canonical_bar.trading_day].append(canonical_bar)

    calendar_unknown: list[date] = []
    open_without_session: list[date] = []
    session_grid_unknown: list[date] = []
    session_grid_unknown_sessions: list[TradingSession] = []
    incomplete_at_cutoff: list[date] = []
    closed_bars: list[CanonicalBar] = []
    timestamp_unknown_days: list[date] = []
    timestamp_unknown_bars: list[CanonicalBar] = []
    outside_grid_bars: list[CanonicalBar] = []
    trading_day_mismatch_bars: list[CanonicalBar] = []
    available_before_completion_bars: list[CanonicalBar] = []
    duplicate_slot_bars: list[CanonicalBar] = []
    available_after_cutoff_bars: list[CanonicalBar] = []
    missing_slots: list[datetime] = []
    covered_bars: list[CanonicalBar] = []

    all_grids: dict[date, _DayGrid] = {}
    remaining_grid_slots = MAX_MINUTE_QUALITY_EXPECTED_SLOTS

    for trading_day in _inclusive_days(command.from_trading_day, command.to_trading_day):
        calendar_day = calendar_by_day.get(trading_day)
        if calendar_day is None:
            calendar_unknown.append(trading_day)
            continue
        if calendar_day.status == "CLOSED":
            closed_bars.extend(bars_by_day.get(trading_day, ()))
            continue

        day_sessions = tuple(sessions_by_day.get(trading_day, ()))
        if not day_sessions:
            open_without_session.append(trading_day)
            continue
        grid = _build_day_grid(
            trading_day=trading_day,
            sessions=day_sessions,
            timezone_value=timezone_value,
            db_session=db_session,
            max_slot_count=remaining_grid_slots,
        )
        if grid is None:
            session_grid_unknown.append(trading_day)
            session_grid_unknown_sessions.extend(day_sessions)
            continue
        all_grids[trading_day] = grid
        remaining_grid_slots -= len(grid.slots)

    ambiguous_grid_days = _globally_ambiguous_grid_days(
        all_grids,
        timezone_value=timezone_value,
        db_session=db_session,
    )
    if ambiguous_grid_days:
        for trading_day in ambiguous_grid_days:
            grid = all_grids.pop(trading_day, None)
            if grid is not None:
                session_grid_unknown.append(trading_day)
                session_grid_unknown_sessions.extend(grid.sessions)

    slot_owners_by_key = _slot_owners_by_key(
        all_grids,
        timezone_value=timezone_value,
        db_session=db_session,
    )
    slot_collision_days = {
        trading_day
        for owners in slot_owners_by_key.values()
        if len(owners) > 1
        for trading_day, _ in owners
    }
    if slot_collision_days:
        for trading_day in slot_collision_days:
            grid = all_grids.pop(trading_day, None)
            if grid is not None:
                session_grid_unknown.append(trading_day)
                session_grid_unknown_sessions.extend(grid.sessions)
        slot_owners_by_key = _slot_owners_by_key(
            all_grids,
            timezone_value=timezone_value,
            db_session=db_session,
        )
    slot_by_key = {slot_key: owners[0] for slot_key, owners in slot_owners_by_key.items()}

    complete_grids: dict[date, _DayGrid] = {}
    for trading_day, grid in all_grids.items():
        if command.as_of < grid.final_close:
            incomplete_at_cutoff.append(trading_day)
            continue
        complete_grids[trading_day] = grid

    expected_slot_keys: set[str] = set()
    for grid in complete_grids.values():
        expected_slot_keys.update(_timestamp_key(slot) for slot in grid.slots)
    if len(expected_slot_keys) > MAX_MINUTE_QUALITY_EXPECTED_SLOTS:
        raise MinuteQualityEvaluationError(
            "MINUTE_QUALITY_EVALUATION_INPUT_TOO_LARGE",
            "the minute quality evaluation exceeds its expected-slot input bound",
        )

    valid_bars_by_slot: dict[str, list[CanonicalBar]] = defaultdict(list)
    event_bounds = _session_event_bounds(sessions, timezone_value, db_session)
    for bar in bars:
        try:
            event_time = _canonical_event_time(bar)
            available_at = _canonical_available_at(bar)
        except MinuteQualityEvaluationError:
            if command.from_trading_day <= bar.trading_day <= command.to_trading_day:
                timestamp_unknown_days.append(bar.trading_day)
            timestamp_unknown_bars.append(bar)
            continue

        is_declared_window_bar = (
            command.from_trading_day <= bar.trading_day <= command.to_trading_day
        )
        is_session_envelope_bar = (
            event_bounds is not None and event_bounds[0] <= event_time <= event_bounds[1]
        )
        if not is_declared_window_bar and not is_session_envelope_bar:
            continue

        slot_info = slot_by_key.get(_timestamp_key(event_time))
        if slot_info is None:
            # ``_load_bars`` includes both declared trading-day and the broad
            # session-time envelope.  A bar admitted by either path but mapping
            # to no expected slot is a known bad canonical fact even if its
            # declared trading day lies just outside the requested window.
            outside_grid_bars.append(bar)
            continue

        expected_day, owning_session = slot_info
        if bar.trading_day != expected_day:
            trading_day_mismatch_bars.append(bar)
            continue
        if event_time.second != 0 or event_time.microsecond != 0:
            outside_grid_bars.append(bar)
            continue
        session_close = _calendar_datetime(owning_session.closes_at)
        if event_time + _ONE_MINUTE > session_close:
            outside_grid_bars.append(bar)
            continue
        if available_at < event_time + _ONE_MINUTE:
            available_before_completion_bars.append(bar)
            continue
        valid_bars_by_slot[_timestamp_key(event_time)].append(bar)

    for slot_key, matching_bars in valid_bars_by_slot.items():
        if len(matching_bars) > 1:
            duplicate_slot_bars.extend(matching_bars)
            continue
        if slot_key not in expected_slot_keys:
            continue
        bar = matching_bars[0]
        available_at = _canonical_available_at(bar)
        if available_at <= command.as_of:
            covered_bars.append(bar)
        else:
            available_after_cutoff_bars.append(bar)

    covered_slot_keys = {_timestamp_key(_canonical_event_time(bar)) for bar in covered_bars}
    for grid in complete_grids.values():
        for slot in grid.slots:
            if _timestamp_key(slot) not in covered_slot_keys:
                missing_slots.append(slot)

    findings: list[_FindingSpec] = []
    if calendar_unknown:
        findings.append(
            _FindingSpec(
                rule_code="CALENDAR_DAY_UNKNOWN",
                outcome="UNKNOWN",
                severity="ERROR",
                trading_days=tuple(sorted(set(calendar_unknown))),
                occurrence_count=len(set(calendar_unknown)),
                evidence_reason="calendar_day_not_materialized",
            )
        )
    if open_without_session:
        findings.append(
            _FindingSpec(
                rule_code="OPEN_DAY_SESSION_UNKNOWN",
                outcome="UNKNOWN",
                severity="ERROR",
                trading_days=tuple(sorted(set(open_without_session))),
                occurrence_count=len(set(open_without_session)),
                evidence_reason="open_calendar_day_has_no_materialized_session",
            )
        )
    if session_grid_unknown:
        findings.append(
            _FindingSpec(
                rule_code="MINUTE_SESSION_GRID_UNKNOWN",
                outcome="UNKNOWN",
                severity="ERROR",
                trading_days=tuple(sorted(set(session_grid_unknown))),
                occurrence_count=len(set(session_grid_unknown)),
                evidence_reason="session_is_not_an_unambiguous_night_or_day_minute_grid",
                sessions=tuple(session_grid_unknown_sessions),
            )
        )
    if incomplete_at_cutoff:
        findings.append(
            _FindingSpec(
                rule_code="MINUTE_AS_OF_BEFORE_FINAL_SESSION_CLOSE",
                outcome="UNKNOWN",
                severity="ERROR",
                trading_days=tuple(sorted(set(incomplete_at_cutoff))),
                occurrence_count=len(set(incomplete_at_cutoff)),
                evidence_reason="evaluation_cutoff_precedes_trading_day_final_session_close",
            )
        )
    if timestamp_unknown_days:
        findings.append(
            _FindingSpec(
                rule_code="MINUTE_CANONICAL_TIMESTAMP_UNKNOWN",
                outcome="UNKNOWN",
                severity="ERROR",
                trading_days=tuple(sorted(set(timestamp_unknown_days))),
                occurrence_count=len(timestamp_unknown_bars),
                evidence_reason="canonical_bar_time_cannot_be_interpreted_without_guessing",
                bars=tuple(timestamp_unknown_bars),
            )
        )
    if closed_bars:
        findings.append(
            _FindingSpec(
                rule_code="CLOSED_DAY_HAS_MINUTE_OBSERVATION",
                outcome="FAIL",
                severity="ERROR",
                trading_days=tuple(sorted({bar.trading_day for bar in closed_bars})),
                occurrence_count=len(closed_bars),
                evidence_reason="canonical_minute_observation_exists_for_explicit_closed_day",
                bars=tuple(closed_bars),
            )
        )
    if trading_day_mismatch_bars:
        findings.append(
            _FindingSpec(
                rule_code="MINUTE_BAR_TRADING_DAY_MISMATCH",
                outcome="FAIL",
                severity="ERROR",
                trading_days=tuple(sorted({bar.trading_day for bar in trading_day_mismatch_bars})),
                occurrence_count=len(trading_day_mismatch_bars),
                evidence_reason="canonical_bar_trading_day_does_not_match_its_materialized_session",
                bars=tuple(trading_day_mismatch_bars),
            )
        )
    if outside_grid_bars:
        findings.append(
            _FindingSpec(
                rule_code="MINUTE_BAR_OUTSIDE_EXPECTED_GRID",
                outcome="FAIL",
                severity="ERROR",
                trading_days=tuple(sorted({bar.trading_day for bar in outside_grid_bars})),
                occurrence_count=len(outside_grid_bars),
                evidence_reason="canonical_bar_is_not_a_complete_expected_session_minute",
                bars=tuple(outside_grid_bars),
            )
        )
    if available_before_completion_bars:
        findings.append(
            _FindingSpec(
                rule_code="MINUTE_BAR_AVAILABLE_BEFORE_COMPLETE",
                outcome="FAIL",
                severity="ERROR",
                trading_days=tuple(
                    sorted({bar.trading_day for bar in available_before_completion_bars})
                ),
                occurrence_count=len(available_before_completion_bars),
                evidence_reason="canonical_bar_available_at_precedes_bar_completion",
                bars=tuple(available_before_completion_bars),
            )
        )
    if duplicate_slot_bars:
        findings.append(
            _FindingSpec(
                rule_code="MINUTE_SLOT_DUPLICATE",
                outcome="FAIL",
                severity="ERROR",
                trading_days=tuple(sorted({bar.trading_day for bar in duplicate_slot_bars})),
                occurrence_count=len(duplicate_slot_bars),
                evidence_reason="multiple_canonical_bars_map_to_one_expected_minute_slot",
                bars=tuple(duplicate_slot_bars),
            )
        )
    if available_after_cutoff_bars:
        findings.append(
            _FindingSpec(
                rule_code="MINUTE_BAR_AVAILABLE_AFTER_CUTOFF",
                outcome="FAIL",
                severity="ERROR",
                trading_days=tuple(
                    sorted({bar.trading_day for bar in available_after_cutoff_bars})
                ),
                occurrence_count=len(available_after_cutoff_bars),
                evidence_reason="expected_minute_observation_is_not_visible_at_evaluation_cutoff",
                bars=tuple(available_after_cutoff_bars),
            )
        )
    missing_without_visible_bar = [
        slot
        for slot in missing_slots
        if _timestamp_key(slot)
        not in {_timestamp_key(_canonical_event_time(bar)) for bar in available_after_cutoff_bars}
    ]
    if missing_without_visible_bar:
        findings.append(
            _FindingSpec(
                rule_code="MINUTE_COVERAGE_GAP",
                outcome="FAIL",
                severity="ERROR",
                trading_days=tuple(
                    sorted(
                        {
                            slot_by_key[_timestamp_key(slot)][0]
                            for slot in missing_without_visible_bar
                        }
                    )
                ),
                occurrence_count=len(missing_without_visible_bar),
                evidence_reason="expected_complete_session_minute_is_absent",
            )
        )
    if not findings:
        findings.append(
            _FindingSpec(
                rule_code="MINUTE_SESSION_COVERAGE_COMPLETE",
                outcome="PASS",
                severity="INFO",
                trading_days=tuple(sorted(complete_grids)),
                occurrence_count=len(covered_bars),
                evidence_reason="all_complete_open_session_minutes_are_visible_at_evaluation_cutoff",
                bars=tuple(covered_bars),
            )
        )

    return _EvaluationAnalysis(
        findings=tuple(findings),
        expected_observation_count=len(expected_slot_keys),
        covered_observation_count=len(covered_slot_keys),
        missing_observation_count=len(missing_slots),
        unknown_day_count=len(
            set(calendar_unknown)
            | set(open_without_session)
            | set(session_grid_unknown)
            | set(incomplete_at_cutoff)
            | set(timestamp_unknown_days)
        ),
    )


def _calendar_timezone_unknown_analysis(
    command: MinuteQualityEvaluationCommand, sessions: Sequence[TradingSession]
) -> _EvaluationAnalysis:
    """Persist a blocked conclusion when a pinned calendar zone is unusable."""

    trading_days = tuple(_inclusive_days(command.from_trading_day, command.to_trading_day))
    return _EvaluationAnalysis(
        findings=(
            _FindingSpec(
                rule_code="MINUTE_SESSION_GRID_UNKNOWN",
                outcome="UNKNOWN",
                severity="ERROR",
                trading_days=trading_days,
                occurrence_count=len(trading_days),
                evidence_reason="calendar_timezone_cannot_be_interpreted_without_guessing",
                sessions=tuple(sessions),
            ),
        ),
        expected_observation_count=0,
        covered_observation_count=0,
        missing_observation_count=0,
        unknown_day_count=len(trading_days),
    )


def _build_day_grid(
    *,
    trading_day: date,
    sessions: Sequence[TradingSession],
    timezone_value: ZoneInfo,
    db_session: Session,
    max_slot_count: int,
) -> _DayGrid | None:
    """Return a complete minute grid only when no session fact needs guessing."""

    normalized: list[tuple[datetime, datetime, TradingSession]] = []
    try:
        for item in sessions:
            if item.kind not in {"NIGHT", "DAY"}:
                return None
            opens_at = _calendar_datetime(item.opens_at)
            closes_at = _calendar_datetime(item.closes_at)
            if (
                opens_at >= closes_at
                or opens_at.second != 0
                or opens_at.microsecond != 0
                or closes_at.second != 0
                or closes_at.microsecond != 0
            ):
                return None
            normalized.append((opens_at, closes_at, item))
    except MinuteQualityEvaluationError:
        return None
    normalized.sort(key=lambda item: (item[0], item[1], item[2].sequence, item[2].id))
    if any(
        next_open < previous_close
        for (_, previous_close, _), (next_open, _, _) in zip(normalized, normalized[1:])
    ):
        return None

    session_slot_counts: list[int] = []
    total_slot_count = 0
    for opens_at, closes_at, _ in normalized:
        session_slot_count = int((closes_at - opens_at) / _ONE_MINUTE)
        total_slot_count += session_slot_count
        if total_slot_count > max_slot_count:
            raise MinuteQualityEvaluationError(
                "MINUTE_QUALITY_EVALUATION_INPUT_TOO_LARGE",
                "the minute quality evaluation exceeds its expected-session grid input bound",
            )
        session_slot_counts.append(session_slot_count)
    if not total_slot_count:
        return None

    slots: list[datetime] = []
    for (opens_at, _, _), session_slot_count in zip(normalized, session_slot_counts):
        current = opens_at
        for _ in range(session_slot_count):
            slots.append(current)
            current += _ONE_MINUTE
    return _DayGrid(
        trading_day=trading_day,
        slots=tuple(slots),
        sessions=tuple(item[2] for item in normalized),
        final_close=max(item[1] for item in normalized),
    )


def _grid_slots_with_sessions(
    grid: _DayGrid,
    *,
    timezone_value: ZoneInfo,
    db_session: Session,
) -> Iterable[tuple[datetime, TradingSession]]:
    """Associate each already-valid slot with its materialized source session."""

    normalized_sessions = tuple(
        (
            _calendar_datetime(item.opens_at),
            _calendar_datetime(item.closes_at),
            item,
        )
        for item in grid.sessions
    )
    for slot in grid.slots:
        for opens_at, closes_at, item in normalized_sessions:
            if opens_at <= slot and slot + _ONE_MINUTE <= closes_at:
                yield slot, item
                break


def _globally_ambiguous_grid_days(
    grids: dict[date, _DayGrid],
    *,
    timezone_value: ZoneInfo,
    db_session: Session,
) -> set[date]:
    """Find cross-trading-day session overlaps that make ownership ambiguous."""

    intervals: list[tuple[datetime, datetime, date]] = []
    for trading_day, grid in grids.items():
        for trading_session in grid.sessions:
            intervals.append(
                (
                    _calendar_datetime(trading_session.opens_at),
                    _calendar_datetime(trading_session.closes_at),
                    trading_day,
                )
            )
    intervals.sort(key=lambda item: (item[0], item[1], item[2]))
    ambiguous_days: set[date] = set()
    active: list[tuple[datetime, date]] = []
    for opens_at, closes_at, trading_day in intervals:
        active = [item for item in active if item[0] > opens_at]
        for _, active_trading_day in active:
            if active_trading_day != trading_day:
                ambiguous_days.add(active_trading_day)
                ambiguous_days.add(trading_day)
        active.append((closes_at, trading_day))
    return ambiguous_days


def _slot_owners_by_key(
    grids: dict[date, _DayGrid],
    *,
    timezone_value: ZoneInfo,
    db_session: Session,
) -> dict[str, list[tuple[date, TradingSession]]]:
    """Retain every candidate owner so an ambiguous slot can never overwrite one."""

    owners_by_key: dict[str, list[tuple[date, TradingSession]]] = defaultdict(list)
    for trading_day, grid in grids.items():
        for slot, owning_session in _grid_slots_with_sessions(
            grid,
            timezone_value=timezone_value,
            db_session=db_session,
        ):
            owners_by_key[_timestamp_key(slot)].append((trading_day, owning_session))
    return owners_by_key


def _session_event_bounds(
    sessions: Sequence[TradingSession], timezone_value: ZoneInfo, db_session: Session
) -> tuple[datetime, datetime] | None:
    """Return a broad session event-time envelope for detecting misassigned bars."""

    if not sessions:
        return None
    try:
        opens_at = [_calendar_datetime(item.opens_at) for item in sessions]
        closes_at = [_calendar_datetime(item.closes_at) for item in sessions]
    except MinuteQualityEvaluationError:
        return None
    return min(opens_at), max(closes_at)


def _input_fingerprint(
    *,
    command: MinuteQualityEvaluationCommand,
    series: DataSeries,
    calendar_days: Sequence[CalendarTradingDay],
    sessions: Sequence[TradingSession],
    bars: Sequence[CanonicalBar],
    timezone_value: ZoneInfo | None,
    db_session: Session,
) -> str:
    """Hash only deterministic, non-secret facts used by the fixed policy."""

    rendered = json.dumps(
        {
            "evaluation_scope": MINUTE_QUALITY_EVALUATION_SCOPE,
            "rule_set": f"{MINUTE_QUALITY_RULE_SET_NAME}/{MINUTE_QUALITY_RULE_SET_VERSION}",
            "series_id": str(series.id),
            "series_interval": series.interval,
            "timestamp_convention": series.timestamp_convention,
            "calendar_id": str(series.calendar_id),
            "calendar_revision": series.calendar.revision,
            "calendar_timezone_name": series.calendar.timezone_name,
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
                    item.kind,
                    _fingerprint_calendar_timestamp(item.opens_at, timezone_value, db_session),
                    _fingerprint_calendar_timestamp(item.closes_at, timezone_value, db_session),
                ]
                for item in sorted(
                    sessions, key=lambda item: (item.trading_day, item.sequence, item.id)
                )
            ],
            "bars": [
                [
                    str(item.id),
                    _fingerprint_canonical_timestamp(item.event_time),
                    item.trading_day.isoformat(),
                    _fingerprint_canonical_timestamp(item.available_at),
                    item.source_timezone_name,
                    item.normalized_payload_hash,
                    str(item.import_run_id) if item.import_run_id is not None else None,
                ]
                for item in sorted(
                    bars,
                    key=lambda item: (item.trading_day, str(item.event_time), item.id),
                )
            ],
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _render_evidence(finding: _FindingSpec, *, as_of: datetime) -> str:
    """Render bounded aggregate evidence without source values or free text."""

    bar_ids = sorted({str(bar.id) for bar in finding.bars})[:_MAX_EVIDENCE_SAMPLE_IDENTIFIERS]
    import_run_ids = sorted(
        {str(bar.import_run_id) for bar in finding.bars if bar.import_run_id is not None}
    )[:_MAX_EVIDENCE_SAMPLE_IDENTIFIERS]
    session_ids = sorted({str(item.id) for item in finding.sessions})[
        :_MAX_EVIDENCE_SAMPLE_IDENTIFIERS
    ]
    payload: dict[str, object] = {
        "evidence_version": f"{MINUTE_QUALITY_RULE_SET_NAME}/{MINUTE_QUALITY_RULE_SET_VERSION}",
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
    if session_ids:
        payload["sample_trading_session_ids"] = session_ids
    rendered = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(rendered) > _MAX_EVIDENCE_BYTES:  # pragma: no cover - fixed samples are defensive
        raise MinuteQualityEvaluationError(
            "MINUTE_QUALITY_EVIDENCE_TOO_LARGE",
            "the minute quality evidence exceeded its bounded storage limit",
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


def _assert_cutoff_is_not_after_snapshot(as_of: datetime, db_session: Session) -> None:
    """Reject an unverifiable future cutoff using the authority database clock."""

    snapshot_now = db_session.scalar(select(func.current_timestamp()))
    if not isinstance(snapshot_now, datetime):  # pragma: no cover
        raise MinuteQualityEvaluationError(
            "MINUTE_QUALITY_AUTHORITY_TIME_UNAVAILABLE",
            "the authoritative database did not return an interpretable current timestamp",
        )
    if as_of > _as_utc(snapshot_now):
        raise MinuteQualityEvaluationError(
            "MINUTE_QUALITY_EVALUATION_AS_OF_FUTURE",
            "as_of must not be after the authoritative database snapshot time",
        )


def _calendar_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise MinuteQualityEvaluationError(
            "MINUTE_QUALITY_CALENDAR_TIMEZONE_INVALID",
            "the pinned calendar does not contain a usable IANA timezone",
        ) from error


def _calendar_datetime(value: datetime) -> datetime:
    """Require an unambiguous timestamp from the PostgreSQL authority."""

    if value.tzinfo is not None and value.utcoffset() is not None:
        return value
    raise MinuteQualityEvaluationError(
        "MINUTE_QUALITY_CALENDAR_TIMESTAMP_INVALID",
        "the pinned calendar contains a timezone-naive timestamp",
    )


def _canonical_event_time(bar: CanonicalBar) -> datetime:
    """Recover the declared source instant for a 1m event without guessing."""

    return _canonical_timestamp(bar.event_time, "event_time")


def _canonical_available_at(bar: CanonicalBar) -> datetime:
    """Recover a canonical observation's source publication instant safely."""

    return _canonical_timestamp(bar.available_at, "available_at")


def _canonical_timestamp(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value
    raise MinuteQualityEvaluationError(
        "MINUTE_QUALITY_CANONICAL_TIMESTAMP_INVALID",
        f"the canonical observation contains a timezone-naive {field_name}",
    )


def _fingerprint_calendar_timestamp(
    value: datetime, timezone_value: ZoneInfo | None, db_session: Session
) -> str:
    """Retain an uninterpretable stored time in the fingerprint without guessing."""

    if timezone_value is None:
        return f"UNINTERPRETABLE:{value.isoformat()}"
    try:
        return _render_timestamp(_calendar_datetime(value))
    except MinuteQualityEvaluationError:
        return f"UNINTERPRETABLE:{value.isoformat()}"


def _fingerprint_canonical_timestamp(value: datetime) -> str:
    """Retain invalid persisted source-time evidence for an UNKNOWN conclusion."""

    try:
        return _render_timestamp(_canonical_timestamp(value, "timestamp"))
    except MinuteQualityEvaluationError:
        return f"UNINTERPRETABLE:{value.isoformat()}"


def _evaluation_matches_command(
    evaluation: QualityEvaluation,
    command: MinuteQualityEvaluationCommand,
) -> bool:
    return (
        evaluation.series_id == command.series_id
        and evaluation.evaluation_scope == MINUTE_QUALITY_EVALUATION_SCOPE
        and evaluation.rule_set_name == MINUTE_QUALITY_RULE_SET_NAME
        and evaluation.rule_set_version == MINUTE_QUALITY_RULE_SET_VERSION
        and evaluation.trading_day_from == command.from_trading_day
        and evaluation.trading_day_to == command.to_trading_day
        and _as_utc(evaluation.as_of) == command.as_of
        and evaluation.correlation_id == command.correlation_id
        and evaluation.causation_id == command.causation_id
    )


def _result_from_evaluation(
    evaluation: QualityEvaluation, *, replayed: bool
) -> MinuteQualityEvaluationResult:
    return MinuteQualityEvaluationResult(
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


def current_minute_quality_input_fingerprint(
    session: Session, evaluation: QualityEvaluation
) -> str:
    """Recompute one stored minute conclusion's fixed-policy input fingerprint."""

    if (
        evaluation.evaluation_scope != MINUTE_QUALITY_EVALUATION_SCOPE
        or evaluation.rule_set_name != MINUTE_QUALITY_RULE_SET_NAME
        or evaluation.rule_set_version != MINUTE_QUALITY_RULE_SET_VERSION
    ):
        raise MinuteQualityEvaluationError(
            "UNSUPPORTED_MINUTE_QUALITY_EVALUATION",
            "the stored quality evaluation does not use the supported minute rule set",
        )
    service = MinuteQualityEvaluationService(session)
    series = service._load_minute_series(evaluation.series_id)
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
    try:
        timezone_value = _calendar_timezone(series.calendar.timezone_name)
    except MinuteQualityEvaluationError:
        timezone_value = None
    bars = service._load_bars(
        series.id,
        evaluation.trading_day_from,
        evaluation.trading_day_to,
        sessions=sessions,
        timezone_value=timezone_value,
        as_of=_as_utc(evaluation.as_of),
    )
    command = MinuteQualityEvaluationCommand(
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
        db_session=session,
    )


def _timestamp_key(value: datetime) -> str:
    return _render_timestamp(value)


def _as_utc(value: datetime) -> datetime:
    """Normalize one PostgreSQL authority timestamp to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise MinuteQualityEvaluationError(
            "MINUTE_QUALITY_AUTHORITY_TIMESTAMP_INVALID",
            "authority timestamps must be timezone-aware",
        )
    return value.astimezone(UTC)


def _render_timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")
