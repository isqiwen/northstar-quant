"""Oldest-first, bounded independent collection through the shared supplier queue."""

from datetime import date, timedelta

from sqlalchemy import Connection, Engine, text

from ..contract_data.lifecycle import SEARCH_START, metadata_date
from ..tushare.nanhua import INDEX_NAMES
from ..tushare.planning import enqueue, target_day
from ..tushare.request_calendar import load as load_calendar

DATASETS = ("continuous", "mapping", "adjusted", "index")


def link(c: Connection, dataset: str, scope: str, identity: str | None) -> None:
    c.execute(
        text("""INSERT INTO data_series_requests(dataset,scope,request_id)
        SELECT :dataset,:scope,request_id FROM data_sync_jobs WHERE identity=:identity
        ON CONFLICT DO NOTHING"""),
        dict(dataset=dataset, scope=scope, identity=identity),
    )


def plan(engine: Engine) -> None:
    target = target_day()
    with engine.begin() as c:
        if not c.scalar(text("SELECT enabled FROM data_sync_settings")):
            return
        if not c.scalar(text("SELECT EXISTS(SELECT 1 FROM data_sync_contracts WHERE kind='2')")):
            return
        c.execute(
            text("""UPDATE data_series_collections SET start_date=:floor,
            planned_through=CASE WHEN planned_through<:floor
                THEN CAST(:floor AS date) - 1 ELSE planned_through END
            WHERE start_date<:floor"""),
            dict(floor=SEARCH_START),
        )
        # Discovery metadata supplies series identity; never synthesize continuous
        # symbols from an expiring month's contract code.
        for r in c.execute(
            text("""SELECT d.* FROM data_sync_contracts d WHERE kind='2'
            AND NOT EXISTS (SELECT 1 FROM data_series_collections s
                WHERE s.dataset='continuous' AND s.scope=d.ts_code)""")
        ).mappings():
            start = max(SEARCH_START, metadata_date(r["details"].get("list_date")) or SEARCH_START)
            for dataset in DATASETS[:3]:
                c.execute(
                    text("""INSERT INTO data_series_collections
                    (dataset,scope,exchange,product,name,start_date)
                    VALUES(:dataset,:scope,:exchange,:product,:name,:start)
                    ON CONFLICT DO NOTHING"""),
                    dict(
                        dataset=dataset,
                        scope=r["ts_code"],
                        exchange=r["exchange"],
                        product=r["product"],
                        name=r["details"].get("name") or r["ts_code"],
                        start=start,
                    ),
                )
        # Range requests require an explicit index in the live supplier API.
        # Use its documented identities; empty historical responses remain gaps.
        known_indices = set(
            c.scalars(text("SELECT scope FROM data_series_collections WHERE dataset='index'"))
        )
        for scope, name in INDEX_NAMES.items():
            if scope in known_indices:
                continue
            c.execute(
                text("""INSERT INTO data_series_collections
                (dataset,scope,exchange,product,name,start_date)
                VALUES('index',:scope,'','',:name,:start) ON CONFLICT DO NOTHING"""),
                dict(scope=scope, name=name, start=SEARCH_START),
            )
        if (
            c.scalar(
                text("""SELECT count(*) FROM data_sync_jobs j WHERE
            j.status IN ('PENDING','RUNNING')
            AND (j.dataset='calendar' OR j.end_at>=:floor) AND EXISTS
            (SELECT 1 FROM data_series_requests s WHERE s.request_id=j.request_id)"""),
                dict(floor=SEARCH_START.isoformat()),
            )
            >= 64
        ):
            return
        row = (
            c.execute(
                text("""SELECT * FROM data_series_collections
            WHERE COALESCE(planned_through,start_date-1)<:target
            ORDER BY COALESCE(planned_through,start_date-1),scope,dataset LIMIT 1
            FOR UPDATE SKIP LOCKED"""),
                dict(target=target),
            )
            .mappings()
            .first()
        )
        if row is None:
            return
        start = (
            row["planned_through"] + timedelta(days=1)
            if row["planned_through"]
            else row["start_date"]
        )
        following = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = min(following - timedelta(days=1), target)
        dataset, scope = row["dataset"], row["scope"]
        if row["exchange"]:
            for year in range(start.year - 1, end.year + 1):
                a, b = date(year, 1, 1), date(year, 12, 31)
                identity = enqueue(
                    c,
                    "calendar",
                    row["exchange"],
                    dict(
                        exchange=row["exchange"],
                        start_date=a.strftime("%Y%m%d"),
                        end_date=b.strftime("%Y%m%d"),
                    ),
                    a.isoformat(),
                    b.isoformat(),
                )
                link(c, dataset, scope, identity)
        bounds: tuple[date, date] | None = (start, end)
        if row["exchange"]:
            calendar = load_calendar(c, row["exchange"], start, end)
            if calendar is None:
                return
            bounds = calendar.bounds(start, end)
        if bounds is None:
            c.execute(
                text("""UPDATE data_series_collections SET planned_through=:end
                WHERE dataset=:dataset AND scope=:scope"""),
                dict(end=end, dataset=dataset, scope=scope),
            )
            return
        parameters: dict[str, object] = dict(
            start_date=bounds[0].strftime("%Y%m%d"), end_date=bounds[1].strftime("%Y%m%d")
        )
        parameters["ts_code"] = scope
        identity = enqueue(c, dataset, scope, parameters, start.isoformat(), end.isoformat())
        link(c, dataset, scope, identity)
        c.execute(
            text("""UPDATE data_series_collections SET planned_through=:end
            WHERE dataset=:dataset AND scope=:scope"""),
            dict(end=end, dataset=dataset, scope=scope),
        )
