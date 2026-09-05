from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from northstar_quant.data.research import Market, ResearchBar
from northstar_quant.execution import Account, PendingOrder
from northstar_quant.risk import Side


def test_fill_enforces_actual_slipped_price_and_fifo_cost_conservation() -> None:
    at = datetime(2026, 1, 5, 1, tzinfo=UTC)
    market = Market(
        UUID(int=1), "RB2605", "Asia/Shanghai", "CNY", "TON", Decimal(1), Decimal(10), 60
    )
    account = Account(Decimal(1000), market)
    order = PendingOrder(
        "open",
        UUID(int=10),
        at,
        at + timedelta(minutes=10),
        Side.BUY,
        2,
        Decimal(100),
        Decimal(102),
    )
    bar = ResearchBar(
        UUID(int=11),
        at,
        at + timedelta(minutes=1),
        at + timedelta(minutes=1, seconds=1),
        date(2026, 1, 5),
        Decimal(102),
        Decimal(100),
    )
    assert account.execute(order, bar, fee_per_lot=Decimal(2), slippage_ticks=1) is None
    fill = account.execute(order, bar, fee_per_lot=Decimal(2), slippage_ticks=0)
    assert fill is not None and fill.price == Decimal(102)
    closing = PendingOrder(
        "close",
        bar.observation_id,
        bar.available_at,
        at + timedelta(minutes=10),
        Side.SELL,
        2,
        Decimal(100),
        Decimal(110),
    )
    later = ResearchBar(
        UUID(int=12),
        at + timedelta(minutes=1),
        at + timedelta(minutes=2),
        at + timedelta(minutes=2, seconds=1),
        date(2026, 1, 5),
        Decimal(105),
        Decimal(100),
    )
    assert account.execute(closing, later, fee_per_lot=Decimal(2), slippage_ticks=1) is not None
    assert account.realized_pnl == Decimal(40)
    assert account.total_fees == Decimal(8)
    assert account.cash == account.equity(later.close) == Decimal(1032)
    assert account.position_lots == 0
