"""技术指标的输入校验与列分组辅助函数。"""

from __future__ import annotations

from collections.abc import Iterable

import polars as pl


def validate_window(window: int, *, parameter: str = "window") -> None:
    """确保滚动窗口为正整数。"""

    if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
        raise ValueError(f"{parameter} 必须是大于 0 的整数")


def require_columns(data: pl.DataFrame, columns: Iterable[str]) -> None:
    """确保行情包含指标所需列。"""

    missing = sorted(set(columns).difference(data.columns))
    if missing:
        raise ValueError(f"指标计算缺少必需行情列: {', '.join(missing)}")


def prepare_frame(
    data: pl.DataFrame,
    *,
    required_columns: Iterable[str],
    group_by: str | None = None,
    order_by: str | None = None,
) -> pl.DataFrame:
    """校验列并在指定时按分组和时间字段排序。

    技术指标的先后顺序会影响结果；调用方使用 ``order_by`` 时，函数在计算前
    显式排序，避免数据下载顺序变化造成静默偏差。
    """

    columns = [*required_columns]
    if group_by:
        columns.append(group_by)
    if order_by:
        columns.append(order_by)
    require_columns(data, columns)

    sort_columns = [column for column in (group_by, order_by) if column]
    return data.sort(sort_columns) if sort_columns else data


def grouped(expression: pl.Expr, group_by: str | None) -> pl.Expr:
    """仅在指定分组字段时将表达式限制在各分组内部。"""

    return expression.over(group_by) if group_by else expression


def temporary_column_name(data: pl.DataFrame, label: str) -> str:
    """生成不会覆盖调用方数据列的内部临时列名。"""

    base = f"__indicator_{label}"
    candidate = base
    suffix = 1
    while candidate in data.columns:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate
