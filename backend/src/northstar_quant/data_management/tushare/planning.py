"""Incremental, bounded planning of all futures history, including expired contracts."""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, Engine, text

from northstar_quant import code_revision

from .catalog import BY_KEY, DATASETS, EXCHANGES
from .store import settings


def enqueue(
    connection: Connection,
    dataset: str,
    scope: str,
    parameters: dict[str, object],
    start: str,
    end: str,
) -> None:
    payload = json.dumps([dataset, scope, parameters], sort_keys=True)
    connection.execute(
        text("""INSERT INTO data_sync_jobs
        (request_id,identity,dataset,scope,parameters,start_at,end_at,code_revision)
        VALUES(:id,:identity,:dataset,:scope,CAST(:params AS jsonb),:start,:end,:revision)
        ON CONFLICT(identity) DO NOTHING"""),
        {
            "id": uuid4(),
            "identity": hashlib.sha256(payload.encode()).hexdigest(),
            "dataset": dataset,
            "scope": scope,
            "params": json.dumps(parameters),
            "start": start,
            "end": end,
            "revision": code_revision(),
        },
    )


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
    target = target_day()
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
        # Catalog and recent windows are rechecked daily; old windows also rotate
        # through a 90-day recheck, with the next-request time persisted independently.
        connection.execute(
            text("""UPDATE data_sync_jobs SET status='PENDING',next_at=now(),attempts=0
            WHERE status='VALIDATED' AND
            (dataset='contracts' OR checked_at < now()-interval '90 days'
             OR end_at >= :recent)"""),
            {"recent": (target - timedelta(days=config["lookback"] * 2)).isoformat()},
        )
        # A coverage hole must be repaired even when newer ranges already exist.
        connection.execute(
            text("""UPDATE data_sync_jobs j SET status='PENDING',next_at=now()
            WHERE status='VALIDATED' AND NOT EXISTS
            (SELECT 1 FROM data_sync_coverage c WHERE c.request_id=j.request_id)""")
        )
        connection.execute(
            text("""UPDATE data_sync_settings SET revision=revision+1,
            refresh_at=now()+interval '6 hours',planned_at=NULL""")
        )


def plan(engine: Engine) -> None:
    config = settings(engine)
    target = target_day()
    with engine.begin() as connection:
        contracts = (
            connection.execute(
                text("""SELECT * FROM data_sync_contracts
            WHERE planned_revision<>:revision ORDER BY ts_code LIMIT 8 FOR UPDATE"""),
                {"revision": config["revision"]},
            )
            .mappings()
            .all()
        )
        for contract in contracts:
            details = contract["details"]
            start_text = details.get("list_date")
            end_text = details.get("delist_date")
            if not start_text:
                # Continuous instruments use the earliest actual contract for their product.
                start_text = connection.scalar(
                    text("""SELECT min(details->>'list_date')
                    FROM data_sync_contracts WHERE exchange=:e AND product=:p AND kind='1'
                    AND length(details->>'list_date')=8"""),
                    {"e": contract["exchange"], "p": contract["product"]},
                )
            try:
                if not start_text:
                    raise ValueError("missing listing")
                start = datetime.strptime(start_text, "%Y%m%d").date()
                end = (
                    min(target, datetime.strptime(end_text, "%Y%m%d").date())
                    if end_text
                    else target
                )
                if start > target:
                    connection.execute(
                        text("""UPDATE data_sync_contracts SET planned_revision=:r,
                        planning_error=NULL WHERE ts_code=:code"""),
                        {"r": config["revision"], "code": contract["ts_code"]},
                    )
                    continue
                if start > end:
                    raise ValueError("invalid lifetime")
            except (ValueError, TypeError):
                connection.execute(
                    text("""UPDATE data_sync_contracts SET planned_revision=:r,
                    planning_error='上市或到期范围缺失/无效，等待目录复核' WHERE ts_code=:code"""),
                    {"r": config["revision"], "code": contract["ts_code"]},
                )
                continue
            for year in range(start.year, target.year + 1):
                a, b = date(year, 1, 1), min(date(year, 12, 31), target)
                enqueue(
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
            for dataset in DATASETS:
                if dataset.scope in ("catalog", "calendar"):
                    continue
                if dataset.scope == "continuous" and contract["kind"] != "2":
                    continue
                if dataset.scope == "contract" and contract["kind"] != "1":
                    continue
                if dataset.scope in ("product", "market") and contract["kind"] != "1":
                    continue
                window_start, window_end = start, end
                if dataset.scope in ("product", "market"):
                    condition = "kind='1'"
                    params = {}
                    if dataset.scope == "product":
                        condition += " AND exchange=:e AND product=:p"
                        params = {"e": contract["exchange"], "p": contract["product"]}
                    group = (
                        connection.execute(
                            text(f"""SELECT min(ts_code) AS first,
                        min(details->>'list_date') AS begin FROM data_sync_contracts
                        WHERE {condition} AND length(details->>'list_date')=8"""),
                            params,
                        )
                        .mappings()
                        .one()
                    )
                    if contract["ts_code"] != group["first"]:
                        continue
                    window_start = datetime.strptime(group["begin"], "%Y%m%d").date()
                    window_end = target
                # Calendar-month shards stay fixed. The open month uses daily shards,
                # so the current cycle's endpoint never grows underneath a running task.
                cursor = window_start.replace(day=1)
                while cursor <= window_end:
                    next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
                    stop = min(next_month - timedelta(days=1), window_end)
                    a = max(window_start, cursor)
                    if next_month <= target.replace(day=1):
                        _window(connection, dataset.key, contract, a, stop)
                    else:
                        while a <= stop:
                            _window(connection, dataset.key, contract, a, a)
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


def _window(connection: Connection, key: str, contract: Any, start: date, end: date) -> None:
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
    enqueue(connection, key, scope, params, start.isoformat(), end.isoformat())


def split(connection: Connection, job: dict[str, Any]) -> bool:
    start, end = date.fromisoformat(job["start_at"]), date.fromisoformat(job["end_at"])
    if start >= end:
        return False
    middle = start + (end - start) // 2
    for a, b in ((start, middle), (middle + timedelta(days=1), end)):
        params = dict(job["parameters"])
        if BY_KEY[job["dataset"]].api == "ft_mins":
            params.update(start_date=f"{a} 00:00:00", end_date=f"{b} 23:59:59")
        else:
            params.update(start_date=a.strftime("%Y%m%d"), end_date=b.strftime("%Y%m%d"))
        enqueue(connection, job["dataset"], job["scope"], params, a.isoformat(), b.isoformat())
    return True
