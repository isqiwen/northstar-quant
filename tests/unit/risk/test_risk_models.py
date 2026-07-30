"""风控模型配置不变量测试。"""

import pytest

from northstar_quant.risk.models import RiskLimits


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_single_weight": -0.1}, "max_single_weight"),
        ({"max_single_weight": 1.1}, "max_single_weight"),
        ({"min_cash_buffer": 1.0}, "min_cash_buffer"),
        ({"min_order_notional": 200.0, "max_order_notional": 100.0}, "不能大于"),
        ({"max_order_qty": 0.0}, "max_order_qty"),
        ({"order_qty_step": -1.0}, "order_qty_step"),
        ({"enforce_price_limit": "false"}, "enforce_price_limit"),
    ],
)
def test_risk_limits_reject_invalid_thresholds(kwargs, message):
    with pytest.raises(ValueError, match=message):
        RiskLimits(**kwargs)
