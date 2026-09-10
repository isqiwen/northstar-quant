"""Read-only account valuation supplied to risk; never an independent ledger."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PortfolioState:
    observed_at: datetime
    equity: Decimal
    position_lots: int
    mark_price: Decimal
