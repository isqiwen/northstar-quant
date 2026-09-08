"""Reference window return, using exact Decimal arithmetic."""

from decimal import Decimal

from northstar_quant.factors.definition import Parameter, Requirements


class WindowReturn:
    factor_id = "trend.return"
    revision = "1"
    parameters = (Parameter("window_bars", "收益窗口", 1, 1, 10000, "bars"),)

    def requirements(self, values: dict[str, str | int]) -> Requirements:
        return Requirements(int(values["window_bars"]) + 1)

    def compute(self, closes: tuple[Decimal, ...], values: dict[str, str | int]) -> Decimal:
        return closes[-1] / closes[0] - 1


FACTOR = WindowReturn()
