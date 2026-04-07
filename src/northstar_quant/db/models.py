"""数据库表模型定义。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, Integer, String, Text
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


class OrderRecord(Base):
    """订单记录表。"""

    __tablename__ = "order_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    target_weight: Mapped[float | None] = mapped_column(Float, default=None)
    order_semantic: Mapped[str | None] = mapped_column(String(16), default=None)
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


class BrokerSyncLog(Base):
    """券商同步日志表。"""

    __tablename__ = "broker_sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    broker: Mapped[str] = mapped_column(String(32), index=True)
    sync_type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    detail: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
