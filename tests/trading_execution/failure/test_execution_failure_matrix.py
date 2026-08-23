"""P5-WP10 execution failure paths must fail closed before new risk is created."""

from __future__ import annotations

import pytest

from northstar_quant.portfolio_risk.limits.models import (
    OrderRiskContext,
    RiskLimits,
    SymbolTradeState,
)
from northstar_quant.portfolio_risk.risk.pretrade import validate_order
from northstar_quant.trading_execution.execution.models import OrderRequest


def test_price_limit_blocks_buy_at_upper_limit_before_submission():
    order = OrderRequest(
        strategy_id="failure-matrix",
        symbol="RB2610",
        side="BUY",
        qty=1.0,
        order_type="LMT",
        limit_price=3_600.0,
        reference_price=3_600.0,
    )
    limits = RiskLimits(
        max_order_notional=None,
        enforce_tradeable_state=True,
        enforce_price_limit=True,
    )
    context = OrderRiskContext(
        available_cash=100_000.0,
        trade_state_by_symbol={
            "RB2610": SymbolTradeState(
                is_suspended=False,
                limit_up_price=3_600.0,
                limit_down_price=3_200.0,
            )
        },
    )

    with pytest.raises(ValueError, match="价格"):
        validate_order(order, limits, context)
