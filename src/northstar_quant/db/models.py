"""数据库表模型定义。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from northstar_quant.common.time import utc_now
from northstar_quant.db.base import Base
from northstar_quant.db.types import UTCDateTime


class RunLog(Base):
    """任务运行记录表。"""

    __tablename__ = "run_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_name: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    detail: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class SignalRecord(Base):
    """策略信号记录表。"""

    __tablename__ = "signal_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    signal_value: Mapped[float] = mapped_column(Float)
    target_weight: Mapped[float] = mapped_column(Float)
    asof: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, default=utc_now)


class StrategyRunRecord(Base):
    """策略账本中的运行级快照。"""

    __tablename__ = "strategy_run_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    profile_id: Mapped[str] = mapped_column(String(64), index=True)
    pipeline_strategy_id: Mapped[str] = mapped_column(String(128), index=True)
    output_type: Mapped[str] = mapped_column(String(32), index=True)
    selected_strategy_ids_json: Mapped[str | None] = mapped_column(Text, default=None)
    strategy_params_json: Mapped[str | None] = mapped_column(Text, default=None)
    risk_limits_json: Mapped[str | None] = mapped_column(Text, default=None)
    market_data_asof: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    signal_data_asof: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    output_asof: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True, default=None)
    snapshot_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class StrategySnapshotRecord(Base):
    """策略账本中的逐标的输出快照。"""

    __tablename__ = "strategy_snapshot_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    profile_id: Mapped[str] = mapped_column(String(64), index=True)
    pipeline_strategy_id: Mapped[str] = mapped_column(String(128), index=True)
    source_strategy_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    output_type: Mapped[str] = mapped_column(String(32), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    signal_value: Mapped[float | None] = mapped_column(Float, default=None)
    target_weight: Mapped[float | None] = mapped_column(Float, default=None)
    side: Mapped[str | None] = mapped_column(String(8), default=None)
    size_fraction: Mapped[float | None] = mapped_column(Float, default=None)
    order_semantic: Mapped[str | None] = mapped_column(String(16), default=None)
    order_type: Mapped[str | None] = mapped_column(String(16), default=None)
    limit_price: Mapped[float | None] = mapped_column(Float, default=None)
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    asof: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, default=utc_now)


class ExecutionPlanRecord(Base):
    """执行账本中的计划级快照。"""

    __tablename__ = "execution_plan_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    batch_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    plan_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    execution_planner_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    strategy_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    target_weight: Mapped[float | None] = mapped_column(Float, default=None)
    current_qty: Mapped[float | None] = mapped_column(Float, default=None)
    target_qty: Mapped[float | None] = mapped_column(Float, default=None)
    latest_price: Mapped[float | None] = mapped_column(Float, default=None)
    execution_reference_price: Mapped[float | None] = mapped_column(Float, default=None)
    estimated_trade_value: Mapped[float | None] = mapped_column(Float, default=None)
    order_semantic: Mapped[str | None] = mapped_column(String(16), default=None)
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    order_type: Mapped[str | None] = mapped_column(String(16), default=None)
    limit_price: Mapped[float | None] = mapped_column(Float, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class OrderRecord(Base):
    """订单记录表。"""

    __tablename__ = "order_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    strategy_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    target_weight: Mapped[float | None] = mapped_column(Float, default=None)
    order_type: Mapped[str | None] = mapped_column(String(16), default=None)
    limit_price: Mapped[float | None] = mapped_column(Float, default=None)
    order_semantic: Mapped[str | None] = mapped_column(String(16), default=None)
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    account: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    reference_price: Mapped[float | None] = mapped_column(Float, default=None)
    reference_price_source: Mapped[str | None] = mapped_column(String(32), default=None)
    planned_trade_value: Mapped[float | None] = mapped_column(Float, default=None)
    execution_planner_id: Mapped[str | None] = mapped_column(String(64), default=None)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    batch_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    plan_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    broker_order_id: Mapped[str | None] = mapped_column(String(128), default=None)
    status: Mapped[str] = mapped_column(String(32), index=True)
    submitted_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class FillRecord(Base):
    """成交记录表。"""

    __tablename__ = "fill_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    broker_order_id: Mapped[str | None] = mapped_column(String(128), index=True, default=None)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str | None] = mapped_column(String(8), default=None)
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    filled_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class TradeAttributionRecord(Base):
    """成交后归因记录。"""

    __tablename__ = "trade_attribution_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fill_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    order_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    broker_order_id: Mapped[str | None] = mapped_column(String(128), index=True, default=None)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    batch_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    plan_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    account: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    strategy_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    execution_planner_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str | None] = mapped_column(String(8), default=None)
    qty: Mapped[float] = mapped_column(Float)
    fill_price: Mapped[float] = mapped_column(Float)
    reference_price: Mapped[float] = mapped_column(Float)
    reference_price_source: Mapped[str | None] = mapped_column(String(32), default=None)
    actual_notional: Mapped[float] = mapped_column(Float)
    reference_notional: Mapped[float] = mapped_column(Float)
    implementation_shortfall: Mapped[float] = mapped_column(Float)
    implementation_shortfall_bps: Mapped[float | None] = mapped_column(Float, default=None)
    order_semantic: Mapped[str | None] = mapped_column(String(16), default=None)
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    attributed_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, default=utc_now)


class WorkingOrderSnapshotRecord(Base):
    """执行账本中的挂单快照。"""

    __tablename__ = "working_order_snapshot_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    broker: Mapped[str] = mapped_column(String(32), index=True)
    account: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    open_order_snapshot_batch_id: Mapped[str | None] = mapped_column(
        String(64),
        index=True,
        default=None,
    )
    broker_order_id: Mapped[str | None] = mapped_column(String(128), index=True, default=None)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str | None] = mapped_column(String(8), default=None)
    qty: Mapped[float] = mapped_column(Float)
    filled_qty: Mapped[float | None] = mapped_column(Float, default=None)
    remaining_qty: Mapped[float | None] = mapped_column(Float, default=None)
    avg_fill_price: Mapped[float | None] = mapped_column(Float, default=None)
    status: Mapped[str] = mapped_column(String(32), index=True)
    order_type: Mapped[str | None] = mapped_column(String(16), default=None)
    limit_price: Mapped[float | None] = mapped_column(Float, default=None)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, default=utc_now)


class CancelRecord(Base):
    """执行账本中的撤单记录。"""

    __tablename__ = "cancel_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cancel_batch_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    order_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    broker: Mapped[str] = mapped_column(String(32), index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(128), index=True, default=None)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    account: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(32), index=True)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, default=utc_now)


class PositionSnapshotRecord(Base):
    """真实持仓快照表。

    该表是“券商持仓的时间序列快照”，主要用于：
    - 真实持仓同步
    - 再平衡前后的审计
    - 回头排查为什么系统发出了某笔订单
    """

    __tablename__ = "position_snapshot_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    qty: Mapped[float] = mapped_column(Float)
    avg_cost: Mapped[float | None] = mapped_column(Float, default=None)
    market_price: Mapped[float | None] = mapped_column(Float, default=None)
    market_value: Mapped[float | None] = mapped_column(Float, default=None)
    asof: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, default=utc_now)
    snapshot_batch_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)


class AccountSnapshotRecord(Base):
    """账户账本中的账户状态快照。"""

    __tablename__ = "account_snapshot_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    broker: Mapped[str] = mapped_column(String(32), index=True)
    account: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    position_snapshot_batch_id: Mapped[str | None] = mapped_column(
        String(64),
        index=True,
        default=None,
    )
    position_count: Mapped[int] = mapped_column(Integer, default=0)
    cash_balance: Mapped[float | None] = mapped_column(Float, default=None)
    net_liquidation: Mapped[float | None] = mapped_column(Float, default=None)
    gross_position_value: Mapped[float | None] = mapped_column(Float, default=None)
    net_position_value: Mapped[float | None] = mapped_column(Float, default=None)
    available_funds: Mapped[float | None] = mapped_column(Float, default=None)
    gross_exposure: Mapped[float | None] = mapped_column(Float, default=None)
    net_exposure: Mapped[float | None] = mapped_column(Float, default=None)
    realized_pnl: Mapped[float | None] = mapped_column(Float, default=None)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float, default=None)
    account_values_json: Mapped[str | None] = mapped_column(Text, default=None)
    asof: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, default=utc_now)


class AccountAttributionRecord(Base):
    """账户区间收益归因记录。"""

    __tablename__ = "account_attribution_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    start_account_snapshot_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    end_account_snapshot_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    broker: Mapped[str] = mapped_column(String(32), index=True)
    account: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    start_position_snapshot_batch_id: Mapped[str | None] = mapped_column(
        String(64),
        index=True,
        default=None,
    )
    end_position_snapshot_batch_id: Mapped[str | None] = mapped_column(
        String(64),
        index=True,
        default=None,
    )
    start_asof: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, default=utc_now)
    end_asof: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, default=utc_now)
    starting_equity: Mapped[float | None] = mapped_column(Float, default=None)
    ending_equity: Mapped[float | None] = mapped_column(Float, default=None)
    equity_change: Mapped[float | None] = mapped_column(Float, default=None)
    starting_cash: Mapped[float | None] = mapped_column(Float, default=None)
    ending_cash: Mapped[float | None] = mapped_column(Float, default=None)
    cash_change: Mapped[float | None] = mapped_column(Float, default=None)
    price_pnl: Mapped[float | None] = mapped_column(Float, default=None)
    rebalance_pnl: Mapped[float | None] = mapped_column(Float, default=None)
    execution_shortfall: Mapped[float | None] = mapped_column(Float, default=None)
    dividend_cash_flow: Mapped[float | None] = mapped_column(Float, default=None)
    interest_cash_flow: Mapped[float | None] = mapped_column(Float, default=None)
    fee_cash_flow: Mapped[float | None] = mapped_column(Float, default=None)
    tax_cash_flow: Mapped[float | None] = mapped_column(Float, default=None)
    funding_cash_flow: Mapped[float | None] = mapped_column(Float, default=None)
    corporate_action_cash_flow: Mapped[float | None] = mapped_column(Float, default=None)
    other_non_trade_cash_flow: Mapped[float | None] = mapped_column(Float, default=None)
    total_non_trade_cash_flow: Mapped[float | None] = mapped_column(Float, default=None)
    traded_notional: Mapped[float | None] = mapped_column(Float, default=None)
    fill_count: Mapped[int] = mapped_column(Integer, default=0)
    residual_pnl: Mapped[float | None] = mapped_column(Float, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class AnomalyEventRecord(Base):
    """日报/归因链路产出的异常事件表。"""

    __tablename__ = "anomaly_event_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_attribution_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    account: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    report_type: Mapped[str] = mapped_column(String(16), index=True)
    alert_code: Mapped[str] = mapped_column(String(64), index=True)
    alert_tag: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True, default="warning")
    summary: Mapped[str] = mapped_column(Text)
    details_json: Mapped[str | None] = mapped_column(Text, default=None)
    report_path: Mapped[str | None] = mapped_column(Text, default=None)
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, default=utc_now)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class RunHealthRecord(Base):
    """paper soak / shadow run 的运行健康快照。"""

    __tablename__ = "run_health_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    mode: Mapped[str] = mapped_column(String(32), index=True)
    broker: Mapped[str] = mapped_column(String(32), index=True)
    account: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    preflight_can_trade: Mapped[bool] = mapped_column(Boolean, default=False)
    blocking_failure_count: Mapped[int] = mapped_column(Integer, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    target_symbol_count: Mapped[int] = mapped_column(Integer, default=0)
    target_weight_sum: Mapped[float | None] = mapped_column(Float, default=None)
    execution_plan_count: Mapped[int] = mapped_column(Integer, default=0)
    planned_trade_value: Mapped[float | None] = mapped_column(Float, default=None)
    plan_consistency_issue_count: Mapped[int] = mapped_column(Integer, default=0)
    open_order_count: Mapped[int] = mapped_column(Integer, default=0)
    partial_fill_count: Mapped[int] = mapped_column(Integer, default=0)
    fills_seen_count: Mapped[int] = mapped_column(Integer, default=0)
    execution_shortfall: Mapped[float | None] = mapped_column(Float, default=None)
    execution_shortfall_bps: Mapped[float | None] = mapped_column(Float, default=None)
    residual_pnl: Mapped[float | None] = mapped_column(Float, default=None)
    anomaly_count_trailing_7d: Mapped[int] = mapped_column(Integer, default=0)
    anomaly_count_prev_7d: Mapped[int] = mapped_column(Integer, default=0)
    anomaly_trend: Mapped[str | None] = mapped_column(String(16), index=True, default=None)
    details_json: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, default=utc_now)


class BrokerSyncLog(Base):
    """券商同步日志表。"""

    __tablename__ = "broker_sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    broker: Mapped[str] = mapped_column(String(32), index=True)
    sync_type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    detail: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
