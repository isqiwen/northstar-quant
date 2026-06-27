import pytest

from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.execution.models import OrderRequest
from northstar_quant.risk.models import OrderRiskContext, RiskLimits
from northstar_quant.risk.pretrade import (
    release_order_context,
    reserve_open_orders_in_context,
    reserve_order_context,
    validate_order,
)
from northstar_quant.strategies.pipeline import build_profile_risk_limits


def test_validate_order_rejects_order_notional_from_planned_trade_value():
    limits = RiskLimits(max_order_notional=5000.0)
    order = OrderRequest(
        strategy_id="core_portfolio",
        symbol="SPY",
        side="BUY",
        qty=100.0,
        planned_trade_value=5500.0,
    )

    try:
        validate_order(order, limits)
    except ValueError as exc:
        assert str(exc) == "订单金额超过风控上限"
    else:
        raise AssertionError("预期应触发订单金额风控")


def test_validate_order_rejects_order_notional_from_reference_price():
    limits = RiskLimits(max_order_notional=5000.0)
    order = OrderRequest(
        strategy_id="core_portfolio",
        symbol="QQQ",
        side="BUY",
        qty=100.0,
        reference_price=55.0,
    )

    try:
        validate_order(order, limits)
    except ValueError as exc:
        assert str(exc) == "订单金额超过风控上限"
    else:
        raise AssertionError("预期应基于参考价触发订单金额风控")


def test_validate_order_rejects_order_notional_from_limit_price():
    limits = RiskLimits(max_order_notional=5000.0)
    order = OrderRequest(
        strategy_id="core_portfolio",
        symbol="IWM",
        side="BUY",
        qty=100.0,
        order_type="LMT",
        limit_price=55.0,
    )

    try:
        validate_order(order, limits)
    except ValueError as exc:
        assert str(exc) == "订单金额超过风控上限"
    else:
        raise AssertionError("预期应基于限价触发订单金额风控")


def test_validate_order_requires_price_basis_for_order_notional_limit():
    limits = RiskLimits(max_order_notional=5000.0)
    order = OrderRequest(
        strategy_id="core_portfolio",
        symbol="DIA",
        side="BUY",
        qty=100.0,
    )

    try:
        validate_order(order, limits)
    except ValueError as exc:
        assert str(exc) == "订单金额风控缺少价格基准"
    else:
        raise AssertionError("预期应拒绝缺少金额风控价格基准的订单")


def test_validate_order_passes_when_notional_within_limit():
    limits = RiskLimits(max_order_notional=5000.0)
    order = OrderRequest(
        strategy_id="core_portfolio",
        symbol="TLT",
        side="BUY",
        qty=50.0,
        reference_price=90.0,
        target_weight=0.2,
    )

    validate_order(order, limits)


def test_validate_order_rejects_order_below_min_notional():
    limits = RiskLimits(min_order_notional=10000.0, max_order_notional=50000.0)
    order = OrderRequest(
        strategy_id="core_portfolio",
        symbol="510300.SS",
        side="BUY",
        qty=100.0,
        reference_price=50.0,
    )

    with pytest.raises(ValueError, match="订单金额低于风控下限"):
        validate_order(order, limits)


def test_validate_order_rejects_qty_not_matching_step():
    limits = RiskLimits(order_qty_step=100.0, max_order_notional=None)
    order = OrderRequest(
        strategy_id="core_portfolio",
        symbol="510300.SS",
        side="BUY",
        qty=150.0,
    )

    with pytest.raises(ValueError, match="订单数量不符合交易单位步长"):
        validate_order(order, limits)


def test_validate_order_uses_side_specific_qty_step():
    limits = RiskLimits(buy_qty_step=100.0, max_order_notional=None)
    buy_order = OrderRequest(
        strategy_id="core_portfolio",
        symbol="510300.SS",
        side="BUY",
        qty=150.0,
    )
    sell_order = OrderRequest(
        strategy_id="core_portfolio",
        symbol="510300.SS",
        side="SELL",
        qty=150.0,
    )

    with pytest.raises(ValueError, match="订单数量不符合交易单位步长"):
        validate_order(buy_order, limits)
    validate_order(sell_order, limits)


def test_validate_order_rejects_invalid_limit_price():
    limits = RiskLimits(max_order_notional=None)
    order = OrderRequest(
        strategy_id="core_portfolio",
        symbol="510300.SS",
        side="BUY",
        qty=100.0,
        order_type="LMT",
        limit_price=0.0,
    )

    with pytest.raises(ValueError, match="限价必须大于 0"):
        validate_order(order, limits)


def test_profile_execution_min_trade_value_becomes_pretrade_notional_floor():
    profile = load_trading_profile("cn_etf_daily")

    limits = build_profile_risk_limits(profile)

    assert limits.min_order_notional == 10000.0
    assert limits.buy_qty_step == 100.0


def test_order_context_reservation_can_be_released_after_cancel():
    context = OrderRiskContext(available_cash=1000.0)
    order = OrderRequest(
        strategy_id="core_portfolio",
        symbol="510300.SS",
        side="BUY",
        qty=5.0,
        reference_price=100.0,
    )

    reserve_order_context(context, order)
    release_order_context(context, order)

    assert context.reserved_buy_notional == 0.0


def test_open_orders_reserve_cash_and_sellable_position():
    context = OrderRiskContext(
        available_cash=1000.0,
        position_qty_by_symbol={"510300.SS": 50.0},
    )
    reserve_open_orders_in_context(
        context,
        [
            {
                "symbol": "510500.SS",
                "side": "BUY",
                "remaining_qty": 3.0,
                "limit_price": 100.0,
                "status": "Submitted",
            },
            {
                "symbol": "510300.SS",
                "side": "SELL",
                "remaining_qty": 20.0,
                "status": "PartiallyFilled",
            },
        ],
    )

    validate_order(
        OrderRequest(
            strategy_id="core_portfolio",
            symbol="510500.SS",
            side="BUY",
            qty=7.0,
            reference_price=100.0,
        ),
        RiskLimits(max_order_notional=None),
        context,
    )
    with pytest.raises(ValueError, match="买入订单金额超过可用资金"):
        validate_order(
            OrderRequest(
                strategy_id="core_portfolio",
                symbol="510500.SS",
                side="BUY",
                qty=8.0,
                reference_price=100.0,
            ),
            RiskLimits(max_order_notional=None),
            context,
        )
    with pytest.raises(ValueError, match="卖出订单数量超过可用持仓"):
        validate_order(
            OrderRequest(
                strategy_id="core_portfolio",
                symbol="510300.SS",
                side="SELL",
                qty=31.0,
                reference_price=10.0,
            ),
            RiskLimits(max_order_notional=None),
            context,
        )

    assert context.reserved_buy_notional == 300.0
    assert context.reserved_sell_qty_by_symbol["510300.SS"] == 20.0


def test_open_buy_order_uses_reference_price_when_order_has_no_price():
    context = OrderRiskContext(available_cash=1000.0)

    reserve_open_orders_in_context(
        context,
        [
            {
                "symbol": "510300.SS",
                "side": "BUY",
                "qty": 10.0,
                "filled_qty": 4.0,
                "status": "Submitted",
            }
        ],
        {"510300.SS": 50.0},
    )

    assert context.reserved_buy_notional == 300.0
    assert context.unresolved_open_order_count == 0


def test_unresolved_open_order_blocks_new_orders():
    context = OrderRiskContext(available_cash=1000.0)
    reserve_open_orders_in_context(
        context,
        [
            {
                "symbol": "510300.SS",
                "side": "BUY",
                "remaining_qty": 10.0,
                "status": "Submitted",
            }
        ],
    )

    with pytest.raises(ValueError, match="账户存在无法解析的未完成订单"):
        validate_order(
            OrderRequest(
                strategy_id="core_portfolio",
                symbol="510500.SS",
                side="BUY",
                qty=1.0,
                reference_price=100.0,
            ),
            RiskLimits(max_order_notional=None),
            context,
        )
