"""Simulated starting capital and cost assumptions, never broker account facts."""

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from northstar_quant.accounting.amounts import decimal_text
from northstar_quant.factors.definition import Parameter, parameters

_DEFINITIONS = (
    Parameter(
        "initial_cash",
        "模拟初始资金",
        "100000",
        "0.000000000000000001",
        "999999999999999999",
        "currency",
    ),
    Parameter("fee_per_lot", "每手费用", "2", "0", "999999999999999999", "currency/lot"),
    Parameter("slippage_ticks", "滑点", 1, 0, 10000, "ticks"),
)


@dataclass(frozen=True)
class SimulationConfig:
    initial_cash: Decimal = Decimal("100000")
    fee_per_lot: Decimal = Decimal("2")
    slippage_ticks: int = 1

    def __post_init__(self) -> None:
        parameters(_DEFINITIONS, self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            key: decimal_text(value) if isinstance(value, Decimal) else value
            for key, value in asdict(self).items()
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "SimulationConfig":
        parsed: dict[str, Any] = {
            key: Decimal(item) if isinstance(item, str) else item
            for key, item in parameters(_DEFINITIONS, value)
        }
        return cls(**parsed)
