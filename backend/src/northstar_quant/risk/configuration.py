"""Immutable risk limits, separate from strategy parameters and simulated costs."""

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from northstar_quant.accounting.amounts import decimal_text

from .sizing import RiskPolicy


@dataclass(frozen=True)
class RiskConfig:
    max_lots: int = 10
    max_gross_notional: Decimal = Decimal("1000000")
    max_margin_fraction: Decimal = Decimal("0.5")
    initial_margin_fraction: Decimal = Decimal("0.1")
    max_adverse_price_move_fraction: Decimal = Decimal("0.1")

    def __post_init__(self) -> None:
        RiskPolicy(**asdict(self), fee_per_lot=Decimal(0), slippage_ticks=0)

    def to_dict(self) -> dict[str, object]:
        return {
            key: decimal_text(value) if isinstance(value, Decimal) else value
            for key, value in asdict(self).items()
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "RiskConfig":
        from northstar_quant.factors.definition import Parameter, parameters

        definitions = (
            Parameter("max_lots", "最大手数", 10, 1, 10000, "lots"),
            Parameter(
                "max_gross_notional",
                "名义金额上限",
                "1000000",
                "0.000000000000000001",
                "999999999999999999",
                "currency",
            ),
            Parameter(
                "max_margin_fraction",
                "保证金占比上限",
                "0.5",
                "0.000000000000000001",
                "1",
                "fraction",
            ),
            Parameter(
                "initial_margin_fraction",
                "初始保证金率",
                "0.1",
                "0.000000000000000001",
                "1",
                "fraction",
            ),
            Parameter(
                "max_adverse_price_move_fraction",
                "不利价格变化边界",
                "0.1",
                "0.000000000000000001",
                "0.999999999999999999",
                "fraction",
            ),
        )
        parsed: dict[str, Any] = {
            key: Decimal(item) if isinstance(item, str) else item
            for key, item in parameters(definitions, value)
        }
        return cls(**parsed)
