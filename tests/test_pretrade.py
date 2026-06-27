import pytest

from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.execution.models import OrderRequest
from northstar_quant.risk.models import RiskLimits
from northstar_quant.risk.pretrade import validate_order
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
