"""Position in the completed-close range; constant prices are neutral, not missing."""

from decimal import Decimal

from northstar_quant.factors.definition import Parameter, Requirements


class RangePosition:
    factor_id = "range.position"
    revision = "1"
    parameters = (Parameter("window_bars", "区间窗口", 5, 1, 10000, "bars"),)

    def requirements(self, values: dict[str, str | int]) -> Requirements:
        return Requirements(int(values["window_bars"]) + 1)

    def compute(self, closes: tuple[Decimal, ...], values: dict[str, str | int]) -> Decimal:
        low, high = min(closes), max(closes)
        return Decimal("0.5") if high == low else (closes[-1] - low) / (high - low)


FACTOR = RangePosition()
