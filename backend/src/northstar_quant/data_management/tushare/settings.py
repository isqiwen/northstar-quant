"""Small, non-secret automatic synchronization settings owned by Data Hub."""

from typing import Any

from sqlalchemy import Engine, text

from ..maintenance import library_write
from . import credentials, job_query, scheduling
from . import products as product_selection
from .catalog import DATASETS
from .store import serial, settings


def configure(
    engine: Engine,
    *,
    revision: int,
    enabled: bool,
    products: list[str] | None = None,
    retry_skipped: bool = False,
) -> dict[str, Any]:
    if enabled and not credentials.configured():
        raise ValueError("请先在页面保存 Tushare token")
    with library_write(engine), engine.begin() as connection:
        if products is not None:
            product_selection.validate(connection, products)
        updated = connection.execute(
            text("""UPDATE data_sync_settings SET
            revision=revision+1,enabled=:enabled,refresh_at=now(),
            selected_products=COALESCE(CAST(:products AS text[]),selected_products),
            planned_at=NULL,error=NULL,updated_at=now()
            WHERE revision=:revision RETURNING revision"""),
            {
                "revision": revision,
                "enabled": enabled,
                "products": products,
            },
        ).scalar_one_or_none()
        if updated is None:
            raise ValueError("设置已被另一页面修改，请刷新后重试")
        if enabled:
            # Explicit restart may follow a changed token. Revisit API permission
            # failures; the scheduler still prevents unselected data downloads.
            connection.execute(
                text("""UPDATE data_sync_jobs SET status='PENDING',
                attempts=0,next_at=now() WHERE status='BLOCKED'
                AND (error LIKE 'Tushare 权限不足%' OR dataset='contracts' OR EXISTS (
                    SELECT 1 FROM data_contract_requests cr
                    JOIN data_contract_collections w ON w.scope=cr.scope
                    JOIN data_sync_contracts d ON d.ts_code=w.scope
                    WHERE cr.request_id=data_sync_jobs.request_id
                    AND w.status IN ('COLLECTING','VERIFYING')
                    AND d.exchange || ':' || d.product=ANY(
                        (SELECT selected_products FROM data_sync_settings)::text[])))""")
            )
        if retry_skipped:
            if not enabled or not products:
                raise ValueError("重新探查需要选择品种并启动采集")
            product_selection.retry(connection, products)
    return status(engine)


def status(engine: Engine) -> dict[str, Any]:
    config = settings(engine)
    with engine.connect() as connection:
        config["products"] = product_selection.catalog(connection)
        config["origins"] = product_selection.origins(connection)
        config["history_end"], lanes = scheduling.progress(connection)
        rows = [
            serial(row)
            for row in connection.execute(
                text("""
            SELECT dataset,status,count(*) AS windows, sum(attempts) AS requests,
                max(checked_at) AS checked_at FROM data_sync_jobs GROUP BY dataset,status
            ORDER BY dataset,status
        """)
            ).mappings()
        ]
        unplanned = connection.scalar(
            text(
                "SELECT count(*) FROM data_sync_contracts WHERE kind='1' "
                "AND exchange || ':' || product=ANY(CAST(:products AS text[])) "
                "AND details->>'last_ddate'<to_char(CURRENT_DATE,'YYYYMMDD') "
                "AND (planned_revision<>:r OR planning_error IS NOT NULL)"
            ),
            {"r": config["revision"], "products": config["selected_products"]},
        )
        config["catalog_ready"] = connection.scalar(
            text("""SELECT coalesce(bool_and(status='VALIDATED'),false)
            FROM data_sync_jobs WHERE dataset='contracts'""")
        )
        config["catalog_errors"] = [
            serial(row)
            for row in connection.execute(
                text("""
            SELECT ts_code,planning_error FROM data_sync_contracts
            WHERE planning_error IS NOT NULL ORDER BY ts_code LIMIT 50
        """)
            ).mappings()
        ]
        from .planning import target_day

        config["targets"] = [
            serial(row)
            for row in connection.execute(
                text("""
            SELECT exchange,max(cal_date) AS target_trading_day FROM data_sync_calendar
            WHERE is_open AND cal_date<=:target GROUP BY exchange ORDER BY exchange
        """),
                {"target": target_day()},
            ).mappings()
        ]
    return {
        "settings": config,
        "token_configured": credentials.configured(),
        "datasets": [item.public() for item in DATASETS],
        "progress": rows,
        "lanes": lanes,
        "jobs": job_query.search(engine, dataset="", status="", offset=0, limit=50)["items"],
        "unplanned_contracts": unplanned,
    }
