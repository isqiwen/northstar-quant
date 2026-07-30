"""AKShare 实际合约行情与金十参考规则的标准化。"""

from __future__ import annotations

from datetime import date, datetime
import math
import re
from typing import Any, cast

import polars as pl

_ACTUAL_DAILY_SOURCE_COLUMNS = {
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_interest",
    "settle",
    "pre_settle",
    "variety",
}
_JIN10_RULE_SOURCE_COLUMNS = {
    "日期",
    "合约代码",
    "现价",
    "涨停板",
    "跌停板",
    "保证金/买开",
    "保证金/卖开",
    "开仓",
    "平今",
    "平昨",
    "手续费公布时间",
    "价格公布时间",
}
_ACTUAL_CONTRACT_PATTERN = re.compile(r"^[A-Z]{1,3}\d{3,4}$")
_FEE_RATE_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)/万分之")
_FEE_PER_LOT_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)元$")


def fetch_exchange_daily(exchange: str, start: date, end: date) -> Any:
    try:
        import akshare as ak  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError("未安装 AKShare，请先执行 `uv sync`") from exc

    try:
        return ak.get_futures_daily(
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            market=exchange,
        )
    except Exception as exc:
        raise RuntimeError(
            f"AKShare 下载 {exchange} 实际合约日线失败；本次不会使用连续合约回退"
        ) from exc


def fetch_jin10_rules(rule_date: date) -> Any:
    try:
        import akshare as ak  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError("未安装 AKShare，请先执行 `uv sync`") from exc

    try:
        return ak.futures_comm_js(date=rule_date.strftime("%Y%m%d"))
    except Exception as exc:
        raise RuntimeError(
            f"AKShare 未能取得 {rule_date} 的期货参考交易规则；"
            "实际合约数据集要求规则逐日完整，本次已停止"
        ) from exc


def standardize_actual_daily_market(
    raw: Any,
    *,
    exchange: str,
    products: set[str],
) -> pl.DataFrame:
    """标准化交易所实际合约日线，并剔除汇总行、期权和无成交价格行。"""

    source_columns = set(getattr(raw, "columns", []))
    missing = sorted(_ACTUAL_DAILY_SOURCE_COLUMNS.difference(source_columns))
    if missing:
        raise ValueError(f"AKShare 返回的 {exchange} 日线缺少字段：{', '.join(missing)}")
    try:
        source = raw.loc[:, sorted(_ACTUAL_DAILY_SOURCE_COLUMNS)].copy()
        for column in (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "open_interest",
            "settle",
            "pre_settle",
        ):
            source[column] = source[column].astype("string")
        frame = pl.from_pandas(source, include_index=False)
    except Exception as exc:
        raise ValueError(f"AKShare 返回的 {exchange} 日线无法转换为表格") from exc
    if frame.is_empty():
        raise ValueError(f"AKShare 未返回 {exchange} 在指定日期范围内的实际合约日线")

    normalized = frame.select(
        pl.col("date")
        .cast(pl.String)
        .str.replace_all("-", "")
        .str.to_date("%Y%m%d", strict=False)
        .alias("date"),
        pl.col("symbol").cast(pl.String).str.strip_chars().str.to_uppercase().alias("symbol"),
        pl.col("variety")
        .cast(pl.String)
        .str.strip_chars()
        .str.to_uppercase()
        .alias("product"),
        pl.lit(exchange).alias("exchange"),
        pl.col("open").cast(pl.Float64, strict=False).alias("open"),
        pl.col("high").cast(pl.Float64, strict=False).alias("high"),
        pl.col("low").cast(pl.Float64, strict=False).alias("low"),
        pl.col("close").cast(pl.Float64, strict=False).alias("close"),
        pl.col("settle").cast(pl.Float64, strict=False).alias("settlement"),
        pl.col("pre_settle").cast(pl.Float64, strict=False).alias("pre_settlement"),
        pl.col("volume").cast(pl.Float64, strict=False).alias("volume"),
        pl.col("open_interest").cast(pl.Float64, strict=False).alias("open_interest"),
    ).filter(pl.col("product").is_in(sorted(products)))

    normalized = normalized.filter(
        pl.col("date").is_not_null()
        & pl.col("symbol").str.contains(_ACTUAL_CONTRACT_PATTERN.pattern)
        & pl.all_horizontal(
            [
                pl.col(column).is_not_null() & (pl.col(column) > 0)
                for column in (
                    "open",
                    "high",
                    "low",
                    "close",
                    "settlement",
                    "pre_settlement",
                )
            ]
        )
        & pl.col("volume").is_not_null()
        & (pl.col("volume") >= 0)
        & pl.col("open_interest").is_not_null()
        & (pl.col("open_interest") >= 0)
    )
    if normalized.is_empty():
        raise ValueError(f"AKShare 返回的 {exchange} 日线没有目标品种的有效实际合约")
    missing_products = sorted(products.difference(normalized.get_column("product").unique().to_list()))
    if missing_products:
        raise ValueError(
            f"AKShare 返回的 {exchange} 日线缺少目标品种：{', '.join(missing_products)}"
        )
    if normalized.group_by(["date", "symbol"]).len().filter(pl.col("len") > 1).height:
        raise ValueError(f"AKShare 返回的 {exchange} 日线存在重复 date/symbol")
    return normalized.sort(["date", "product", "symbol"])


def standardize_jin10_rule_snapshot(
    raw: Any,
    *,
    selection_date: date,
    products: set[str],
) -> pl.DataFrame:
    """把金十主力合约参考规则解析为结构化快照。"""

    try:
        frame = pl.from_pandas(raw, include_index=False)
    except Exception as exc:
        raise ValueError(f"{selection_date} 的参考交易规则无法转换为表格") from exc
    missing = sorted(_JIN10_RULE_SOURCE_COLUMNS.difference(frame.columns))
    if missing:
        raise ValueError(
            f"{selection_date} 的参考交易规则缺少字段：{', '.join(missing)}"
        )
    records: list[dict[str, object]] = []
    for row in frame.to_dicts():
        symbol = str(row["合约代码"]).strip().upper()
        match = re.match(r"^([A-Z]{1,3})\d{3,4}$", symbol)
        if match is None or match.group(1) not in products:
            continue
        source_date = _source_date(row["日期"], selection_date, symbol)
        if source_date != selection_date:
            raise ValueError(
                f"{selection_date}/{symbol} 的规则快照实际日期为 {source_date}"
            )
        product = match.group(1)
        open_per_lot, open_rate = _parse_commission(row["开仓"], selection_date, symbol)
        close_today_per_lot, close_today_rate = _parse_commission(
            row["平今"],
            selection_date,
            symbol,
        )
        close_per_lot, close_rate = _parse_commission(row["平昨"], selection_date, symbol)
        upper_limit = _positive_number(row["涨停板"], selection_date, symbol, "涨停板")
        lower_limit = _positive_number(row["跌停板"], selection_date, symbol, "跌停板")
        if lower_limit >= upper_limit:
            raise ValueError(f"{selection_date}/{symbol} 的参考涨跌停价格无效")
        limit_midpoint = (upper_limit + lower_limit) / 2
        records.append(
            {
                "selection_date": selection_date,
                "product": product,
                "active_contract": symbol,
                "_published_at": max(
                    _source_timestamp(
                        row["手续费公布时间"],
                        selection_date,
                        symbol,
                        "手续费公布时间",
                    ),
                    _source_timestamp(
                        row["价格公布时间"],
                        selection_date,
                        symbol,
                        "价格公布时间",
                    ),
                ),
                "margin_rate": max(
                    _parse_percentage(row["保证金/买开"], selection_date, symbol),
                    _parse_percentage(row["保证金/卖开"], selection_date, symbol),
                ),
                "commission_open_per_lot": open_per_lot,
                "commission_open_rate": open_rate,
                "commission_close_per_lot": close_per_lot,
                "commission_close_rate": close_rate,
                "commission_close_today_per_lot": close_today_per_lot,
                "commission_close_today_rate": close_today_rate,
                "upper_limit_rate": upper_limit / limit_midpoint - 1,
                "lower_limit_rate": 1 - lower_limit / limit_midpoint,
            }
        )

    if not records:
        raise ValueError(f"{selection_date} 的参考交易规则没有目标品种")
    candidates: dict[str, list[dict[str, object]]] = {}
    for record in records:
        candidates.setdefault(str(record["product"]), []).append(record)
    selected: list[dict[str, object]] = []
    for product, product_records in candidates.items():
        latest = max(
            cast(datetime, record["_published_at"])
            for record in product_records
        )
        latest_records = [
            record for record in product_records if record["_published_at"] == latest
        ]
        if len(latest_records) != 1:
            raise ValueError(
                f"{selection_date}/{product} 的最新参考交易规则无法唯一确定主力合约"
            )
        latest_record = dict(latest_records[0])
        latest_record.pop("_published_at")
        selected.append(latest_record)
    result = pl.DataFrame(selected)
    missing_products = sorted(products.difference(result.get_column("product").to_list()))
    if missing_products:
        raise ValueError(
            f"{selection_date} 的参考交易规则缺少品种：{', '.join(missing_products)}"
        )
    return result.sort("product")


def _parse_percentage(value: object, rule_date: date, symbol: str) -> float:
    text = str(value).strip()
    if not text.endswith("%"):
        raise ValueError(f"{rule_date}/{symbol} 的保证金格式无法解析：{text}")
    try:
        result = float(text[:-1]) / 100
    except ValueError as exc:
        raise ValueError(f"{rule_date}/{symbol} 的保证金格式无法解析：{text}") from exc
    if not 0 < result <= 1:
        raise ValueError(f"{rule_date}/{symbol} 的保证金比例不在 (0, 1]")
    return result


def _parse_commission(value: object, rule_date: date, symbol: str) -> tuple[float, float]:
    text = str(value).strip().replace(" ", "")
    rate_match = _FEE_RATE_PATTERN.match(text)
    if rate_match:
        return 0.0, float(rate_match.group(1)) / 10_000
    per_lot_match = _FEE_PER_LOT_PATTERN.match(text)
    if per_lot_match:
        return float(per_lot_match.group(1)), 0.0
    raise ValueError(f"{rule_date}/{symbol} 的手续费格式无法解析：{text}")


def _positive_number(value: object, rule_date: date, symbol: str, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{rule_date}/{symbol} 的 {field} 必须是正数")
    try:
        result = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{rule_date}/{symbol} 的 {field} 必须是正数") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{rule_date}/{symbol} 的 {field} 必须是正数")
    return result


def _source_date(value: object, rule_date: date, symbol: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{rule_date}/{symbol} 的规则日期无法解析：{value}") from exc


def _source_timestamp(
    value: object,
    rule_date: date,
    symbol: str,
    field: str,
) -> datetime:
    try:
        return datetime.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{rule_date}/{symbol} 的 {field} 无法解析：{value}") from exc
