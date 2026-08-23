"""实盘券商状态持久化与持仓偏离分析。

这里同时协调券商端口与数据库事务，因此属于实盘应用编排层，而不是纯执行域。
"""

from __future__ import annotations

from datetime import datetime
from typing import NoReturn

import polars as pl
from sqlalchemy.orm import Session

from northstar_quant.foundation.common.time import ensure_utc, utc_now
from northstar_quant.foundation.db.repositories import (
    acquire_reconciliation_safety_fence,
    assert_broker_fills_explained,
    assert_broker_order_rows_explained,
    latest_reconciliation_safety_state,
    latest_account_snapshot,
    list_latest_positions,
    save_reconciliation_safety_state,
    save_account_snapshot,
    save_account_attribution_for_snapshot,
    save_fill_snapshots,
    save_position_snapshot_batch,
    save_working_order_snapshots,
    update_order_statuses,
    update_pending_cancel_statuses,
    write_sync_log,
)
from northstar_quant.portfolio_risk.risk import RiskState, RiskStateSnapshot
from northstar_quant.trading_execution.broker.broker_base import BrokerAdapter
from northstar_quant.foundation.observability.logging.logger import get_logger

logger = get_logger(__name__)


def _persist_safety_snapshot(
    session: Session,
    *,
    profile_id: str | None,
    broker: str,
    account: str | None,
    snapshot: RiskStateSnapshot,
    evidence: dict[str, object],
    commit: bool = True,
) -> None:
    save_reconciliation_safety_state(
        session,
        profile_id=profile_id,
        broker=broker,
        account=account,
        state=snapshot.state.value,
        reason=snapshot.reason,
        evidence=evidence,
        predecessor_hash=snapshot.predecessor_hash,
        state_hash=snapshot.state_hash,
        recovery_approver_id=snapshot.recovery_approver_id,
        occurred_at=snapshot.occurred_at,
        commit=commit,
    )


def _latest_safety_snapshot(
    session: Session,
    *,
    profile_id: str | None,
    broker: str,
    account: str | None,
) -> RiskStateSnapshot | None:
    row = latest_reconciliation_safety_state(
        session,
        profile_id=profile_id,
        broker=broker,
        account=account,
    )
    if row is None:
        return None
    try:
        snapshot = RiskStateSnapshot(
            state=RiskState(row.state),
            occurred_at=row.occurred_at,
            reason=row.reason,
            predecessor_hash=row.predecessor_hash,
            recovery_approver_id=row.recovery_approver_id,
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "RECONCILIATION_SAFETY_STATE_INVALID: 对账安全状态不可解释。"
        ) from exc
    if snapshot.state_hash != row.state_hash:
        raise RuntimeError(
            "RECONCILIATION_SAFETY_STATE_TAMPERED: 对账安全状态审计链校验失败。"
        )
    return snapshot


def _ensure_initial_normal_safety_snapshot(
    session: Session,
    *,
    profile_id: str | None,
    broker: str,
    account: str | None,
    observed_at: datetime,
) -> None:
    """Append the one initial clean-sync state without ever recovering an existing state."""

    if (
        _latest_safety_snapshot(
            session,
            profile_id=profile_id,
            broker=broker,
            account=account,
        )
        is not None
    ):
        return

    normalized_profile = str(profile_id or "").strip() or "unscoped"
    normalized_account = str(account or "").strip() or "unscoped"
    initial = RiskStateSnapshot(
        state=RiskState.NORMAL,
        occurred_at=observed_at,
        reason=(
            "INITIAL_CLEAN_RECONCILIATION:"
            f"{normalized_profile}:{broker}:{normalized_account}"
        ),
    )
    _persist_safety_snapshot(
        session,
        profile_id=profile_id,
        broker=broker,
        account=account,
        snapshot=initial,
        evidence={
            "action": "initialize_after_clean_reconciliation",
            "snapshot_asof": observed_at.isoformat(),
        },
        commit=False,
    )


def halt_for_reconciliation(
    session: Session,
    *,
    profile_id: str | None,
    broker: str,
    account: str | None,
    reason: str,
    evidence: dict[str, object],
) -> RiskStateSnapshot:
    """Append a fail-closed HALT without ever minting a transient NORMAL state."""

    acquire_reconciliation_safety_fence(
        session,
        profile_id=profile_id,
        broker=broker,
        account=account,
    )
    try:
        now = utc_now()
        current = _latest_safety_snapshot(
            session,
            profile_id=profile_id,
            broker=broker,
            account=account,
        )
        if current is not None and current.state is RiskState.HALT:
            # ``pg_advisory_xact_lock`` is released only at transaction end.
            # This no-op transition owns its fence transaction, so end it
            # explicitly instead of retaining the account fence until a caller
            # happens to close the session.
            session.commit()
            return current
        if current is None:
            halted = RiskStateSnapshot(
                state=RiskState.HALT,
                occurred_at=now,
                reason=reason,
            )
            _persist_safety_snapshot(
                session,
                profile_id=profile_id,
                broker=broker,
                account=account,
                snapshot=halted,
                evidence=evidence,
            )
            return halted
        halted = current.transition(
            target=RiskState.HALT,
            occurred_at=now,
            reason=reason,
        )
        _persist_safety_snapshot(
            session,
            profile_id=profile_id,
            broker=broker,
            account=account,
            snapshot=halted,
            evidence=evidence,
        )
        return halted
    except Exception:
        session.rollback()
        raise


def begin_reconciliation_manual_recovery(
    session: Session,
    *,
    profile_id: str,
    broker: str,
    account: str | None,
    approver_id: str,
    reason: str,
) -> RiskStateSnapshot:
    """由具名负责人把 HALT 进入 MANUAL_RECOVERY；此状态仍禁止提交订单。"""

    acquire_reconciliation_safety_fence(
        session,
        profile_id=profile_id,
        broker=broker,
        account=account,
    )
    try:
        current = _latest_safety_snapshot(
            session,
            profile_id=profile_id,
            broker=broker,
            account=account,
        )
        if current is None or current.state is not RiskState.HALT:
            raise RuntimeError("RECONCILIATION_MANUAL_RECOVERY_REQUIRES_HALT")
        recovery = current.transition(
            target=RiskState.MANUAL_RECOVERY,
            occurred_at=utc_now(),
            reason=reason,
            recovery_approver_id=approver_id,
        )
        _persist_safety_snapshot(
            session,
            profile_id=profile_id,
            broker=broker,
            account=account,
            snapshot=recovery,
            evidence={"action": "begin_manual_recovery", "approver_id": approver_id},
        )
        return recovery
    except Exception:
        session.rollback()
        raise


def complete_reconciliation_manual_recovery(
    session: Session,
    *,
    profile_id: str,
    broker: str,
    account: str | None,
    approver_id: str,
    reason: str,
) -> RiskStateSnapshot:
    """仅原具名负责人可完成复核并重新开放对账安全门禁。"""

    acquire_reconciliation_safety_fence(
        session,
        profile_id=profile_id,
        broker=broker,
        account=account,
    )
    try:
        current = _latest_safety_snapshot(
            session,
            profile_id=profile_id,
            broker=broker,
            account=account,
        )
        if current is None or current.state is not RiskState.MANUAL_RECOVERY:
            raise RuntimeError("RECONCILIATION_MANUAL_RECOVERY_REQUIRED")
        if current.recovery_approver_id != approver_id:
            raise PermissionError("RECONCILIATION_RECOVERY_APPROVER_MISMATCH")
        normal = current.transition(
            target=RiskState.NORMAL,
            occurred_at=utc_now(),
            reason=reason,
        )
        _persist_safety_snapshot(
            session,
            profile_id=profile_id,
            broker=broker,
            account=account,
            snapshot=normal,
            evidence={"action": "complete_manual_recovery", "approver_id": approver_id},
        )
        return normal
    except Exception:
        session.rollback()
        raise


def _record_reconciliation_failure(
    session: Session,
    *,
    broker: str,
    account: str | None,
    profile_id: str | None,
    detail: str,
) -> None:
    """尽力留下失败同步与粘性 HALT；持久化故障不能掩盖原始差异。"""

    try:
        write_sync_log(
            session,
            broker=broker,
            sync_type="full_state",
            status="failed",
            detail=detail,
            commit=False,
        )
        halt_for_reconciliation(
            session,
            profile_id=profile_id,
            broker=broker,
            account=account,
            reason="RECONCILIATION_UNEXPLAINED_DIFFERENCE",
            evidence={"failure": detail},
        )
    except Exception:
        session.rollback()
        logger.exception("对账失败后的安全审计记录写入失败")


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

    # A reconciliation owns its entire database transaction: it acquires an
    # account-scoped advisory fence and may commit the clean NORMAL fact or a
    # fail-closed HALT.  Entering through a caller's already-open transaction
    # could commit unrelated work and would make the fence's lifetime
    # ambiguous.  Refuse rather than silently committing or rolling back
    # caller-owned state.
    if session.in_transaction() or session.new or session.dirty or session.deleted:
        raise RuntimeError("RECONCILIATION_SESSION_MUST_BE_CLEAN")
    broker_name = str(broker.get_name()).strip().lower()
    expected_account = str(broker.get_account() or "").strip() or None
    reconcile_logger = logger.bind(command="broker.reconcile", broker=broker_name)
    reconcile_logger.info("开始同步券商状态")
    if snapshot is None:
        try:
            snapshot = broker.sync_state()
        except Exception as exc:
            detail = f"BROKER_STATE_UNAVAILABLE: {type(exc).__name__}: {exc}"
            _record_reconciliation_failure(
                session,
                broker=broker_name,
                account=expected_account,
                profile_id=profile_id,
                detail=detail,
            )
            raise RuntimeError(detail) from exc
    snapshot_account = str(snapshot.account or "").strip() or None
    acquire_reconciliation_safety_fence(
        session,
        profile_id=profile_id,
        broker=broker_name,
        account=snapshot_account or expected_account,
    )
    try:
        nested_transaction = session.begin_nested()
    except Exception as exc:
        # A savepoint cannot be trusted to protect the outer safety fence if it
        # was never created.  End that transaction explicitly, then use a new
        # fenced transaction for the best-effort failed-sync/HALT fact.
        session.rollback()
        _record_reconciliation_failure(
            session,
            broker=broker_name,
            account=snapshot_account or expected_account,
            profile_id=profile_id,
            detail=f"{type(exc).__name__}: {exc}",
        )
        raise

    def reject(detail: str) -> NoReturn:
        nested_transaction.rollback()
        _record_reconciliation_failure(
            session,
            broker=broker_name,
            account=snapshot_account or expected_account,
            profile_id=profile_id,
            detail=detail,
        )
        raise RuntimeError(detail)

    if not snapshot.state_complete or snapshot.state_errors:
        error_detail = "；".join(str(item) for item in snapshot.state_errors)
        suffix = f" 错误：{error_detail}" if error_detail else ""
        reject(f"券商状态快照不完整，已停止对账写入。{suffix}")
    if snapshot.asof is None:
        reject("券商状态快照缺少 asof，已停止对账写入。")
    try:
        snapshot_asof = ensure_utc(snapshot.asof)
    except (TypeError, ValueError):
        reject("BROKER_STATE_SNAPSHOT_TIME_INVALID")
    if snapshot_asof > utc_now():
        reject("BROKER_STATE_SNAPSHOT_IN_FUTURE")
    missing_fill_timestamps = [
        item.exec_id or item.broker_order_id
        for item in snapshot.fills
        if item.filled_at is None
    ]
    if missing_fill_timestamps:
        reject(
            "券商成交缺少 filled_at，已停止对账写入："
            + ", ".join(str(item) for item in missing_fill_timestamps)
        )
    if expected_account and snapshot_account != expected_account:
        reject(
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
        assert_broker_order_rows_explained(
            session,
            broker_order_rows,
            broker=broker_name,
            account=snapshot_account,
        )
        updated_orders = update_order_statuses(
            session,
            broker_order_rows,
            broker=broker_name,
            account=snapshot_account,
            commit=False,
        )
        # 先用 open/completed orders 恢复 orderRef/permId 等订单强身份，再落成交。
        # 否则崩溃窗口中的 execution 会先以 order_id=NULL 去重，永久丢失归属。
        assert_broker_fills_explained(
            session,
            snapshot.fills,
            broker=broker_name,
            account=snapshot_account,
        )
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
        _ensure_initial_normal_safety_snapshot(
            session,
            profile_id=profile_id,
            broker=broker_name,
            account=snapshot_account or expected_account,
            observed_at=snapshot.asof,
        )
        # SQLAlchemy expires ORM instances at commit.  Capture their scalar
        # identities before ending the fenced transaction so return/logging
        # code cannot silently open a new caller-visible read transaction.
        account_snapshot_id = account_snapshot.id
        account_attribution_id = getattr(account_attribution, "id", None)
        nested_transaction.commit()
        session.commit()
    except Exception as exc:
        if nested_transaction.is_active:
            nested_transaction.rollback()
        else:
            # ``session.commit()`` happens only after the savepoint succeeds.
            # A commit failure leaves the outer transaction unusable and has
            # released (or made indeterminate) its transaction fence.  Start a
            # new fenced failure transaction rather than attempting HALT on an
            # aborted session.
            session.rollback()
        error_detail = f"{type(exc).__name__}: {exc}"
        _record_reconciliation_failure(
            session,
            broker=broker_name,
            account=snapshot_account or expected_account,
            profile_id=profile_id,
            detail=error_detail,
        )
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
        account_snapshot_id,
        account_attribution_id,
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
        'account_snapshot_id': account_snapshot_id,
        'account_attribution_id': account_attribution_id,
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
