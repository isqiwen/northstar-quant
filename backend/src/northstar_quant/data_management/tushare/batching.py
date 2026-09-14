"""Combine untouched adjacent requests without changing any downloaded identity."""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import Connection, text

from .catalog import BY_KEY
from .planning import enqueue
from .scheduling import history_end


def combine(connection: Connection, selected: Any) -> Any:
    dataset = BY_KEY[selected["dataset"]]
    if (
        selected["attempts"]
        or selected["source_generation"]
        or selected["generation"]
        or selected["receipt_id"]
        or selected["error"]
        or not selected["start_at"]
        or dataset.scope in ("catalog", "calendar", "product")
    ):
        return selected
    # Actual successful response density calibrates request size. It is only an
    # estimate: changing liquidity/session lengths still require truncation guards.
    samples = (
        connection.execute(
            text("""SELECT r.row_count,
        (j.end_at::date-j.start_at::date+1) AS days
        FROM data_sync_receipts r JOIN data_sync_coverage v USING(receipt_id)
        JOIN data_sync_jobs j ON j.request_id=r.request_id
        WHERE j.dataset=:dataset AND r.row_count>0 AND j.start_at<>''
        ORDER BY r.created_at DESC LIMIT 32"""),
            {"dataset": dataset.key},
        )
        .mappings()
        .all()
    )
    rows_per_day = max((row["row_count"] / row["days"] for row in samples), default=0)
    if rows_per_day == 0:
        rows_per_day = (
            600 / int(dataset.frequency.removesuffix("min")) if dataset.api == "ft_mins" else 1
        )
    # Reserve 10% for density changes; a full response is never declared complete.
    days = max(1, int((dataset.limit * 0.9) / rows_per_day))
    start = date.fromisoformat(selected["start_at"])
    end = date.fromisoformat(selected["end_at"])
    boundary = history_end(connection)
    ceiling = start + timedelta(days=days - 1)
    if boundary and end <= boundary:
        ceiling = min(ceiling, boundary)
    if ceiling <= end:
        return selected
    candidates = (
        connection.execute(
            text("""SELECT * FROM data_sync_jobs
        WHERE dataset=:dataset AND scope=:scope AND status='PENDING' AND attempts=0
        AND source_generation IS NULL AND generation IS NULL AND receipt_id IS NULL
        AND error IS NULL AND next_at<=now() AND start_at>:end AND end_at<=:ceiling
        ORDER BY start_at,end_at,request_id LIMIT 128 FOR UPDATE SKIP LOCKED"""),
            {
                "dataset": dataset.key,
                "scope": selected["scope"],
                "end": end.isoformat(),
                "ceiling": ceiling.isoformat(),
            },
        )
        .mappings()
        .all()
    )

    def identity(parameters: Any) -> dict[str, Any]:
        return {k: v for k, v in parameters.items() if k not in ("start_date", "end_date")}

    children = [selected["request_id"]]
    for candidate in candidates:
        if date.fromisoformat(candidate["start_at"]) != end + timedelta(days=1):
            break
        if identity(candidate["parameters"]) != identity(selected["parameters"]):
            break
        end = date.fromisoformat(candidate["end_at"])
        children.append(candidate["request_id"])
    if len(children) == 1:
        return selected
    parameters = dict(selected["parameters"])
    parameters["end_date"] = (
        f"{end} 23:59:59" if dataset.api == "ft_mins" else end.strftime("%Y%m%d")
    )
    identity_key = enqueue(
        connection, dataset.key, selected["scope"], parameters, start.isoformat(), end.isoformat()
    )
    combined = (
        connection.execute(
            text("SELECT * FROM data_sync_jobs WHERE identity=:id FOR UPDATE"), {"id": identity_key}
        )
        .mappings()
        .one()
    )
    # A retry/split may already own this identity; never re-fetch it or change its evidence.
    if combined["attempts"] or combined["status"] != "PENDING" or combined["source_generation"]:
        return selected
    request_id = combined["request_id"]
    connection.execute(
        text("""UPDATE data_sync_jobs SET status='SPLIT',
        error=:reason,updated_at=now() WHERE request_id=ANY(:ids)"""),
        {"reason": f"合并下载：{request_id}", "ids": children},
    )
    connection.execute(
        text("""INSERT INTO data_contract_requests(scope,request_id)
        SELECT DISTINCT scope,:combined FROM data_contract_requests
        WHERE request_id=ANY(:children) ON CONFLICT DO NOTHING"""),
        dict(combined=request_id, children=children),
    )
    return combined
