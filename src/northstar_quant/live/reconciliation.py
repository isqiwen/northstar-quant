"""实盘券商状态持久化与持仓偏离分析。

这里同时协调券商端口与数据库事务，因此属于实盘应用编排层，而不是纯执行域。
"""

from __future__ import annotations

import polars as pl
from sqlalchemy.orm import Session

from northstar_quant.db.repositories import (
    latest_account_snapshot,
    list_latest_positions,
    save_account_snapshot,
    save_account_attribution_for_snapshot,
    save_fill_snapshots,
    save_position_snapshot_batch,
    save_working_order_snapshots,
    update_order_statuses,
    update_pending_cancel_statuses,
    write_sync_log,
)
from northstar_quant.execution.broker_base import BrokerAdapter
from northstar_quant.logging_.logger import get_logger

logger = get_logger(__name__)


def reconcile_broker_state(
    session: Session,
    broker: BrokerAdapter,
    *,
    snapshot=None,
    run_id: str | None = None,
    profile_id: str | None = None,
) -> dict:
    """同步券商状态并写入数据库。

    这是个人量化里非常关键的一层：
    - 研究系统认为自己该持有什么
    - 券商账户实际上持有什么
    - 两者是否一致

    只有把真实持仓和真实成交持续落库，你后续才能做可靠的审计与复盘。
    """

    broker_name = str(broker.get_name()).strip().lower()
    expected_account = str(broker.get_account() or "").strip() or None
    reconcile_logger = logger.bind(command="broker.reconcile", broker=broker_name)
    reconcile_logger.info("开始同步券商状态")
    if snapshot is None:
        snapshot = broker.sync_state()
    snapshot_account = str(snapshot.account or "").strip() or None
    if not snapshot.state_complete or snapshot.state_errors:
        error_detail = "；".join(str(item) for item in snapshot.state_errors)
        suffix = f" 错误：{error_detail}" if error_detail else ""
        raise RuntimeError(f"券商状态快照不完整，已停止对账写入。{suffix}")
    if snapshot.asof is None:
        raise RuntimeError("券商状态快照缺少 asof，已停止对账写入。")
    missing_fill_timestamps = [
        item.exec_id or item.broker_order_id
        for item in snapshot.fills
        if item.filled_at is None
    ]
    if missing_fill_timestamps:
        raise RuntimeError(
            "券商成交缺少 filled_at，已停止对账写入："
            + ", ".join(str(item) for item in missing_fill_timestamps)
        )
    if expected_account and snapshot_account != expected_account:
        raise RuntimeError(
            "券商状态账户与适配器目标账户不一致，已停止对账写入。"
        )
    try:
        position_batch = save_position_snapshot_batch(
            session,
            snapshot.positions,
            broker=broker_name,
            account=snapshot_account or expected_account,
            profile_id=profile_id,
            run_id=run_id,
            asof=snapshot.asof,
            commit=False,
        )
        pos_count = int(position_batch.position_count)
        broker_order_rows = [
            *snapshot.open_orders,
            *snapshot.completed_orders,
        ]
        updated_orders = update_order_statuses(
            session,
            broker_order_rows,
            broker=broker_name,
            account=snapshot_account,
            commit=False,
        )
        # 先用 open/completed orders 恢复 orderRef/permId 等订单强身份，再落成交。
        # 否则崩溃窗口中的 execution 会先以 order_id=NULL 去重，永久丢失归属。
        fill_count = save_fill_snapshots(
            session,
            snapshot.fills,
            broker=broker_name,
            commit=False,
        )
        updated_cancels = update_pending_cancel_statuses(
            session,
            broker_order_rows,
            broker=broker_name,
            account=snapshot_account,
            commit=False,
        )
        default_account = (
            snapshot_account
            or snapshot.account_values.get("Account")
            if isinstance(snapshot.account_values, dict)
            else None
        ) or next((item.account for item in snapshot.positions if item.account), None)
        working_order_snapshot = save_working_order_snapshots(
            session,
            snapshot.open_orders,
            broker=broker_name,
            run_id=run_id,
            profile_id=profile_id,
            default_account=default_account,
            observed_at=snapshot.asof,
            commit=False,
        )
        working_order_count = int(str(working_order_snapshot["count"]))
        account_snapshot = save_account_snapshot(
            session,
            broker=broker_name,
            snapshot=snapshot,
            run_id=run_id,
            profile_id=profile_id,
            position_snapshot_batch_id=position_batch.snapshot_batch_id,
            commit=False,
        )
        account_attribution = save_account_attribution_for_snapshot(
            session,
            account_snapshot,
            commit=False,
        )
        write_sync_log(
            session,
            broker=broker_name,
            sync_type="full_state",
            status="success",
            detail=(
                f"positions={pos_count}, fills={fill_count}, "
                f"open_orders={len(snapshot.open_orders)}, "
                f"completed_orders={len(snapshot.completed_orders)}, "
                f"updated_orders={updated_orders}, updated_cancels={updated_cancels}, "
                f"working_order_snapshots={working_order_count}, "
                f"account_snapshot_id={account_snapshot.id}, "
                f"account_attribution_id={getattr(account_attribution, 'id', None)}"
            ),
            commit=False,
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        error_detail = f"{type(exc).__name__}: {exc}"
        try:
            write_sync_log(
                session,
                broker=broker_name,
                sync_type="full_state",
                status="failed",
                detail=error_detail,
            )
        except Exception:
            session.rollback()
            reconcile_logger.exception("券商状态同步失败，且失败日志写入失败")
        reconcile_logger.exception("券商状态同步失败，事务已回滚")
        raise
    reconcile_logger.info(
        "券商状态同步完成，positions=%s，fills=%s，open_orders=%s，completed_orders=%s，updated_orders=%s，updated_cancels=%s，working_order_snapshots=%s，account_snapshot_id=%s，account_attribution_id=%s",
        pos_count,
        fill_count,
        len(snapshot.open_orders),
        len(snapshot.completed_orders),
        updated_orders,
        updated_cancels,
        working_order_count,
        account_snapshot.id,
        getattr(account_attribution, "id", None),
    )
    return {
        'positions_synced': pos_count,
        'fills_synced': fill_count,
        'open_orders_count': len(snapshot.open_orders),
        'completed_orders_count': len(snapshot.completed_orders),
        'updated_order_statuses': updated_orders,
        'updated_cancel_statuses': updated_cancels,
        "working_order_snapshots_synced": working_order_count,
        'working_order_snapshot_batch_id': working_order_snapshot["snapshot_batch_id"],
        'account_snapshots_synced': 1,
        'account_snapshot_id': account_snapshot.id,
        'account_attribution_id': getattr(account_attribution, "id", None),
        'account_values': snapshot.account_values,
    }


def analyze_position_drift(
    session: Session,
    targets: pl.DataFrame,
    latest_prices: dict[str, float],
    *,
    broker: str | None = None,
    account: str | None = None,
    profile_id: str | None = None,
    equity: float | None = None,
) -> dict:
    """分析真实持仓与目标仓位之间的差异。

    这是“实盘到底有没有跟上策略”的核心检查：
    - 目标权重是多少
    - 当前真实仓位是多少
    - 偏离金额和偏离权重多大
    """

    logger.bind(command="position.drift").info("开始计算持仓偏离")
    latest_positions = list_latest_positions(
        session,
        broker=broker,
        account=account,
        profile_id=profile_id,
    )
    current_rows = []
    for pos in latest_positions:
        price = float(latest_prices.get(pos.symbol, pos.market_price or 0.0) or 0.0)
        current_rows.append(
            {
                'symbol': pos.symbol,
                'current_qty': float(pos.qty),
                'current_market_value': float(pos.qty) * price,
            }
        )

    current_df = pl.DataFrame(current_rows) if current_rows else pl.DataFrame({'symbol': [], 'current_qty': [], 'current_market_value': []})
    target_df = targets.select(['symbol', 'target_weight'])

    resolved_equity = float(equity) if equity is not None else None
    if resolved_equity is None:
        account_row = latest_account_snapshot(
            session,
            broker=broker,
            account=account,
            profile_id=profile_id,
        )
        if account_row is not None and account_row.net_liquidation is not None:
            resolved_equity = float(account_row.net_liquidation)
    if resolved_equity is None or resolved_equity <= 0:
        raise ValueError("持仓偏离计算需要作用域内大于 0 的账户权益")

    merged = target_df.join(current_df, on='symbol', how='full', coalesce=True).fill_null(0.0)
    merged = merged.with_columns(
        (pl.col('current_market_value') / resolved_equity).alias('current_weight'),
        (pl.col('target_weight') - pl.col('current_market_value') / resolved_equity).alias('weight_diff'),
    )
    merged = merged.sort('weight_diff', descending=True)

    position_count = int(merged.height)
    total_abs_weight_diff = float(
        merged.select(pl.col("weight_diff").abs().sum()).item()
    )
    max_abs_weight_diff = float(
        merged.select(pl.col("weight_diff").abs().max()).item() or 0.0
    )
    result = {
        'summary': {
            "position_count": position_count,
            "equity": resolved_equity,
            "total_abs_weight_diff": total_abs_weight_diff,
            "max_abs_weight_diff": max_abs_weight_diff,
        },
        'rows': merged.to_dicts(),
    }
    logger.bind(command="position.drift").info(
        "持仓偏离计算完成，position_count=%s，total_abs_weight_diff=%.4f",
        position_count,
        total_abs_weight_diff,
    )
    return result
