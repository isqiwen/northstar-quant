"""Tushare's futures download capabilities and their request/row identities."""

from dataclasses import dataclass

EXCHANGES = ("SHFE", "DCE", "CZCE", "CFFEX", "INE", "GFEX")


@dataclass(frozen=True)
class Dataset:
    key: str
    label: str
    api: str
    scope: str
    limit: int
    identity: tuple[str, ...]
    frequency: str = ""
    amount_multiplier: int = 1

    def public(self) -> dict[str, object]:
        return {"key": self.key, "label": self.label}


DATASETS = (
    Dataset("contracts", "合约信息", "fut_basic", "catalog", 10000, ("ts_code",)),
    Dataset("calendar", "交易日历", "fut_trade_cal", "calendar", 10000, ("exchange", "cal_date")),
    *(
        Dataset(
            f"{n}min",
            f"{n} 分钟",
            "ft_mins",
            "contract",
            8000,
            ("ts_code", "trade_time"),
            f"{n}min",
        )
        for n in (1, 5, 15, 30, 60)
    ),
    Dataset(
        "daily",
        "日线",
        "fut_daily",
        "contract",
        2000,
        ("ts_code", "trade_date"),
        amount_multiplier=10000,
    ),
    *(
        Dataset(
            freq,
            label,
            "fut_weekly_monthly",
            "contract",
            6000,
            ("ts_code", "trade_date", "end_date"),
            freq,
            10000,
        )
        for freq, label in (("week", "周线"), ("month", "月线"))
    ),
    Dataset(
        "settlement", "每日结算参数", "fut_settle", "contract", 1600, ("ts_code", "trade_date")
    ),
    Dataset(
        "limits", "涨跌停与最低保证金", "ft_limit", "contract", 4000, ("ts_code", "trade_date")
    ),
    Dataset(
        "holdings", "持仓排名", "fut_holding", "product", 2000, ("trade_date", "symbol", "broker")
    ),
    Dataset(
        "warehouse", "仓单日报", "fut_wsr", "product", 1000, ("trade_date", "symbol", "warehouse")
    ),
    Dataset(
        "mapping",
        "主力与连续合约映射",
        "fut_mapping",
        "continuous",
        2000,
        ("ts_code", "trade_date"),
    ),
    Dataset(
        "adjusted",
        "复权日线",
        "fut_daily_adj",
        "continuous",
        3000,
        ("ts_code", "trade_date"),
        amount_multiplier=10000,
    ),
    Dataset(
        "index",
        "南华指数日线",
        "fut_index_daily",
        "market",
        2000,
        ("ts_code", "trade_date"),
        amount_multiplier=1000,
    ),
    Dataset(
        "weekly_detail",
        "品种交易周报",
        "fut_weekly_detail",
        "product",
        4000,
        ("exchange", "prd", "week"),
        amount_multiplier=100000000,
    ),
)
BY_KEY = {item.key: item for item in DATASETS}
DEFAULT_DATASETS = tuple(BY_KEY)
