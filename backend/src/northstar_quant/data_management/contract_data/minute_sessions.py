"""Dated DCE corn sessions and conservative dense supplier-label verification.

The scope is deliberately bounded to the two reviewed exchange notice years.
Missing dense labels remain unknown: no zero-volume omission policy is invented.
"""

import hashlib
import json
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import Connection, text

from ..tushare.request_calendar import load

RULE = "dce-corn-sessions/1"
REFERENCES = [
    "https://www.mkqh.com/front/serviceinfo/15731",
    "https://www.nanhua.net/news/2024/12/9804d25c-a009-423b-a56f-cbea3913ccf7.html",
    "https://www.zjncf.com.cn/customer/info/5304.html",
]
# Exchange notices 2024/563 and 2025/437, reproduced by member brokers.
NO_NIGHT = frozenset(
    date.fromisoformat(s)
    for s in (
        "2024-12-31",
        "2025-01-27",
        "2025-04-03",
        "2025-04-30",
        "2025-05-30",
        "2025-09-30",
        "2025-12-31",
        "2026-02-13",
        "2026-04-03",
        "2026-04-30",
        "2026-06-18",
        "2026-09-24",
        "2026-09-30",
    )
)


@dataclass(frozen=True)
class Sessions:
    days: tuple[date, ...]
    calendar_hash: str
    start: date
    end: date

    def trading_day(self, timestamp: datetime) -> date | None:
        day = timestamp.date()
        if timestamp.time() >= time(21):
            index = bisect_right(self.days, day)
            return self.days[index] if index < len(self.days) else None
        return day

    def labels(self, dataset: str) -> dict[str, date]:
        step = int(dataset.removesuffix("min"))
        labels: dict[str, date] = {}
        for index, day in enumerate(self.days):
            if not self.start <= day <= self.end:
                continue
            previous = self.days[index - 1]
            night = previous not in NO_NIGHT
            if night:
                for minute in range(21 * 60, 23 * 60 + 1, step):
                    labels[_label(previous, minute)] = day
            else:
                labels[_label(day, 9 * 60)] = day
            # Count trading minutes, pausing across 10:15-10:30 and lunch.
            minutes = [
                m for a, b in ((540, 615), (630, 690), (810, 900)) for m in range(a + 1, b + 1)
            ]
            for offset, minute in enumerate(minutes, 1):
                if offset % step == 0 or offset == len(minutes):
                    labels[_label(day, minute)] = day
        return labels

    def evidence(self) -> dict[str, Any]:
        return dict(
            rule=RULE,
            exchange="DCE",
            product="C",
            valid_from="2025-01-01",
            valid_through="2026-12-31",
            calendar_hash=self.calendar_hash,
            references=REFERENCES,
            source_kind="EXCHANGE_NOTICE_MEMBER_REPRODUCTION",
            supplier_rule="tushare-minute-support/1",
            reviewed_on="2026-09-16",
            acceptance="ALL_DENSE_LABELS_PRESENT",
            availability_verified=False,
        )


def _label(day: date, minute: int) -> str:
    return datetime.combine(day, time(minute // 60, minute % 60)).isoformat(sep=" ")


def load_sessions(
    c: Connection, exchange: str, product: str | None, start: date, end: date
) -> Sessions | None:
    if exchange != "DCE" or product != "C" or start < date(2025, 1, 1) or end > date(2026, 12, 31):
        return None
    calendar = load(c, exchange, start, end)
    if calendar is None:
        return None
    rows = c.execute(
        text("""SELECT cal_date,is_open FROM data_sync_calendar
        WHERE exchange=:e AND cal_date BETWEEN :a AND :b ORDER BY cal_date"""),
        dict(e=exchange, a=calendar.open_days[0], b=end),
    ).all()
    digest = hashlib.sha256(json.dumps([(d.isoformat(), o) for d, o in rows]).encode()).hexdigest()
    return Sessions(calendar.open_days, digest, start, end)
