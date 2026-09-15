"""Exchange-calendar request envelopes, separate from intraday session proof."""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date

from sqlalchemy import Connection, text


@dataclass(frozen=True)
class RequestCalendar:
    open_days: tuple[date, ...]

    def bounds(self, start: date, end: date, *, minute: bool = False) -> tuple[date, date] | None:
        left = bisect_left(self.open_days, start)
        right = bisect_right(self.open_days, end)
        if left == right:
            return None
        # Include the whole previous open date as a conservative supplier-query
        # envelope. This does not assert a night session, a 21:00 label or its
        # trading-day assignment; historical session proof belongs to admission.
        if minute:
            if left == 0:
                raise ValueError("交易日历尚无前一交易日，不能确定分钟下载边界")
            left -= 1
        return self.open_days[left], self.open_days[right - 1]


def load(c: Connection, exchange: str, start: date, end: date) -> RequestCalendar | None:
    # A preceding calendar year handles January, weekends and long closures
    # without guessing a fixed number of lookback days.
    first = date(start.year - 1, 1, 1)
    rows = c.execute(
        text("""SELECT cal_date,is_open FROM data_sync_calendar
        WHERE exchange=:exchange AND cal_date BETWEEN :start AND :end ORDER BY cal_date"""),
        dict(exchange=exchange, start=first, end=end),
    ).all()
    previous = [row.cal_date for row in rows if row.is_open and row.cal_date < start]
    if not previous:
        return None
    anchor = previous[-1]
    relevant = [row for row in rows if row.cal_date >= anchor]
    if len(relevant) != (end - anchor).days + 1:
        return None
    return RequestCalendar(tuple(row.cal_date for row in relevant if row.is_open))
