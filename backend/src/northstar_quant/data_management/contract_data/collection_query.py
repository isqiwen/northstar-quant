"""One paginated collection row per real contract, with its own lifecycle facts."""

from typing import Any

from sqlalchemy import Engine, text

from ..exploration.instruments import display_name
from ..tushare.store import serial
from .lifecycle import describe


def search(
    engine: Engine,
    *,
    exchange: str,
    product: str,
    search: str,
    status: str,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    if status and status not in {"COLLECTING", "VERIFYING", "REJECTED", "PUBLISHED"}:
        raise ValueError("未知合约状态")
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("分页范围无效")
    conditions = []
    params: dict[str, Any] = dict(offset=offset, limit=limit, exchange=exchange)
    for field, value in (("d.exchange", exchange), ("d.product", product), ("w.status", status)):
        if value:
            key = field.split(".")[1]
            conditions.append(f"{field}=:{key}")
            params[key] = value
    if search.strip():
        conditions.append("(d.ts_code ILIKE :search OR d.details->>'name' ILIKE :search)")
        literal = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params["search"] = f"%{literal}%"
    where = " AND ".join(conditions) or "true"
    base = "FROM data_contract_collections w JOIN data_sync_contracts d ON d.ts_code=w.scope"
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
        total = c.scalar(text(f"SELECT count(*) {base} WHERE {where}"), params)
        rows = c.execute(
            text(f"""SELECT w.*,d.exchange,d.product,d.kind,d.details
            {base} WHERE {where} ORDER BY w.end_date,d.exchange,d.product,w.scope
            LIMIT :limit OFFSET :offset"""),
            params,
        ).mappings()
        items = []
        for row in rows:
            item = {**serial(row), **describe(row)}
            item["display_name"] = display_name(
                row["scope"], row["details"].get("name"), row["exchange"], row["product"]
            )
            item.pop("details")
            item.pop("kind")
            items.append(item)
        exchanges = list(c.scalars(text(f"SELECT DISTINCT d.exchange {base} ORDER BY d.exchange")))
        products = list(
            c.scalars(
                text(f"""SELECT DISTINCT d.product {base}
            WHERE :exchange='' OR d.exchange=:exchange ORDER BY d.product"""),
                params,
            )
        )
        return dict(
            total=total,
            offset=offset,
            limit=limit,
            items=items,
            exchanges=exchanges,
            products=products,
        )
