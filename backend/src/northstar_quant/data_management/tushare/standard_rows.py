"""Tushare adapter into Northstar catalog values; endpoints stay in provenance."""

import io
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from ..catalog.partitioned import NUMERIC
from ..files import SourceFiles
from .normalization import decimal_text

DOMAINS = {
    "contracts": "reference/futures/contracts",
    "calendar": "reference/futures/trading_calendar",
    "daily": "market/futures/contracts/daily",
    "week": "market/futures/contracts/weekly",
    "month": "market/futures/contracts/monthly",
    "settlement": "reference/futures/settlement",
    "limits": "reference/futures/trading_limits",
    "holdings": "market/futures/products/positions",
    "warehouse": "market/futures/products/warehouse_receipts",
    "weekly_detail": "market/futures/products/weekly_statistics",
    **{f"{n}min": f"market/futures/contracts/minute/interval={n}min" for n in (1, 5, 15, 30, 60)},
}
COLUMNS = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "vol": "volume",
    "oi": "open_interest",
    "amount_cny": "turnover_cny",
    "settle": "settlement_price",
    "pre_settle": "previous_settlement_price",
    "observation_status": "observation_status",
    "trading_fee_rate": "fee_rate_raw",
    "trading_fee": "fee_amount_raw",
    "delivery_fee": "delivery_fee_raw",
    "offset_today_fee": "close_today_fee_raw",
    "long_margin_rate": "long_margin_raw",
    "short_margin_rate": "short_margin_raw",
    "b_hedging_margin_rate": "long_hedge_margin_raw",
    "s_hedging_margin_rate": "short_hedge_margin_raw",
    "up_limit": "upper_limit",
    "down_limit": "lower_limit",
    "m_ratio": "minimum_margin_raw",
    "broker": "member",
    "long_hld": "long_positions",
    "short_hld": "short_positions",
    "warehouse": "warehouse",
    "wh_id": "warehouse_id",
    "unit": "quantity_unit",
    "week": "period_label",
    "week_date": "period_end",
    "name": "name",
}


def _day(value: Any) -> str | None:
    if value is None or value == "":
        return None
    value = str(value)
    return (
        datetime.strptime(value[:10], "%Y-%m-%d").date().isoformat()
        if "-" in value
        else datetime.strptime(value, "%Y%m%d").date().isoformat()
    )


def records(manifest: dict[str, Any], source: SourceFiles) -> Iterator[dict[str, Any]]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    exchange, product, scope = (manifest[k] for k in ("exchange", "product", "scope"))
    contract = scope.split(".")[0]
    for domain, identity, values in (
        ("reference/futures/exchanges", [exchange], dict(exchange=exchange)),
        (
            "reference/futures/products",
            [exchange, product],
            dict(exchange=exchange, product=product),
        ),
        (
            "reference/futures/contracts",
            [exchange, product, contract],
            dict(
                exchange=exchange,
                product=product,
                contract=contract,
                supplier_contract=scope,
                listing_date=manifest.get("listing_date"),
                last_trade_date=manifest.get("last_trade_date"),
                last_delivery_date=manifest.get("last_delivery_date"),
                delivery_month=manifest.get("delivery_month"),
                **manifest.get("reference", {}),
            ),
        ),
    ):
        yield dict(
            domain=domain,
            partition=domain
            + f"/exchange={exchange}"
            + (f"/product={product}" if "exchanges" not in domain else ""),
            identity=identity,
            values=values,
        )
    for item in manifest["inputs"]:
        dataset = item["dataset"]
        # A whole exchange metadata response is private provenance, not permission
        # to publish unrelated or still-active contracts from that response.
        if dataset == "contracts":
            continue
        if dataset not in DOMAINS:
            raise ValueError("真实合约快照不能包含连续、复权或市场指数")
        table = pq.ParquetFile(io.BytesIO(source.read(item["parquet_hash"], item["parquet_bytes"])))
        domain = DOMAINS[dataset]
        for batch in table.iter_batches(batch_size=512):
            for row in batch.to_pylist():
                if row.get("ts_code") is not None and row["ts_code"] != scope:
                    raise ValueError("标准化输入混入其他合约")
                if dataset == "calendar":
                    day = _day(row.get("cal_date"))
                    values = dict(
                        exchange=exchange,
                        calendar_date=day,
                        is_open={0: False, 1: True, "0": False, "1": True}.get(row.get("is_open")),
                    )
                    identity = [exchange, day]
                else:
                    day = _day(
                        row.get("trade_date") or row.get("trade_time") or row.get("week_date")
                    )
                    values = {
                        target: (
                            decimal_text(row[native])
                            if target in NUMERIC and row.get(native) is not None
                            else row.get(native)
                        )
                        for native, target in COLUMNS.items()
                        if native in row
                    }
                    if "period_end" in values:
                        values["period_end"] = _day(values["period_end"])
                    values.update(exchange=exchange, product=product)
                    if dataset in {"holdings", "warehouse", "weekly_detail"}:
                        values["trading_day"] = _day(row.get("trade_date"))
                        identity = [
                            exchange,
                            product,
                            day,
                            row.get("week"),
                            row.get("broker"),
                            row.get("warehouse"),
                        ]
                    else:
                        values.update(
                            contract=contract,
                            trading_day=_day(row.get("trade_date")),
                            timestamp_label=row.get("trade_time"),
                            period_end=_day(row.get("end_date")),
                        )
                        identity = [
                            exchange,
                            product,
                            contract,
                            row.get("trade_time") or day,
                            values["period_end"],
                        ]
                    if dataset == "warehouse" and "volume" in values:
                        values["warehouse_volume"] = values.pop("volume")
                if day is None:
                    raise ValueError("领域记录缺少可核实的分区日期")
                partition = domain + f"/exchange={exchange}"
                if dataset != "calendar":
                    partition += f"/product={product}"
                partition += f"/year={day[:4]}"
                if dataset.endswith("min"):
                    partition += f"/month={day[5:7]}"
                yield dict(domain=domain, partition=partition, identity=identity, values=values)
