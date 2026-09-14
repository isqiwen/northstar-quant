"""Display metadata for catalog instruments, independent of fixed market identities."""

from typing import Any

from sqlalchemy import Engine, text

from .catalog import BROWSABLE_DATASETS

# Display-only CFFEX names; official product catalog consulted 2026-09-14.
_CFFEX_NAMES = {
    "IF": "沪深300股指",
    "IH": "上证50股指",
    "IC": "中证500股指",
    "IM": "中证1000股指",
    "TS": "2年期国债",
    "TF": "5年期国债",
    "T": "10年期国债",
    "TL": "30年期国债",
}


def localized_products(search: str) -> list[str]:
    return [code for code, name in _CFFEX_NAMES.items() if search and search in name]


def display_name(scope: str, name: str | None, exchange: str | None, product: str | None) -> str:
    if name and any("\u4e00" <= c <= "\u9fff" for c in name):
        return name
    if exchange == "CFFEX" and product in _CFFEX_NAMES:
        symbol = scope.split(".")[0]
        assert product is not None
        suffix = symbol[len(product) :] if symbol.startswith(product) else ""
        return _CFFEX_NAMES[product] + suffix
    return name or scope


def describe(engine: Engine, scope: str) -> dict[str, Any]:
    with engine.connect() as c:
        metadata = (
            c.execute(
                text("""SELECT exchange,product,details->>'name' AS name
            FROM data_sync_contracts WHERE ts_code=:scope"""),
                {"scope": scope},
            )
            .mappings()
            .one_or_none()
        )
        periods = list(
            c.execute(
                text("""SELECT DISTINCT j.dataset
            FROM data_sync_jobs j JOIN data_sync_receipts r ON r.receipt_id=j.receipt_id
            WHERE j.scope=:scope AND j.status<>'SPLIT' AND r.row_count>0
            AND j.dataset=ANY(:datasets)"""),
                {"scope": scope, "datasets": list(BROWSABLE_DATASETS)},
            ).scalars()
        )
    row = dict(metadata) if metadata else {}
    return {
        "scope": scope,
        "name": display_name(scope, row.get("name"), row.get("exchange"), row.get("product")),
        "exchange": row.get("exchange") or "",
        "periods": [p for p in BROWSABLE_DATASETS if p in periods],
    }
