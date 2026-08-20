"""交易日历的离线、版本化事实模型。

本模块只保存已经取得的日历事实，不读取网络、当前时钟或 ``exchange_calendars``。中国
商品期货的夜盘、休市和品种差异必须由显式 ``CalendarSession`` 表达；不能由工作日规则
补全。快照的 ``available_at`` 是所有查询的 point-in-time 可见性边界。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from enum import Enum
import re
from typing import Iterable, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from northstar_quant.data_platform.artifacts.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
    require_sha256,
)


class CalendarError(ValueError):
    """日历事实缺失、不一致或不满足安全约束。"""


class CalendarQualityStatus(str, Enum):
    """日历快照的质量结论；只有 ``PASS`` 可作为开市事实。"""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"


class CalendarDecisionStatus(str, Enum):
    """日历查询的 fail-closed 结论。"""

    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"
    NOT_YET_AVAILABLE = "not_yet_available"
    AMBIGUOUS = "ambiguous"


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_REASON_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_STABLE_PRODUCT_CODE_RE = re.compile(r"^[A-Z]+$")


@dataclass(frozen=True, slots=True)
class CalendarSession:
    """一个明确的、绝对时间表示的品种交易会话。

    ``instrument_id`` 是稳定品种身份（例如 ``SHFE.RB``），不是 ``RB2610`` 等实际月份
    合约。``trading_day`` 也不等同于 ``opens_at`` 的自然日。例如星期日夜盘若存在，应明确
    写成次一交易日的 ``trading_day``，而不是由调用方自行计算。
    """

    exchange_id: str
    instrument_id: str
    trading_day: date
    opens_at: datetime
    closes_at: datetime
    session_id: str

    def __post_init__(self) -> None:
        exchange_id = _identifier(self.exchange_id, "session.exchange_id")
        object.__setattr__(self, "exchange_id", exchange_id)
        object.__setattr__(
            self,
            "instrument_id",
            _stable_instrument_id(
                self.instrument_id,
                "session.instrument_id",
                exchange_id=exchange_id,
            ),
        )
        object.__setattr__(self, "session_id", _identifier(self.session_id, "session.session_id"))
        _require_date(self.trading_day, "session.trading_day")
        _require_aware_datetime(self.opens_at, "session.opens_at")
        _require_aware_datetime(self.closes_at, "session.closes_at")
        if self.opens_at >= self.closes_at:
            raise CalendarError("session.opens_at 必须早于 session.closes_at")


@dataclass(frozen=True, slots=True)
class TradingCalendarSnapshot:
    """一份有来源、可回放的交易所日历快照。

    覆盖范围内的每一个自然日都必须显式出现在 ``trading_days`` 或 ``closed_dates`` 中。
    因此缺失日期不会被误判为开市或休市。品种层面只有在该交易日有明确会话时才被视为
    已知可交易。
    """

    calendar_id: str
    exchange_id: str
    timezone_name: str
    observed_at: datetime
    available_at: datetime
    coverage_start: date
    coverage_end: date
    source_artifact_hash: str
    quality_status: CalendarQualityStatus
    trading_days: tuple[date, ...]
    closed_dates: tuple[date, ...]
    sessions: tuple[CalendarSession, ...]
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "calendar_id", _identifier(self.calendar_id, "calendar_id"))
        object.__setattr__(self, "exchange_id", _identifier(self.exchange_id, "exchange_id"))
        timezone_name = _required_text(self.timezone_name, "timezone_name")
        try:
            calendar_timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise CalendarError("timezone_name 必须是有效 IANA 时区") from exc
        object.__setattr__(self, "timezone_name", timezone_name)

        observed_at = _as_utc(self.observed_at, "observed_at")
        available_at = _as_utc(self.available_at, "available_at")
        if available_at < observed_at:
            raise CalendarError("available_at 不能早于 observed_at")
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "available_at", available_at)

        _require_date(self.coverage_start, "coverage_start")
        _require_date(self.coverage_end, "coverage_end")
        if self.coverage_end < self.coverage_start:
            raise CalendarError("coverage_end 不能早于 coverage_start")

        try:
            source_artifact_hash = require_sha256(
                self.source_artifact_hash,
                field_name="source_artifact_hash",
            )
        except FingerprintError as exc:
            raise CalendarError("source_artifact_hash 必须是来源制品的 SHA-256") from exc
        object.__setattr__(self, "source_artifact_hash", source_artifact_hash)

        if not isinstance(self.quality_status, CalendarQualityStatus):
            raise CalendarError("quality_status 必须是 CalendarQualityStatus")

        trading_days = _canonical_dates(self.trading_days, "trading_days")
        closed_dates = _canonical_dates(self.closed_dates, "closed_dates")
        _validate_coverage_partition(
            coverage_start=self.coverage_start,
            coverage_end=self.coverage_end,
            trading_days=trading_days,
            closed_dates=closed_dates,
        )
        object.__setattr__(self, "trading_days", trading_days)
        object.__setattr__(self, "closed_dates", closed_dates)

        sessions = _canonical_sessions(
            self.sessions,
            exchange_id=self.exchange_id,
            calendar_timezone=calendar_timezone,
            coverage_start=self.coverage_start,
            coverage_end=self.coverage_end,
            trading_days=frozenset(trading_days),
        )
        object.__setattr__(self, "sessions", sessions)
        object.__setattr__(self, "content_hash", _snapshot_content_hash(self))

    @classmethod
    def create(
        cls,
        *,
        calendar_id: str,
        exchange_id: str,
        timezone_name: str,
        observed_at: datetime,
        available_at: datetime,
        coverage_start: date,
        coverage_end: date,
        source_artifact_hash: str,
        quality_status: CalendarQualityStatus,
        trading_days: Iterable[date],
        closed_dates: Iterable[date],
        sessions: Iterable[CalendarSession],
    ) -> TradingCalendarSnapshot:
        """构造并计算内容哈希，禁止调用方手工提供快照身份。"""

        return cls(
            calendar_id=calendar_id,
            exchange_id=exchange_id,
            timezone_name=timezone_name,
            observed_at=observed_at,
            available_at=available_at,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            source_artifact_hash=source_artifact_hash,
            quality_status=quality_status,
            trading_days=tuple(trading_days),
            closed_dates=tuple(closed_dates),
            sessions=tuple(sessions),
        )

    @property
    def snapshot_hash(self) -> str:
        """供决策记录使用的不可变快照身份。"""

        return self.content_hash

    def covers(self, value: date) -> bool:
        """返回一个自然日是否在该快照的显式覆盖范围内。"""

        _require_date(value, "coverage query date")
        return self.coverage_start <= value <= self.coverage_end


@dataclass(frozen=True, slots=True)
class CalendarDecision:
    """一次可审计、不可变的日历裁决。"""

    status: CalendarDecisionStatus
    reason_code: str
    exchange_id: str
    instrument_id: str
    decision_at: datetime
    trading_day: date | None
    snapshot_hash: str | None
    market_at: datetime | None = None
    session: CalendarSession | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, CalendarDecisionStatus):
            raise CalendarError("decision.status 必须是 CalendarDecisionStatus")
        if not isinstance(self.reason_code, str) or _REASON_CODE_RE.fullmatch(self.reason_code) is None:
            raise CalendarError("decision.reason_code 必须是大写稳定原因码")
        exchange_id = _identifier(self.exchange_id, "decision.exchange_id")
        object.__setattr__(self, "exchange_id", exchange_id)
        object.__setattr__(
            self,
            "instrument_id",
            _stable_instrument_id(
                self.instrument_id,
                "decision.instrument_id",
                exchange_id=exchange_id,
            ),
        )
        object.__setattr__(self, "decision_at", _as_utc(self.decision_at, "decision.decision_at"))
        if self.trading_day is not None:
            _require_date(self.trading_day, "decision.trading_day")
        if self.market_at is not None:
            _require_aware_datetime(self.market_at, "decision.market_at")
            object.__setattr__(self, "market_at", self.market_at.astimezone(UTC))
        if self.snapshot_hash is not None:
            try:
                object.__setattr__(
                    self,
                    "snapshot_hash",
                    require_sha256(self.snapshot_hash, field_name="decision.snapshot_hash"),
                )
            except FingerprintError as exc:
                raise CalendarError("decision.snapshot_hash 必须是 SHA-256") from exc
        if self.session is not None:
            if not isinstance(self.session, CalendarSession):
                raise CalendarError("decision.session 必须是 CalendarSession")
            if (
                self.session.exchange_id != self.exchange_id
                or self.session.instrument_id != self.instrument_id
            ):
                raise CalendarError("decision.session 必须与裁决的交易所和稳定品种身份一致")

    @property
    def is_open(self) -> bool:
        """只有明确的 ``OPEN`` 才表示可在日历层进入下一道安全门。"""

        return self.status is CalendarDecisionStatus.OPEN


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise CalendarError(f"{field_name} 必须是非空标识符")
    normalized = value.strip().upper()
    if _IDENTIFIER_RE.fullmatch(normalized) is None:
        raise CalendarError(f"{field_name} 必须是非空标识符")
    return normalized


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalendarError(f"{field_name} 必须是非空字符串")
    return value.strip()


def _stable_instrument_id(
    value: object,
    field_name: str,
    *,
    exchange_id: str,
) -> str:
    """规范化 ``EXCHANGE.PRODUCT`` 稳定品种身份并绑定交易所。"""

    normalized = _identifier(value, field_name)
    expected_prefix = f"{exchange_id}."
    product_code = normalized.removeprefix(expected_prefix)
    if (
        not normalized.startswith(expected_prefix)
        or _STABLE_PRODUCT_CODE_RE.fullmatch(product_code) is None
    ):
        raise CalendarError(
            f"{field_name} 必须是与 {exchange_id} 一致的稳定品种身份 EXCHANGE.PRODUCT，"
            "不能是实际月份合约"
        )
    return normalized


def _require_date(value: object, field_name: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise CalendarError(f"{field_name} 必须是 date")
    return value


def _require_aware_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CalendarError(f"{field_name} 必须是含时区的 datetime")
    return value


def _as_utc(value: object, field_name: str) -> datetime:
    return _require_aware_datetime(value, field_name).astimezone(UTC)


def _canonical_dates(values: object, field_name: str) -> tuple[date, ...]:
    if isinstance(values, (str, bytes)):
        raise CalendarError(f"{field_name} 必须是 date 序列")
    try:
        dates: tuple[date, ...] = tuple(
            _require_date(item, field_name) for item in cast(Iterable[object], values)
        )
    except TypeError as exc:
        raise CalendarError(f"{field_name} 必须是 date 序列") from exc
    if len(set(dates)) != len(dates):
        raise CalendarError(f"{field_name} 不能包含重复日期")
    return tuple(sorted(dates))


def _validate_coverage_partition(
    *,
    coverage_start: date,
    coverage_end: date,
    trading_days: tuple[date, ...],
    closed_dates: tuple[date, ...],
) -> None:
    classified = set(trading_days) | set(closed_dates)
    if set(trading_days) & set(closed_dates):
        raise CalendarError("trading_days 与 closed_dates 不能重叠")
    if any(item < coverage_start or item > coverage_end for item in classified):
        raise CalendarError("trading_days 或 closed_dates 超出 coverage")
    coverage_size = (coverage_end - coverage_start).days + 1
    if len(classified) != coverage_size:
        raise CalendarError("coverage 内每个自然日必须显式列入 trading_days 或 closed_dates")


def _canonical_sessions(
    values: object,
    *,
    exchange_id: str,
    calendar_timezone: ZoneInfo,
    coverage_start: date,
    coverage_end: date,
    trading_days: frozenset[date],
) -> tuple[CalendarSession, ...]:
    if isinstance(values, (str, bytes)):
        raise CalendarError("sessions 必须是 CalendarSession 序列")
    try:
        raw_sessions: tuple[object, ...] = tuple(cast(Iterable[object], values))
    except TypeError as exc:
        raise CalendarError("sessions 必须是 CalendarSession 序列") from exc
    if not all(isinstance(item, CalendarSession) for item in raw_sessions):
        raise CalendarError("sessions 必须是 CalendarSession 序列")
    typed_sessions = tuple(cast(CalendarSession, item) for item in raw_sessions)

    sessions = tuple(
        replace(
            item,
            opens_at=item.opens_at.astimezone(calendar_timezone),
            closes_at=item.closes_at.astimezone(calendar_timezone),
        )
        for item in typed_sessions
    )
    for session in sessions:
        if session.exchange_id != exchange_id:
            raise CalendarError("session.exchange_id 必须与快照 exchange_id 一致")
        if session.trading_day not in trading_days:
            raise CalendarError("session.trading_day 必须是快照显式 trading_day")
        _validate_session_coverage(
            session,
            calendar_timezone,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
        )

    session_ids = {
        (item.instrument_id, item.trading_day, item.session_id)
        for item in sessions
    }
    if len(session_ids) != len(sessions):
        raise CalendarError("同一品种/交易日的 session_id 不能重复")
    _validate_no_session_overlap(sessions)
    return tuple(
        sorted(
            sessions,
            key=lambda item: (
                item.instrument_id,
                item.opens_at,
                item.closes_at,
                item.trading_day,
                item.session_id,
            ),
        )
    )


def _validate_session_coverage(
    session: CalendarSession,
    calendar_timezone: ZoneInfo,
    *,
    coverage_start: date,
    coverage_end: date,
) -> None:
    opens_local = session.opens_at.astimezone(calendar_timezone)
    closes_local = session.closes_at.astimezone(calendar_timezone)
    if opens_local.date() < coverage_start or closes_local.date() > coverage_end:
        raise CalendarError("session 绝对时间超出快照 coverage")


def _validate_no_session_overlap(sessions: tuple[CalendarSession, ...]) -> None:
    by_instrument: dict[str, list[CalendarSession]] = {}
    for session in sessions:
        by_instrument.setdefault(session.instrument_id, []).append(session)
    for instrument_sessions in by_instrument.values():
        ordered = sorted(instrument_sessions, key=lambda item: (item.opens_at, item.closes_at))
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current.opens_at < previous.closes_at:
                raise CalendarError("同一品种的 session 绝对时间不能重叠")


def _snapshot_content_hash(snapshot: TradingCalendarSnapshot) -> str:
    return canonical_json_sha256(
        {
            "available_at": snapshot.available_at.isoformat(),
            "calendar_id": snapshot.calendar_id,
            "closed_dates": [item.isoformat() for item in snapshot.closed_dates],
            "coverage_end": snapshot.coverage_end.isoformat(),
            "coverage_start": snapshot.coverage_start.isoformat(),
            "exchange_id": snapshot.exchange_id,
            "observed_at": snapshot.observed_at.isoformat(),
            "quality_status": snapshot.quality_status.value,
            "sessions": [
                {
                    "closes_at": item.closes_at.isoformat(),
                    "exchange_id": item.exchange_id,
                    "instrument_id": item.instrument_id,
                    "opens_at": item.opens_at.isoformat(),
                    "session_id": item.session_id,
                    "trading_day": item.trading_day.isoformat(),
                }
                for item in snapshot.sessions
            ],
            "source_artifact_hash": snapshot.source_artifact_hash,
            "timezone_name": snapshot.timezone_name,
            "trading_days": [item.isoformat() for item in snapshot.trading_days],
        }
    )
