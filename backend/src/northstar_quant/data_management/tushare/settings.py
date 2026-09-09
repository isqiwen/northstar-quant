"""Small, non-secret automatic synchronization settings owned by Data Hub."""

from typing import Any

from sqlalchemy import Engine, text

from ..maintenance import library_write
from . import credentials
from .catalog import DATASETS
from .store import serial, settings


def configure(engine: Engine, *, revision: int, enabled: bool) -> dict[str, Any]:
    if enabled and not credentials.configured():
        raise ValueError("请先在页面保存 Tushare token")
    with library_write(engine), engine.begin() as connection:
        updated = connection.execute(
            text("""UPDATE data_sync_settings SET
            revision=revision+1,enabled=:enabled,refresh_at=now(),
            planned_at=NULL,error=NULL,updated_at=now()
            WHERE revision=:revision RETURNING revision"""),
            {
                "revision": revision,
                "enabled": enabled,
            },
        ).scalar_one_or_none()
        if updated is None:
            raise ValueError("设置已被另一页面修改，请刷新后重试")
        connection.execute(
            text(
                "UPDATE data_sync_jobs SET status='PENDING',attempts=0,next_at=now() WHERE "
                "status='BLOCKED'"
            )
        )
    return status(engine)


def status(engine: Engine) -> dict[str, Any]:
    config = settings(engine)
    with engine.connect() as connection:
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
        recent = [
            serial(row)
            for row in connection.execute(
                text("""
            SELECT request_id,dataset,scope,start_at,end_at,status,attempts,next_at,error,
                receipt_id,updated_at FROM data_sync_jobs
            ORDER BY updated_at DESC,request_id LIMIT 50
        """)
            ).mappings()
        ]
        unplanned = connection.scalar(
            text("SELECT count(*) FROM data_sync_contracts WHERE planned_revision<>:r"),
            {"r": config["revision"]},
        )
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
        "jobs": recent,
        "unplanned_contracts": unplanned,
    }
