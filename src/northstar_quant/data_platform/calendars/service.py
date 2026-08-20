"""基于不可变日历快照的纯离线查询服务。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import re
from typing import Iterable
from zoneinfo import ZoneInfo

from northstar_quant.data_platform.calendars.models import (
    CalendarDecision,
    CalendarDecisionStatus,
    CalendarError,
    CalendarQualityStatus,
    CalendarSession,
    TradingCalendarSnapshot,
)


class CalendarService:
    """按 ``decision_at`` 重放可见日历事实的服务。

    服务本身不读文件、不访问网络，也不读取当前时钟。调用方必须明确给出查询时点和决策
    时点，因而研究回放与未来执行路径都不会因运行机器的日期或第三方日历版本漂移。
    """

    def __init__(self, snapshots: Iterable[TradingCalendarSnapshot]) -> None:
        values = tuple(snapshots)
        if not all(isinstance(item, TradingCalendarSnapshot) for item in values):
            raise CalendarError("snapshots 必须是 TradingCalendarSnapshot 序列")
        snapshot_hashes = [item.snapshot_hash for item in values]
        if len(set(snapshot_hashes)) != len(snapshot_hashes):
            raise CalendarError("snapshots 不能重复引用相同快照")
        self._snapshots = tuple(
            sorted(
                values,
                key=lambda item: (
                    item.exchange_id,
                    item.available_at,
                    item.coverage_start,
                    item.coverage_end,
                    item.snapshot_hash,
                ),
            )
        )

    def snapshots_as_of(self, decision_at: datetime) -> tuple[TradingCalendarSnapshot, ...]:
        """仅返回在明确 PIT 时点已经可见的不可变快照。"""

        visible_at = _as_utc(decision_at, "decision_at")
        return tuple(item for item in self._snapshots if item.available_at <= visible_at)

    def resolve_market_session(
        self,
        exchange_id: str,
        instrument_id: str,
        at: datetime,
        decision_at: datetime,
    ) -> CalendarDecision:
        """解析一个绝对时点是否落在明确声明的品种会话中。

        夜盘不从小时数推断。查询会同时考虑本地自然日与下一日，以覆盖“前夜开始、归属下一
        交易日”的显式会话；若没有相应快照或品种会话，则返回非 ``OPEN`` 的结论。
        """

        normalized_exchange = _identifier(exchange_id, "exchange_id")
        normalized_instrument = _stable_instrument_id(
            instrument_id,
            "instrument_id",
            exchange_id=normalized_exchange,
        )
        market_at = _as_utc(at, "at")
        normalized_decision_at = _as_utc(decision_at, "decision_at")

        matching_exchange = [
            item for item in self._snapshots if item.exchange_id == normalized_exchange
        ]
        if not matching_exchange:
            return _decision(
                status=CalendarDecisionStatus.UNKNOWN,
                reason_code="EXCHANGE_CALENDAR_UNKNOWN",
                exchange_id=normalized_exchange,
                instrument_id=normalized_instrument,
                decision_at=normalized_decision_at,
                market_at=market_at,
                trading_day=None,
                snapshot_hash=None,
            )

        candidate_dates = _candidate_local_dates(market_at, matching_exchange)
        selected = self._select_snapshot(
            exchange_id=normalized_exchange,
            instrument_id=normalized_instrument,
            candidate_dates=candidate_dates,
            decision_at=normalized_decision_at,
            market_at=market_at,
        )
        if isinstance(selected, CalendarDecision):
            return selected

        sessions = tuple(
            item
            for item in selected.sessions
            if item.instrument_id == normalized_instrument
            and item.opens_at <= market_at < item.closes_at
        )
        if len(sessions) > 1:
            return _decision(
                status=CalendarDecisionStatus.AMBIGUOUS,
                reason_code="OVERLAPPING_MARKET_SESSIONS",
                exchange_id=normalized_exchange,
                instrument_id=normalized_instrument,
                decision_at=normalized_decision_at,
                market_at=market_at,
                trading_day=None,
                snapshot_hash=selected.snapshot_hash,
            )
        if sessions:
            session = sessions[0]
            return _decision(
                status=CalendarDecisionStatus.OPEN,
                reason_code="MARKET_SESSION_OPEN",
                exchange_id=normalized_exchange,
                instrument_id=normalized_instrument,
                decision_at=normalized_decision_at,
                market_at=market_at,
                trading_day=session.trading_day,
                snapshot_hash=selected.snapshot_hash,
                session=session,
            )

        local_day = market_at.astimezone(ZoneInfo(selected.timezone_name)).date()
        if not selected.covers(local_day):
            return _decision(
                status=CalendarDecisionStatus.UNKNOWN,
                reason_code="LOCAL_DATE_OUTSIDE_COVERAGE",
                exchange_id=normalized_exchange,
                instrument_id=normalized_instrument,
                decision_at=normalized_decision_at,
                market_at=market_at,
                trading_day=local_day,
                snapshot_hash=selected.snapshot_hash,
            )
        if local_day in selected.closed_dates:
            return _decision(
                status=CalendarDecisionStatus.CLOSED,
                reason_code="EXCHANGE_CLOSED_DATE",
                exchange_id=normalized_exchange,
                instrument_id=normalized_instrument,
                decision_at=normalized_decision_at,
                market_at=market_at,
                trading_day=local_day,
                snapshot_hash=selected.snapshot_hash,
            )
        if not _sessions_for_day(selected, normalized_instrument, local_day):
            return _decision(
                status=CalendarDecisionStatus.UNKNOWN,
                reason_code="INSTRUMENT_SESSION_UNKNOWN",
                exchange_id=normalized_exchange,
                instrument_id=normalized_instrument,
                decision_at=normalized_decision_at,
                market_at=market_at,
                trading_day=local_day,
                snapshot_hash=selected.snapshot_hash,
            )
        return _decision(
            status=CalendarDecisionStatus.CLOSED,
            reason_code="OUTSIDE_DECLARED_SESSION",
            exchange_id=normalized_exchange,
            instrument_id=normalized_instrument,
            decision_at=normalized_decision_at,
            market_at=market_at,
            trading_day=local_day,
            snapshot_hash=selected.snapshot_hash,
        )

    def is_trading_day(
        self,
        exchange_id: str,
        instrument_id: str,
        trading_day: date,
        decision_at: datetime,
    ) -> CalendarDecision:
        """判断某品种在一个明确的交易日是否拥有已知会话。"""

        normalized_exchange = _identifier(exchange_id, "exchange_id")
        normalized_instrument = _stable_instrument_id(
            instrument_id,
            "instrument_id",
            exchange_id=normalized_exchange,
        )
        _require_date(trading_day, "trading_day")
        normalized_decision_at = _as_utc(decision_at, "decision_at")
        selected = self._select_snapshot(
            exchange_id=normalized_exchange,
            instrument_id=normalized_instrument,
            candidate_dates=(trading_day,),
            decision_at=normalized_decision_at,
            market_at=None,
        )
        if isinstance(selected, CalendarDecision):
            return selected
        if trading_day in selected.closed_dates:
            return _decision(
                status=CalendarDecisionStatus.CLOSED,
                reason_code="EXCHANGE_CLOSED_DATE",
                exchange_id=normalized_exchange,
                instrument_id=normalized_instrument,
                decision_at=normalized_decision_at,
                market_at=None,
                trading_day=trading_day,
                snapshot_hash=selected.snapshot_hash,
            )
        if not _sessions_for_day(selected, normalized_instrument, trading_day):
            return _decision(
                status=CalendarDecisionStatus.UNKNOWN,
                reason_code="INSTRUMENT_SESSION_UNKNOWN",
                exchange_id=normalized_exchange,
                instrument_id=normalized_instrument,
                decision_at=normalized_decision_at,
                market_at=None,
                trading_day=trading_day,
                snapshot_hash=selected.snapshot_hash,
            )
        return _decision(
            status=CalendarDecisionStatus.OPEN,
            reason_code="INSTRUMENT_TRADING_DAY_DECLARED",
            exchange_id=normalized_exchange,
            instrument_id=normalized_instrument,
            decision_at=normalized_decision_at,
            market_at=None,
            trading_day=trading_day,
            snapshot_hash=selected.snapshot_hash,
        )

    def next_trading_day(
        self,
        exchange_id: str,
        instrument_id: str,
        after_day: date,
        decision_at: datetime,
    ) -> CalendarDecision:
        """返回同一 PIT 快照内下一已声明品种交易日。

        不会跳到快照覆盖范围以外，更不会混合不同可用时间的日历版本。覆盖不足和品种会话
        缺失均保持 ``UNKNOWN``。
        """

        normalized_exchange = _identifier(exchange_id, "exchange_id")
        normalized_instrument = _stable_instrument_id(
            instrument_id,
            "instrument_id",
            exchange_id=normalized_exchange,
        )
        _require_date(after_day, "after_day")
        normalized_decision_at = _as_utc(decision_at, "decision_at")
        selected = self._select_snapshot(
            exchange_id=normalized_exchange,
            instrument_id=normalized_instrument,
            candidate_dates=(after_day,),
            decision_at=normalized_decision_at,
            market_at=None,
        )
        if isinstance(selected, CalendarDecision):
            return selected

        future_exchange_days = tuple(item for item in selected.trading_days if item > after_day)
        if not future_exchange_days:
            return _decision(
                status=CalendarDecisionStatus.UNKNOWN,
                reason_code="NEXT_TRADING_DAY_OUTSIDE_COVERAGE",
                exchange_id=normalized_exchange,
                instrument_id=normalized_instrument,
                decision_at=normalized_decision_at,
                market_at=None,
                trading_day=None,
                snapshot_hash=selected.snapshot_hash,
            )
        for candidate_day in future_exchange_days:
            if _sessions_for_day(selected, normalized_instrument, candidate_day):
                return _decision(
                    status=CalendarDecisionStatus.OPEN,
                    reason_code="NEXT_TRADING_DAY_RESOLVED",
                    exchange_id=normalized_exchange,
                    instrument_id=normalized_instrument,
                    decision_at=normalized_decision_at,
                    market_at=None,
                    trading_day=candidate_day,
                    snapshot_hash=selected.snapshot_hash,
                )
        return _decision(
            status=CalendarDecisionStatus.UNKNOWN,
            reason_code="INSTRUMENT_SESSION_UNKNOWN",
            exchange_id=normalized_exchange,
            instrument_id=normalized_instrument,
            decision_at=normalized_decision_at,
            market_at=None,
            trading_day=future_exchange_days[0],
            snapshot_hash=selected.snapshot_hash,
        )

    def _select_snapshot(
        self,
        *,
        exchange_id: str,
        instrument_id: str,
        candidate_dates: tuple[date, ...],
        decision_at: datetime,
        market_at: datetime | None,
    ) -> TradingCalendarSnapshot | CalendarDecision:
        relevant = tuple(
            item
            for item in self._snapshots
            if item.exchange_id == exchange_id
            and any(item.covers(candidate_day) for candidate_day in candidate_dates)
        )
        representative_day = candidate_dates[0] if candidate_dates else None
        if not relevant:
            return _decision(
                status=CalendarDecisionStatus.UNKNOWN,
                reason_code="CALENDAR_COVERAGE_UNKNOWN",
                exchange_id=exchange_id,
                instrument_id=instrument_id,
                decision_at=decision_at,
                market_at=market_at,
                trading_day=representative_day,
                snapshot_hash=None,
        )
        available = tuple(item for item in relevant if item.available_at <= decision_at)
        if not available:
            return _decision(
                status=CalendarDecisionStatus.NOT_YET_AVAILABLE,
                reason_code="CALENDAR_AVAILABLE_AFTER_DECISION_TIME",
                exchange_id=exchange_id,
                instrument_id=instrument_id,
                decision_at=decision_at,
                market_at=market_at,
                trading_day=representative_day,
                snapshot_hash=None,
            )
        latest_available_at = max(item.available_at for item in available)
        latest = tuple(item for item in available if item.available_at == latest_available_at)
        if len(latest) != 1:
            return _decision(
                status=CalendarDecisionStatus.AMBIGUOUS,
                reason_code="CALENDAR_SNAPSHOTS_OVERLAP",
                exchange_id=exchange_id,
                instrument_id=instrument_id,
                decision_at=decision_at,
                market_at=market_at,
                trading_day=representative_day,
                snapshot_hash=None,
            )
        selected = latest[0]
        if selected.quality_status is not CalendarQualityStatus.PASS:
            return _decision(
                status=CalendarDecisionStatus.UNKNOWN,
                reason_code="CALENDAR_QUALITY_NOT_PASS",
                exchange_id=exchange_id,
                instrument_id=instrument_id,
                decision_at=decision_at,
                market_at=market_at,
                trading_day=representative_day,
                snapshot_hash=selected.snapshot_hash,
            )
        return selected


def _candidate_local_dates(
    market_at: datetime,
    snapshots: list[TradingCalendarSnapshot],
) -> tuple[date, ...]:
    """给夜盘查询构造本地当天和次日，不用星期规则推导交易日。"""

    local_dates = {
        market_at.astimezone(ZoneInfo(item.timezone_name)).date()
        for item in snapshots
    }
    return tuple(sorted(local_dates | {item + timedelta(days=1) for item in local_dates}))


def _sessions_for_day(
    snapshot: TradingCalendarSnapshot,
    instrument_id: str,
    trading_day: date,
) -> tuple[CalendarSession, ...]:
    return tuple(
        item
        for item in snapshot.sessions
        if item.instrument_id == instrument_id and item.trading_day == trading_day
    )


def _decision(
    *,
    status: CalendarDecisionStatus,
    reason_code: str,
    exchange_id: str,
    instrument_id: str,
    decision_at: datetime,
    market_at: datetime | None,
    trading_day: date | None,
    snapshot_hash: str | None,
    session: CalendarSession | None = None,
) -> CalendarDecision:
    return CalendarDecision(
        status=status,
        reason_code=reason_code,
        exchange_id=exchange_id,
        instrument_id=instrument_id,
        decision_at=decision_at,
        trading_day=trading_day,
        snapshot_hash=snapshot_hash,
        market_at=market_at,
        session=session,
    )


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise CalendarError(f"{field_name} 必须是非空标识符")
    normalized = value.strip().upper()
    if not normalized or any(character.isspace() for character in normalized):
        raise CalendarError(f"{field_name} 必须是非空标识符")
    return normalized


_STABLE_PRODUCT_CODE_RE = re.compile(r"^[A-Z]+$")


def _stable_instrument_id(
    value: object,
    field_name: str,
    *,
    exchange_id: str,
) -> str:
    """日历只接受与查询交易所一致的 ``EXCHANGE.PRODUCT`` 稳定品种身份。"""

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


def _as_utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CalendarError(f"{field_name} 必须是含时区的 datetime")
    return value.astimezone(UTC)


def _require_date(value: object, field_name: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise CalendarError(f"{field_name} 必须是 date")
    return value
