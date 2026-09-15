"""Observed supplier starts, backed by retained responses, never inferred from emptiness."""

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import Connection, text


def observe(connection: Connection, job: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Called after response validation, independently of full interval coverage."""
    days = []
    for row in rows:
        value = row.get("trade_time", row.get("cal_date", row.get("trade_date")))
        if value is None:
            # Native week identifiers have mixed widths and historical offsets.
            # A missing report date cannot be reconstructed from an ISO week.
            value = row.get("week_date")
        if value is None:
            # Catalogs describe instrument lifetimes, not historical observations.
            continue
        day = (
            datetime.strptime(value, "%Y-%m-%d %H:%M:%S").date()
            if "trade_time" in row
            else datetime.strptime(value, "%Y%m%d").date()
        )
        if job["dataset"] in ("week", "month"):
            day = min(day, datetime.strptime(row["end_date"], "%Y%m%d").date())
        days.append(day)
    if not days:
        return
    connection.execute(
        text("""UPDATE data_sync_attempts
        SET quality=coalesce(quality,'{}'::jsonb) || CAST(:evidence AS jsonb)
        WHERE generation=:generation AND request_id=:request"""),
        {
            "evidence": json.dumps({"first_observed": min(days).isoformat()}),
            "generation": job["generation"],
            "request": job["request_id"],
        },
    )


def first(connection: Connection, dataset: str, scope: str) -> date | None:
    found = connection.scalar(
        text("""SELECT min(a.quality->>'first_observed')
        FROM data_sync_jobs j JOIN data_sync_attempts a USING(request_id)
        WHERE j.dataset=:d AND j.scope=:s"""),
        {"d": dataset, "s": scope},
    )
    return date.fromisoformat(found) if found else None


def describe(connection: Connection, job: dict[str, Any]) -> dict[str, Any]:
    evidence = (
        connection.execute(
            text("""SELECT a.quality->>'first_observed' AS first_observed,
            a.generation,a.source_hash,a.source_bytes
        FROM data_sync_jobs j JOIN data_sync_attempts a USING(request_id)
        WHERE j.dataset=:d AND j.scope=:s AND a.quality->>'first_observed' IS NOT NULL
        ORDER BY a.quality->>'first_observed',a.started_at,a.generation LIMIT 1"""),
            {"d": job["dataset"], "s": job["scope"]},
        )
        .mappings()
        .one_or_none()
    )
    return {
        "first_observed": evidence["first_observed"] if evidence else None,
        "generation": str(evidence["generation"]) if evidence else None,
        "source_hash": evidence["source_hash"] if evidence else None,
        "source_bytes": evidence["source_bytes"] if evidence else None,
        "basis": "VALIDATED_RESPONSE" if evidence else "DISCOVERING",
        "note": "已发现的最早有效记录，不代表更早区间已证明不存在；各周期独立核查。",
    }
