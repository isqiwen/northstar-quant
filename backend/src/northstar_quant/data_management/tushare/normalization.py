"""Exact numerical meaning of supplier bars, independent of their JSON spelling.

This does not infer sessions, trading days, instrument terms or first availability.
Raw supplier bytes remain separately retained by the downloader.
"""

from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from .catalog import BY_KEY

RULE = "tushare-numbers/1"
PRECISION = 38
SCALE = 12
BAR_APIS = {"ft_mins", "fut_daily", "fut_weekly_monthly", "fut_daily_adj", "fut_index_daily"}
_FIELDS = frozenset(
    {
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "pre_settle",
        "settle",
        "delv_settle",
        "change1",
        "change2",
        "vol",
        "oi",
        "oi_chg",
        "amount",
        "amount_cny",
    }
)


def fields(dataset: str) -> frozenset[str]:
    return _FIELDS if BY_KEY[dataset].api in BAR_APIS else frozenset()


def decimal_text(value: Any) -> str:
    """No float conversion, context-dependent rounding or enormous exponent expansion."""
    if type(value) not in (str, int, Decimal):
        raise ValueError("数值必须是精确十进制值")
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError("数值字段无效") from None
    if not number.is_finite():
        raise ValueError("数值不是有限十进制值")
    if number == 0:
        return "0"
    sign, digits, exponent = number.as_tuple()
    assert isinstance(exponent, int)
    # Trailing zeros do not change precision or economic identity.
    while digits and digits[-1] == 0:
        digits = digits[:-1]
        exponent += 1
    if exponent < -SCALE or len(digits) + exponent > PRECISION - SCALE:
        raise ValueError("数值超过 Decimal(38,12) 精确范围；拒绝舍入")
    return format(Decimal((sign, digits, exponent)), "f")


def normalize(row: dict[str, Any], dataset: str) -> None:
    for field in sorted(fields(dataset) - {"amount_cny"}):
        if row.get(field) is not None:
            try:
                row[field] = decimal_text(row[field])
            except ValueError as error:
                raise ValueError(f"{field}：{error}") from None
    # amount_cny is owned by this transformation, never by an upstream extra field.
    row.pop("amount_cny", None)
    if row.get("amount") is not None:
        with localcontext() as context:
            context.prec = PRECISION + 8
            row["amount_cny"] = decimal_text(
                Decimal(decimal_text(row["amount"])) * BY_KEY[dataset].amount_multiplier
            )


def evidence(dataset: str) -> dict[str, Any]:
    return {
        "rule": RULE,
        "numeric_fields": sorted(fields(dataset)),
        "decimal_precision": PRECISION,
        "decimal_scale": SCALE,
        "amount_cny_multiplier": BY_KEY[dataset].amount_multiplier,
        "quantity_basis": "SUPPLIER_REPORTED_NOT_SIDE_ADJUSTED",
    }


def response_row(row: dict[str, Any]) -> dict[str, Any]:
    """Preserve the browser/API's exact-string interface when reading Decimal columns."""
    return {
        key: decimal_text(value) if isinstance(value, Decimal) else value
        for key, value in row.items()
    }
