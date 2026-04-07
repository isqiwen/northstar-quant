"""数据库写入辅助函数。"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from northstar_quant.db.models import BrokerSyncLog, FillRecord, OrderRecord, PositionSnapshotRecord
from northstar_quant.execution.models import FillSnapshot, PositionSnapshot


def save_position_snapshots(session: Session, snapshots: list[PositionSnapshot]) -> int:
    """批量保存真实持仓快照。"""

    count = 0
    for item in snapshots:
        session.add(
            PositionSnapshotRecord(
                account=item.account,
                symbol=item.symbol,
                qty=item.qty,
                avg_cost=item.avg_cost,
                market_price=item.market_price,
                market_value=item.market_value,
                asof=item.asof,
            )
        )
        count += 1
    session.commit()
    return count


def save_fill_snapshots(session: Session, fills: list[FillSnapshot]) -> int:
    """批量保存成交快照。

    这里按 broker_order_id + symbol + qty + price + filled_at 做最基础的去重，
    避免轮询同步时把同一笔成交重复写入。
    """

    count = 0
    for item in fills:
        exists = session.scalar(
            select(FillRecord.id).where(
                FillRecord.broker_order_id == item.broker_order_id,
                FillRecord.symbol == item.symbol,
                FillRecord.qty == item.qty,
                FillRecord.price == item.price,
                FillRecord.filled_at == item.filled_at,
            )
        )
        if exists:
            continue

        session.add(
            FillRecord(
                broker_order_id=item.broker_order_id,
                symbol=item.symbol,
                side=item.side,
                qty=item.qty,
                price=item.price,
                filled_at=item.filled_at,
            )
        )
        count += 1
    session.commit()
    return count


def save_order_result(
    session: Session,
    strategy_id: str,
    symbol: str,
    side: str,
    qty: float,
    target_weight: float | None,
    order_semantic: str | None,
    broker_order_id: str | None,
    status: str,
) -> OrderRecord:
    """保存订单记录。"""

    row = OrderRecord(
        strategy_id=strategy_id,
        symbol=symbol,
        side=side,
        qty=qty,
        target_weight=target_weight,
        order_semantic=order_semantic,
        broker_order_id=broker_order_id,
        status=status,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_order_statuses(session: Session, broker_rows: Sequence[dict]) -> int:
    """按券商返回的未完成订单 / 状态信息更新本地订单状态。"""

    updated = 0
    for row in broker_rows:
        broker_order_id = str(row.get('broker_order_id') or '')
        if not broker_order_id:
            continue
        order = session.scalar(select(OrderRecord).where(OrderRecord.broker_order_id == broker_order_id))
        if order is None:
            continue
        new_status = str(row.get('status') or order.status)
        if order.status != new_status:
            order.status = new_status
            updated += 1
    session.commit()
    return updated


def list_latest_positions(session: Session) -> list[PositionSnapshotRecord]:
    """读取最近一次持仓快照。

    这里为了保持实现简单，按 asof 最大值取一批快照。
    后续如需更强审计能力，可以引入 batch_id 概念。
    """

    latest_asof = session.scalar(select(PositionSnapshotRecord.asof).order_by(PositionSnapshotRecord.asof.desc()).limit(1))
    if latest_asof is None:
        return []
    return list(session.scalars(select(PositionSnapshotRecord).where(PositionSnapshotRecord.asof == latest_asof)))


def write_sync_log(session: Session, broker: str, sync_type: str, status: str, detail: str | None = None) -> None:
    """写入券商同步日志。"""

    session.add(BrokerSyncLog(broker=broker, sync_type=sync_type, status=status, detail=detail))
    session.commit()


def list_recent_orders(session: Session, limit: int = 50) -> list[OrderRecord]:
    """读取最近订单记录。"""

    return list(session.scalars(select(OrderRecord).order_by(OrderRecord.submitted_at.desc()).limit(limit)))


def list_recent_fills(session: Session, limit: int = 50) -> list[FillRecord]:
    """读取最近成交记录。"""

    return list(session.scalars(select(FillRecord).order_by(FillRecord.filled_at.desc()).limit(limit)))


def aggregate_position_market_value(session: Session) -> float:
    """估算最近一次真实持仓的总市值。"""

    rows = list_latest_positions(session)
    total = 0.0
    for row in rows:
        total += float(row.market_value or 0.0)
    return total
