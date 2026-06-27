"""订单 / 成交 / 持仓对账模块。"""

from __future__ import annotations

import polars as pl
from sqlalchemy.orm import Session

from northstar_quant.db.repositories import (
    list_latest_positions,
    save_account_snapshot,
    save_account_attribution_for_snapshot,
    save_fill_snapshots,
    save_position_snapshots,
    save_working_order_snapshots,
    update_order_statuses,
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

    reconcile_logger = logger.bind(command="broker.reconcile", broker=broker.get_name())
    reconcile_logger.info("开始同步券商状态")
    if snapshot is None:
        snapshot = broker.sync_state()
    pos_count = save_position_snapshots(session, snapshot.positions)
    fill_count = save_fill_snapshots(session, snapshot.fills)
    updated_orders = update_order_statuses(session, snapshot.open_orders)
    default_account = (
        snapshot.account_values.get("Account")
        if isinstance(snapshot.account_values, dict)
        else None
    ) or next((item.account for item in snapshot.positions if item.account), None)
    working_order_snapshot = save_working_order_snapshots(
        session,
        snapshot.open_orders,
        broker=broker.get_name(),
        run_id=run_id,
        profile_id=profile_id,
        default_account=default_account,
        observed_at=snapshot.asof,
    )
    account_snapshot = save_account_snapshot(
        session,
        broker=broker.get_name(),
        snapshot=snapshot,
        run_id=run_id,
        profile_id=profile_id,
    )
    account_attribution = save_account_attribution_for_snapshot(session, account_snapshot)
    write_sync_log(
        session,
        broker=broker.get_name(),
        sync_type='full_state',
        status='success',
        detail=(
            f'positions={pos_count}, fills={fill_count}, open_orders={len(snapshot.open_orders)}, '
            f'updated_orders={updated_orders}, working_order_snapshots={working_order_snapshot["count"]}, '
            f'account_snapshot_id={account_snapshot.id}, '
            f'account_attribution_id={getattr(account_attribution, "id", None)}'
        ),
    )
    reconcile_logger.info(
        "券商状态同步完成，positions=%s，fills=%s，open_orders=%s，updated_orders=%s，working_order_snapshots=%s，account_snapshot_id=%s，account_attribution_id=%s",
        pos_count,
        fill_count,
        len(snapshot.open_orders),
        updated_orders,
        working_order_snapshot["count"],
        account_snapshot.id,
        getattr(account_attribution, "id", None),
    )
    return {
        'positions_synced': pos_count,
        'fills_synced': fill_count,
        'open_orders_count': len(snapshot.open_orders),
        'updated_order_statuses': updated_orders,
        'working_order_snapshots_synced': int(working_order_snapshot["count"]),
        'working_order_snapshot_batch_id': working_order_snapshot["snapshot_batch_id"],
        'account_snapshots_synced': 1,
        'account_snapshot_id': account_snapshot.id,
        'account_attribution_id': getattr(account_attribution, "id", None),
        'account_values': snapshot.account_values,
    }


def analyze_position_drift(session: Session, targets: pl.DataFrame, latest_prices: dict[str, float]) -> dict:
    """分析真实持仓与目标仓位之间的差异。

    这是“实盘到底有没有跟上策略”的核心检查：
    - 目标权重是多少
    - 当前真实仓位是多少
    - 偏离金额和偏离权重多大
    """

    logger.bind(command="position.drift").info("开始计算持仓偏离")
    latest_positions = list_latest_positions(session)
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

    total_market_value = 0.0
    if not current_df.is_empty():
        total_market_value = float(current_df['current_market_value'].sum())
    if total_market_value <= 0:
        total_market_value = 1.0

    merged = target_df.join(current_df, on='symbol', how='full', coalesce=True).fill_null(0.0)
    merged = merged.with_columns(
        (pl.col('current_market_value') / total_market_value).alias('current_weight'),
        (pl.col('target_weight') - pl.col('current_market_value') / total_market_value).alias('weight_diff'),
    )
    merged = merged.sort('weight_diff', descending=True)

    result = {
        'summary': {
            'position_count': int(merged.height),
            'total_abs_weight_diff': float(merged.select(pl.col('weight_diff').abs().sum()).item()),
            'max_abs_weight_diff': float(merged.select(pl.col('weight_diff').abs().max()).item() or 0.0),
        },
        'rows': merged.to_dicts(),
    }
    logger.bind(command="position.drift").info(
        "持仓偏离计算完成，position_count=%s，total_abs_weight_diff=%.4f",
        result['summary']['position_count'],
        result['summary']['total_abs_weight_diff'],
    )
    return result
