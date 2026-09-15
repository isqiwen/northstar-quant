"""Supplier-derived research series values, distinct from executable contracts."""

from collections.abc import Iterator
from typing import Any

from ..catalog.partitioned import NUMERIC
from .normalization import decimal_text
from .standard_rows import COLUMNS, _day

DOMAINS = {
    "continuous": "market/futures/continuous/daily",
    "mapping": "market/futures/mappings/continuous_to_contract",
    "adjusted": "market/futures/adjusted/daily",
    "index": "market/futures/indices/daily",
}
KINDS = dict(continuous="CONTINUOUS", mapping="MAPPING", adjusted="ADJUSTED", index="INDEX")


def records(
    dataset: str,
    scope: str,
    exchange: str,
    product: str,
    rows: list[dict[str, Any]],
    targets: dict[str, Any],
) -> Iterator[dict[str, Any]]:
    domain = DOMAINS[dataset]
    for row in rows:
        if row["ts_code"] != scope:
            continue
        day = _day(row["trade_date"])
        assert day is not None
        values: dict[str, Any] = dict(series=scope, trading_day=day)
        if dataset == "index":
            if not scope.endswith(".NH"):
                raise ValueError("指数响应包含非南华代码")
            values["publisher"] = "NANHUA"
            partition = f"{domain}/publisher=NANHUA/index={scope}/year={day[:4]}"
        else:
            values.update(exchange=exchange, product=product)
            partition = f"{domain}/exchange={exchange}/product={product}/year={day[:4]}"
        if dataset == "mapping":
            mapped = row.get("mapping_ts_code")
            target = targets.get(str(mapped))
            if (
                not target
                or target["kind"] != "1"
                or (target["exchange"], target["product"]) != (exchange, product)
            ):
                raise ValueError("映射目标未核实为同交易所/品种的真实合约")
            values["mapped_contract"] = str(mapped).split(".")[0]
            values["supplier_mapped_contract"] = mapped
        else:
            for native, field in COLUMNS.items():
                if native not in row:
                    continue
                value = row[native]
                values[field] = (
                    decimal_text(value) if field in NUMERIC and value is not None else value
                )
            values["price_basis"] = (
                "SUPPLIER_ADJUSTED"
                if dataset == "adjusted"
                else "INDEX_POINTS"
                if dataset == "index"
                else "SUPPLIER_CONTINUOUS"
            )
        yield dict(domain=domain, partition=partition, identity=[scope, day], values=values)
