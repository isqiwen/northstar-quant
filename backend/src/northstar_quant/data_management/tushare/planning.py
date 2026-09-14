"""Bounded collection of complete lifetimes for already ended real contracts."""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, Engine, text

from northstar_quant import code_revision

from ..contract_data.lifecycle import completed
from .catalog import BY_KEY, DATASETS, EXCHANGES, NANHUA_CODES
from .store import settings


def enqueue(
    connection: Connection,
    dataset: str,
    scope: str,
    parameters: dict[str, object],
    start: str,
    end: str,
) -> str | None:
    # Persist the field selection with the request, including split/retry identities.
    if BY_KEY[dataset].fields:
        parameters = {**parameters, "fields": ",".join(BY_KEY[dataset].fields)}
    payload = json.dumps([dataset, scope, parameters], sort_keys=True)
    identity = hashlib.sha256(payload.encode()).hexdigest()
    connection.execute(
        text("""INSERT INTO data_sync_jobs
        (request_id,identity,dataset,scope,parameters,start_at,end_at,code_revision)
        VALUES(:id,:identity,:dataset,:scope,CAST(:params AS jsonb),:start,:end,:revision)
        ON CONFLICT(identity) DO NOTHING"""),
        {
            "id": uuid4(),
            "identity": identity,
            "dataset": dataset,
            "scope": scope,
            "params": json.dumps(parameters),
            "start": start,
            "end": end,
            "revision": code_revision(),
        },
    )

    connection.execute(
        text("""INSERT INTO data_contract_requests(scope,request_id)
        SELECT w.scope,j.request_id FROM data_contract_collections w CROSS JOIN data_sync_jobs j
        WHERE w.scope=:scope AND j.identity=:identity ON CONFLICT DO NOTHING"""),
        dict(scope=scope, identity=identity),
    )
    return identity


def invalidate_catalog(connection: Connection) -> None:
    """Replan dependent ranges without resetting downloaded jobs or coverage."""
    connection.execute(text("UPDATE data_sync_contracts SET planned_revision=0"))
    connection.execute(text("UPDATE data_sync_settings SET planned_at=NULL"))


def target_day() -> date:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    # Target is a completed session, never an assertion that the vendor has published it.
    return now.date() if now.hour >= 18 else now.date() - timedelta(days=1)


def refresh(engine: Engine) -> None:
    config = settings(engine)
    if not config["enabled"]:
        return
    now = datetime.now(UTC)
    if datetime.fromisoformat(config["refresh_at"]) > now:
        return
    with engine.begin() as connection:
        for exchange in EXCHANGES:
            for kind in ("1", "2"):
                enqueue(
                    connection,
                    "contracts",
                    exchange,
                    {"exchange": exchange, "fut_type": kind},
                    "",
                    "",
                )
        # Ended lifetimes are fixed. Refresh discovery without silently reopening
        # already published or rejected contracts and their internal request windows.
        connection.execute(
            text("""UPDATE data_sync_jobs SET status='PENDING',next_at=now(),attempts=0
            WHERE status='VALIDATED' AND dataset='contracts'""")
        )
        connection.execute(
            text("""UPDATE data_sync_jobs j SET status='PENDING',next_at=now()
            WHERE j.status='VALIDATED' AND NOT EXISTS (
                SELECT 1 FROM data_sync_coverage v WHERE v.request_id=j.request_id)
            AND EXISTS (SELECT 1 FROM data_contract_requests cr
                JOIN data_contract_collections w ON w.scope=cr.scope
                WHERE cr.request_id=j.request_id AND w.status IN ('COLLECTING','VERIFYING'))""")
        )
        connection.execute(
            text("""UPDATE data_sync_settings SET revision=revision+1,
            refresh_at=now()+interval '6 hours',planned_at=NULL""")
        )


def plan(engine: Engine) -> None:
    config = settings(engine)
    target = target_day()
    with engine.begin() as connection:
        connection.execute(
            text("""UPDATE data_contract_collections w SET status='VERIFYING',
            updated_at=now() WHERE status='COLLECTING' AND NOT EXISTS(
                SELECT 1 FROM data_contract_requests cr JOIN data_sync_jobs j USING(request_id)
                WHERE cr.scope=w.scope AND j.status IN ('PENDING','RUNNING'))""")
        )
        if connection.scalar(
            text("SELECT EXISTS(SELECT 1 FROM data_contract_collections WHERE status='COLLECTING')")
        ):
            return
        contracts = (
            connection.execute(
                text("""SELECT * FROM data_sync_contracts
            WHERE planned_revision<>:revision AND kind='1'
            AND details->>'delist_date' ~ '^[0-9]{8}$'
            AND details->>'delist_date'<:today
            ORDER BY details->>'delist_date' DESC,exchange,product,ts_code LIMIT 1 FOR UPDATE"""),
                dict(revision=config["revision"], today=target.strftime("%Y%m%d")),
            )
            .mappings()
            .all()
        )
        for contract in contracts:
            try:
                lifetime = completed(contract, today=target)
            except ValueError as error:
                connection.execute(
                    text("""UPDATE data_sync_contracts SET planned_revision=:r,
                    planning_error=:reason WHERE ts_code=:scope"""),
                    dict(r=config["revision"], reason=str(error), scope=contract["ts_code"]),
                )
                continue
            start, end = lifetime.start, lifetime.end
            connection.execute(
                text("""INSERT INTO data_contract_collections(scope,start_date,end_date)
                VALUES(:scope,:start,:end) ON CONFLICT(scope) DO UPDATE
                SET start_date=excluded.start_date,end_date=excluded.end_date,
                    status='COLLECTING',reason=NULL,updated_at=now()
                WHERE (data_contract_collections.start_date,data_contract_collections.end_date)
                    IS DISTINCT FROM (excluded.start_date,excluded.end_date)"""),
                dict(scope=contract["ts_code"], start=start, end=end),
            )
            connection.execute(
                text("""INSERT INTO data_contract_requests(scope,request_id)
                SELECT :owner,request_id FROM data_sync_jobs
                WHERE dataset='contracts' AND scope=:exchange ON CONFLICT DO NOTHING"""),
                dict(owner=contract["ts_code"], exchange=contract["exchange"]),
            )
            for year in range(start.year, end.year + 1):
                a, b = date(year, 1, 1), date(year, 12, 31)
                identity = enqueue(
                    connection,
                    "calendar",
                    contract["exchange"],
                    {
                        "exchange": contract["exchange"],
                        "start_date": a.strftime("%Y%m%d"),
                        "end_date": b.strftime("%Y%m%d"),
                    },
                    a.isoformat(),
                    b.isoformat(),
                )
                _link(connection, contract["ts_code"], identity)
            for dataset in DATASETS:
                if dataset.scope in ("catalog", "calendar"):
                    continue
                if dataset.scope == "continuous":
                    # Related series keep their own identity, bounded by this retired
                    # contract's life. Never collect a continuous series independently.
                    related = (
                        connection.execute(
                            text("""SELECT * FROM data_sync_contracts
                        WHERE exchange=:e AND product=:p AND kind='2'"""),
                            dict(e=contract["exchange"], p=contract["product"]),
                        )
                        .mappings()
                        .all()
                    )
                else:
                    related = [contract]
                window_start, window_end = start, end
                # Calendar-month shards stay fixed. The open month uses daily shards,
                # so the current cycle's endpoint never grows underneath a running task.
                cursor = window_start.replace(day=1)
                while cursor <= window_end:
                    next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
                    stop = min(next_month - timedelta(days=1), window_end)
                    a = max(window_start, cursor)
                    if next_month <= target.replace(day=1):
                        for item in related:
                            _window(
                                connection, dataset.key, item, a, stop, owner=contract["ts_code"]
                            )
                    else:
                        while a <= stop:
                            for item in related:
                                _window(
                                    connection, dataset.key, item, a, a, owner=contract["ts_code"]
                                )
                            a += timedelta(days=1)
                    cursor = next_month
            connection.execute(
                text(
                    "UPDATE data_sync_contracts SET planned_revision=:r,planning_error=NULL "
                    "WHERE ts_code=:c"
                ),
                {"r": config["revision"], "c": contract["ts_code"]},
            )
        if not contracts:
            connection.execute(
                text("UPDATE data_sync_settings SET planned_at=now() WHERE planned_at IS NULL")
            )


def _window(
    connection: Connection, key: str, contract: Any, start: date, end: date, *, owner: str
) -> None:
    if end < start:
        return
    dataset = BY_KEY[key]
    scope = contract["ts_code"]
    params: dict[str, object] = {
        "start_date": start.strftime("%Y%m%d"),
        "end_date": end.strftime("%Y%m%d"),
    }
    if dataset.scope in ("contract", "continuous"):
        params["ts_code"] = contract["ts_code"]
    elif dataset.scope == "product":
        scope = contract["exchange"] + ":" + contract["product"]
        params.update(exchange=contract["exchange"], symbol=contract["product"])
    else:
        scope = "ALL"
    if dataset.frequency:
        params["freq"] = dataset.frequency
    if dataset.api == "ft_mins":
        params.update(
            start_date=f"{start.isoformat()} 00:00:00", end_date=f"{end.isoformat()} 23:59:59"
        )
    if dataset.api == "fut_weekly_detail":
        params = {
            "exchange": contract["exchange"],
            "prd": contract["product"],
            "start_week": start.strftime("%G%V"),
            "end_week": end.strftime("%G%V"),
        }
    if key == "index":
        for code in NANHUA_CODES:
            identity = enqueue(
                connection,
                key,
                code,
                {**params, "ts_code": code},
                start.isoformat(),
                end.isoformat(),
            )
            _link(connection, owner, identity)
    else:
        identity = enqueue(connection, key, scope, params, start.isoformat(), end.isoformat())
        _link(connection, owner, identity)


def _link(connection: Connection, owner: str, identity: str | None) -> None:
    connection.execute(
        text("""INSERT INTO data_contract_requests(scope,request_id)
        SELECT :scope,request_id FROM data_sync_jobs WHERE identity=:identity
        ON CONFLICT DO NOTHING"""),
        dict(scope=owner, identity=identity),
    )


def split(connection: Connection, job: dict[str, Any]) -> bool:
    start, end = date.fromisoformat(job["start_at"]), date.fromisoformat(job["end_at"])
    if start >= end:
        return False
    middle = start + (end - start) // 2
    for a, b in ((start, middle), (middle + timedelta(days=1), end)):
        params = dict(job["parameters"])
        if BY_KEY[job["dataset"]].api == "ft_mins":
            params.update(start_date=f"{a} 00:00:00", end_date=f"{b} 23:59:59")
        elif BY_KEY[job["dataset"]].api == "fut_weekly_detail":
            params.update(start_week=a.strftime("%G%V"), end_week=b.strftime("%G%V"))
        else:
            params.update(start_date=a.strftime("%Y%m%d"), end_date=b.strftime("%Y%m%d"))
        child = enqueue(
            connection, job["dataset"], job["scope"], params, a.isoformat(), b.isoformat()
        )
        connection.execute(
            text("""INSERT INTO data_contract_requests(scope,request_id)
            SELECT parent.scope,j.request_id FROM data_contract_requests parent
            CROSS JOIN data_sync_jobs j WHERE parent.request_id=:parent AND j.identity=:child
            ON CONFLICT DO NOTHING"""),
            dict(parent=job["request_id"], child=child),
        )
        connection.execute(
            text("""UPDATE data_sync_jobs SET status='PENDING',
            next_at=now(),error='截断后拆分，按此区间下载' WHERE identity=:id
            AND status='SPLIT' AND attempts=0 AND error LIKE '合并下载%'
            AND receipt_id IS NULL AND source_generation IS NULL"""),
            {"id": child},
        )
    return True
