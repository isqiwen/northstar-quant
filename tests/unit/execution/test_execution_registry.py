"""执行计划注册表的画像执行政策测试。"""

from datetime import UTC, datetime

import pytest

from northstar_quant.execution.models import (
    BrokerStateSnapshot,
    FillSnapshot,
    RebalanceOrderPlan,
)
from northstar_quant.execution.registry import (
    _apply_rebalance_tolerance,
    project_broker_state_positions,
)


def test_rebalance_tolerance_filters_only_provably_small_weight_changes():
    small_change = RebalanceOrderPlan(
        symbol="AAA",
        side="BUY",
        qty=1.0,
        current_qty=10.0,
        target_weight=0.105,
        execution_reference_price=100.0,
    )
    large_change = RebalanceOrderPlan(
        symbol="BBB",
        side="BUY",
        qty=5.0,
        current_qty=10.0,
        target_weight=0.15,
        execution_reference_price=100.0,
    )
    incomplete = RebalanceOrderPlan(
        symbol="CCC",
        side="BUY",
        qty=1.0,
        target_weight=0.1,
    )

    result = _apply_rebalance_tolerance(
        [small_change, large_change, incomplete],
        tolerance=0.01,
        equity=10_000.0,
    )

    assert [plan.symbol for plan in result] == ["BBB", "CCC"]


def test_position_projection_rejects_missing_snapshot_and_fill_timestamps():
    with pytest.raises(ValueError, match="BROKER_STATE_TIMESTAMP_REQUIRED"):
        project_broker_state_positions(BrokerStateSnapshot())

    with pytest.raises(ValueError, match="BROKER_FILL_TIMESTAMP_REQUIRED"):
        project_broker_state_positions(
            BrokerStateSnapshot(
                asof=datetime(2024, 1, 2, tzinfo=UTC),
                fills=[
                    FillSnapshot(
                        broker_order_id="1",
                        symbol="RB2405",
                        qty=1.0,
                        price=100.0,
                        side="BUY",
                    )
                ],
            )
        )
