from datetime import UTC, datetime

from northstar_quant.trading_execution.execution.models import PositionSnapshot


def test_position_snapshot_preserves_futures_long_short_and_risk_components():
    snapshot = PositionSnapshot(
        symbol="RB2610",
        qty=2.0,
        long_today_qty=1.0,
        long_yesterday_qty=1.0,
        short_today_qty=0.0,
        short_yesterday_qty=0.0,
        long_frozen_qty=0.25,
        short_frozen_qty=0.0,
        long_closable_qty=1.75,
        short_closable_qty=0.0,
        margin=6200.0,
        realized_pnl=120.0,
        unrealized_pnl=-40.0,
        asof=datetime(2026, 8, 22, tzinfo=UTC),
    )

    assert snapshot.long_today_qty + snapshot.long_yesterday_qty == snapshot.qty
    assert snapshot.long_closable_qty == 1.75
    assert snapshot.margin == 6200.0
    assert snapshot.realized_pnl + snapshot.unrealized_pnl == 80.0
