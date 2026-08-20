"""指标的轻量公共类型。

这里刻意只定义协议和元数据，不要求普通指标继承基类。批处理技术指标通常
是 ``DataFrame -> DataFrame`` 的纯函数，强制套入类层次会降低可组合性。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import polars as pl


@dataclass(frozen=True, slots=True)
class IndicatorSpec:
    """指标的稳定描述，用于展示、配置校验和未来的注册发现。"""

    name: str
    category: str
    input_columns: tuple[str, ...]
    description: str


class Indicator(Protocol):
    """有状态或配置化指标可选择实现的协议。

    普通无状态指标无需实现此协议，直接导出函数即可。
    """

    spec: IndicatorSpec

    def calculate(self, data: pl.DataFrame) -> pl.DataFrame:
        """根据行情数据计算并返回附加指标列后的数据。"""
