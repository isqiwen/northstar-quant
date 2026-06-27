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
from sqlalchemy import select
from sqlalchemy.orm import Session

from northstar_quant.config.settings import get_settings
from northstar_quant.db.models import OrderRecord
from northstar_quant.db.repositories import add_cancel_record
from northstar_quant.execution.broker_base import BrokerAdapter

_OPEN_STATUSES = {
    'submitted', 'presubmitted', 'pending', 'open', 'Submitted', 'PreSubmitted',
    'PendingSubmit', 'PartiallyFilled', 'partiallyfilled'
}


def cancel_stale_orders(session: Session, broker: BrokerAdapter) -> dict:
    """撤销超时未完成订单。

    判断依据使用本地下单时间 submitted_at。
    这对个人日频系统已经足够实用，也便于审计。
    """

    settings = get_settings()
    cutoff = utc_now() - timedelta(seconds=settings.order_timeout_seconds)

    rows = list(session.scalars(select(OrderRecord).where(OrderRecord.status.in_(_OPEN_STATUSES), OrderRecord.submitted_at <= cutoff)))
    canceled_ids: list[str] = []
    cancel_batch_id = f"cancel-batch-{uuid4().hex[:12]}"
    for row in rows:
        if not row.broker_order_id:
            continue
        ok = broker.cancel_order(row.broker_order_id)
        if ok:
            row.status = 'Canceled'
            add_cancel_record(
                session,
                order=row,
                broker=broker.get_name(),
                cancel_batch_id=cancel_batch_id,
                reason="stale_order_timeout",
            )
            canceled_ids.append(row.broker_order_id)
    session.commit()
    return {
        'stale_order_count': len(rows),
        'canceled_order_ids': canceled_ids,
        'cancel_record_count': len(canceled_ids),
        'cancel_batch_id': cancel_batch_id if canceled_ids else None,
    }
