"""Explicit product subscriptions and source-backed admission preflight."""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import Connection, text

from ..contract_data.requirements import CORE_DATASETS, classify, requirement
from .catalog import BY_KEY
from .request_calendar import RequestCalendar

# User-selected collection boundary, not a supplier coverage claim.
LISTING_START = date(2025, 1, 1)


def catalog(c: Connection) -> list[dict[str, Any]]:
    rows = c.execute(
        text("""SELECT exchange,product,min(details->>'name') AS name,
        count(*) FILTER(WHERE kind='1') AS contracts FROM data_sync_contracts WHERE kind='1'
        GROUP BY exchange,product ORDER BY exchange,product""")
    ).mappings()
    return [dict(key=f"{r['exchange']}:{r['product']}", **r) for r in rows]


def validate(c: Connection, selected: list[str]) -> None:
    known = {r["key"] for r in catalog(c)}
    if len(selected) > 200 or len(selected) != len(set(selected)) or set(selected) - known:
        raise ValueError("品种选择无效；请先更新目录，再选择交易所和品种")


def origins(c: Connection) -> list[dict[str, Any]]:
    # Observations prove presence, not absence before the observed date. Product
    # identity comes from the real owner, never a code prefix or continuous proxy.
    rows = c.execute(
        text("""SELECT d.exchange,d.product,j.dataset,
        min(a.quality->>'first_observed') AS first_observed,
        count(DISTINCT d.ts_code) AS observed_contracts
        FROM data_sync_contracts d JOIN data_contract_requests cr ON cr.scope=d.ts_code
        JOIN data_sync_jobs j USING(request_id)
        JOIN data_sync_attempts a ON a.request_id=j.request_id
        WHERE d.exchange || ':' || d.product=ANY(
            (SELECT selected_products FROM data_sync_settings)::text[])
        AND a.quality->>'first_observed' IS NOT NULL AND j.dataset NOT IN ('contracts','calendar')
        GROUP BY d.exchange,d.product,j.dataset ORDER BY d.exchange,d.product,j.dataset""")
    )
    return [dict(r, basis="VALIDATED_RESPONSE", complete_history=False) for r in rows.mappings()]


def probe(c: Connection, contract: Any, start: date, end: date, calendar: RequestCalendar) -> bool:
    """Check the opening month before enqueueing the rest of a retired lifetime.

    These are ordinary bounded, archived requests, reused by full collection. No
    limit=1 sample is ever passed off as interval coverage. A presently empty or
    invalid opening interval skips this candidate, not the product's older history
    forever; transient transport/permission problems stay pending.
    """
    from .planning import _window

    scope = contract["ts_code"]
    if c.scalar(
        text("SELECT discovery_complete FROM data_contract_collections WHERE scope=:s"),
        {"s": scope},
    ):
        return True
    bounds = calendar.bounds(start, end)
    if bounds is None:
        return False
    first_day = bounds[0]
    next_month = (first_day.replace(day=28) + timedelta(days=4)).replace(day=1)
    stop = min(end, next_month - timedelta(days=1))
    needed = sorted(
        k
        for k in CORE_DATASETS - {"contracts", "calendar"}
        if requirement(classify(contract), k).collect
    )
    for key in needed:
        _window(c, key, contract, start, stop, owner=scope, calendar=calendar)
    rows = (
        c.execute(
            text("""SELECT j.*,a.quality FROM data_contract_requests cr
        JOIN data_sync_jobs j USING(request_id)
        LEFT JOIN data_sync_attempts a ON a.generation=j.generation
        WHERE cr.scope=:s AND j.dataset=ANY(CAST(:datasets AS text[]))
        AND j.status<>'SPLIT'"""),
            {"s": scope, "datasets": needed},
        )
        .mappings()
        .all()
    )
    for row in rows:
        empty = row["status"] == "WAITING" and str(row["error"] or "").startswith(
            ("起点探测：", "历史区间返回空数据")
        )
        invalid = row["status"] == "BLOCKED" and bool((row["quality"] or {}).get("issues"))
        if empty or invalid:
            reason = (
                f"历史覆盖探查：{BY_KEY[row['dataset']].label} 上市首段 "
                f"{row['start_at']}～{row['end_at']} "
                + ("本次返回空数据" if empty else "记录未通过校验")
                + "；跳过本次完整合约下载，不认定源端永久缺失"
            )
            c.execute(
                text("""UPDATE data_contract_collections SET status='REJECTED',
                reason=:reason,updated_at=now() WHERE scope=:scope"""),
                dict(reason=reason, scope=scope),
            )
            return False
    if (
        not needed
        or {r["dataset"] for r in rows} != set(needed)
        or any(r["status"] != "VALIDATED" for r in rows)
    ):
        return False
    c.execute(
        text("""UPDATE data_contract_collections SET discovery_complete=true,
        reason=NULL,updated_at=now() WHERE scope=:s"""),
        {"s": scope},
    )
    return True


def retry(c: Connection, selected: list[str]) -> None:
    """Explicitly revisit skipped preflight candidates; preserve successful receipts."""
    scopes = list(
        c.scalars(
            text("""SELECT w.scope FROM data_contract_collections w
        JOIN data_sync_contracts d ON d.ts_code=w.scope
        WHERE NOT w.discovery_complete AND w.status='REJECTED'
        AND d.exchange || ':' || d.product=ANY(CAST(:products AS text[]))
        FOR UPDATE OF w"""),
            {"products": selected},
        )
    )
    if not scopes:
        return
    c.execute(
        text("""UPDATE data_sync_jobs j SET status='PENDING',attempts=0,next_at=now(),
        source_generation=NULL,error=NULL WHERE status IN ('WAITING','BLOCKED')
        AND EXISTS (SELECT 1 FROM data_contract_requests cr WHERE cr.request_id=j.request_id
            AND cr.scope=ANY(CAST(:scopes AS text[])))"""),
        {"scopes": scopes},
    )
    c.execute(
        text("""UPDATE data_contract_collections SET status='COLLECTING',reason=NULL,
        updated_at=now() WHERE scope=ANY(CAST(:scopes AS text[]))"""),
        {"scopes": scopes},
    )
    c.execute(
        text("""UPDATE data_sync_contracts SET planned_revision=0
        WHERE ts_code=ANY(CAST(:scopes AS text[]))"""),
        {"scopes": scopes},
    )
