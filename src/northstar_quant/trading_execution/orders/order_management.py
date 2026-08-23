"""订单管理模块。

负责：
- 识别超时未成交订单
- 发起撤单
- 更新本地订单状态
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from northstar_quant.foundation.common.time import utc_now
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from northstar_quant.foundation.common.order_status import WORKING_ORDER_STATUSES
from northstar_quant.foundation.config.settings import get_settings
from northstar_quant.foundation.db.models import OrderRecord
from northstar_quant.foundation.db.repositories import (
    finalize_order_cancel_request,
    prepare_order_cancel,
)
from northstar_quant.trading_execution.broker.broker_base import BrokerAdapter


def cancel_stale_orders(
    session: Session,
    broker: BrokerAdapter,
    *,
    cancel_batch_id: str | None = None,
) -> dict:
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

    order_conditions = [
        func.lower(OrderRecord.broker) == broker_name,
        OrderRecord.account == account,
        func.lower(OrderRecord.status).in_(WORKING_ORDER_STATUSES),
        OrderRecord.submitted_at <= cutoff,
    ]
    client_id_getter = getattr(broker, "get_client_id", None)
    client_id = (
        client_id_getter()
        if callable(client_id_getter)
        else None
    )
    if client_id is not None:
        order_conditions.append(OrderRecord.client_id == int(client_id))
    rows = list(
        session.scalars(select(OrderRecord).where(*order_conditions))
    )
    canceled_ids: list[str] = []
    cancel_batch_id = cancel_batch_id or f"cancel-batch-{uuid4().hex[:12]}"
    for row in rows:
        if not row.broker_order_id:
            continue
        if getattr(broker, "persists_cancel_intents", False):
            ok = broker.cancel_order_for_local_order(
                row.id,
                row.broker_order_id,
            )
        else:
            cancel_row, created = prepare_order_cancel(
                session,
                broker=broker_name,
                account=account,
                broker_order_id=row.broker_order_id,
                reason="stale_order_timeout",
                cancel_batch_id=cancel_batch_id,
                local_order_id=row.id,
            )
            if not created:
                continue
            ok = broker.cancel_order(row.broker_order_id)
            finalize_order_cancel_request(
                session,
                cancel_id=cancel_row.id,
                accepted=ok,
            )
        if ok:
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
