"""订单管理模块。

负责：
- 识别超时未成交订单
- 发起撤单
- 更新本地订单状态
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from northstar_quant.common.time import utc_now
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from northstar_quant.common.order_status import WORKING_ORDER_STATUSES
from northstar_quant.config.settings import get_settings
from northstar_quant.db.models import OrderRecord
from northstar_quant.db.repositories import add_cancel_record
from northstar_quant.execution.broker_base import BrokerAdapter


def cancel_stale_orders(session: Session, broker: BrokerAdapter) -> dict:
    """撤销超时未完成订单。

    判断依据使用本地下单时间 submitted_at。
    这对个人日频系统已经足够实用，也便于审计。
    """

    settings = get_settings()
    cutoff = utc_now() - timedelta(seconds=settings.order_timeout_seconds)
    broker_name = str(broker.get_name()).strip().lower()
    account = str(broker.get_account() or "").strip()
    if not broker_name or not account:
        raise RuntimeError("撤单前无法确认券商与账户范围，已停止操作。")

    rows = list(
        session.scalars(
            select(OrderRecord).where(
                func.lower(OrderRecord.broker) == broker_name,
                OrderRecord.account == account,
                func.lower(OrderRecord.status).in_(WORKING_ORDER_STATUSES),
                OrderRecord.submitted_at <= cutoff,
            )
        )
    )
    canceled_ids: list[str] = []
    cancel_batch_id = f"cancel-batch-{uuid4().hex[:12]}"
    for row in rows:
        if not row.broker_order_id:
            continue
        ok = broker.cancel_order(row.broker_order_id)
        if ok:
            row.status = "PendingCancel"
            add_cancel_record(
                session,
                order=row,
                broker=broker_name,
                cancel_batch_id=cancel_batch_id,
                reason="stale_order_timeout",
                status="PendingCancel",
            )
            canceled_ids.append(row.broker_order_id)
    session.commit()
    return {
        "broker": broker_name,
        "account": account,
        "stale_order_count": len(rows),
        "cancel_requested_order_ids": canceled_ids,
        # 兼容既有调用方；这里只表示撤单请求已发出，不代表券商终态已确认。
        "canceled_order_ids": canceled_ids,
        "cancel_record_count": len(canceled_ids),
        "cancel_batch_id": cancel_batch_id if canceled_ids else None,
    }
