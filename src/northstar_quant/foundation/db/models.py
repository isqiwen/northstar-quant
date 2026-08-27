"""数据库表模型定义。"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from northstar_quant.foundation.common.time import utc_now
from northstar_quant.foundation.db.base import Base
from northstar_quant.foundation.db.types import UTCDateTime


_SIMULATED_BROKER_STATE_HASH_PATTERN = r"^[0-9a-f]{64}$"
_CONTRACT_AUTHORITY_HASH_PATTERN = r"^[0-9a-f]{64}$"


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
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            name="uq_strategy_run_records_run_id",
        ),
    )

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
    instrument_id: Mapped[str | None] = mapped_column(String(32), default=None)
    exchange_id: Mapped[str | None] = mapped_column(String(16), default=None)
    ctp_offset: Mapped[str | None] = mapped_column(String(24), default=None)
    volume_multiple: Mapped[int | None] = mapped_column(Integer, default=None)
    margin_rate: Mapped[float | None] = mapped_column(Float, default=None)
    required_margin: Mapped[float | None] = mapped_column(Float, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class ExecutionProvenanceConsumptionRecord(Base):
    """Append-only consumption of one P8 CTP-sim provenance commitment.

    This deliberately records only hash-bound provenance, never a broker capability.
    The unique key prevents a final CTP-sim gate from consuming the same plan/order
    commitment twice for the same broker account.
    """

    __tablename__ = "execution_provenance_consumption_records"
    __table_args__ = (
        UniqueConstraint(
            "broker",
            "account",
            "plan_hash",
            "order_hash",
            name="uq_execution_provenance_consumption_plan_order",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    preflight_id: Mapped[str] = mapped_column(String(128), index=True)
    receipt_hash: Mapped[str] = mapped_column(String(64), index=True)
    plan_hash: Mapped[str] = mapped_column(String(64), index=True)
    order_hash: Mapped[str] = mapped_column(String(64), index=True)
    profile_id: Mapped[str] = mapped_column(String(64), index=True)
    broker: Mapped[str] = mapped_column(String(32), index=True)
    account: Mapped[str] = mapped_column(String(64), index=True)
    order_ref: Mapped[str] = mapped_column(String(64), index=True)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    valid_until: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    consumed_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PortfolioRiskApprovalRecord(Base):
    """Append-only, verifier-backed manual approval for one P3 portfolio review.

    This is a durable audit fact, never an execution capability.  The
    application boundary replays the P3 review and exact binding before it can
    write or consume this record.
    """

    __tablename__ = "portfolio_risk_approval_records"
    __table_args__ = (
        UniqueConstraint(
            "approval_id",
            name="uq_portfolio_risk_approval_records_approval_id",
        ),
        UniqueConstraint(
            "profile_id",
            "broker",
            "account",
            "binding_hash",
            name="uq_portfolio_risk_approval_records_scope_binding",
        ),
        UniqueConstraint(
            "record_hash",
            name="uq_portfolio_risk_approval_records_record_hash",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    approval_id: Mapped[str] = mapped_column(String(128), index=True)
    profile_id: Mapped[str] = mapped_column(String(64), index=True)
    broker: Mapped[str] = mapped_column(String(32), index=True)
    account: Mapped[str] = mapped_column(String(64), index=True)
    review_hash: Mapped[str] = mapped_column(String(64), index=True)
    evidence_hash: Mapped[str] = mapped_column(String(64), index=True)
    portfolio_target_hash: Mapped[str] = mapped_column(String(64), index=True)
    approved_target_hash: Mapped[str] = mapped_column(String(64), index=True)
    composition_hash: Mapped[str] = mapped_column(String(64), index=True)
    composition_evidence_hash: Mapped[str] = mapped_column(String(64), index=True)
    authority_hash: Mapped[str] = mapped_column(String(64), index=True)
    policy_hash: Mapped[str] = mapped_column(String(64), index=True)
    reconciliation_state_hash: Mapped[str] = mapped_column(String(64), index=True)
    binding_hash: Mapped[str] = mapped_column(String(64), index=True)
    attestation_hash: Mapped[str] = mapped_column(String(64), index=True)
    approver_id: Mapped[str] = mapped_column(String(128), index=True)
    verifier_id: Mapped[str] = mapped_column(String(128), index=True)
    verifier_receipt_hash: Mapped[str] = mapped_column(String(64), index=True)
    rationale: Mapped[str] = mapped_column(Text)
    review_evaluated_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    approved_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    verified_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    valid_until: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    record_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), index=True, default=utc_now
    )


_RESEARCH_AGENT_AUDIT_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_RESEARCH_AGENT_TRACE_TOOL_NAMES: tuple[str, ...] = (
    "search_events",
    "search_datasets",
    "get_feature",
    "create_experiment",
    "run_backtest",
    "run_validation",
    "generate_research_card",
)
_RESEARCH_AGENT_FAILURE_CODES: tuple[str, ...] = (
    "RESEARCH_AGENT_RESULT_INVALID",
)
_RESEARCH_AGENT_TRACE_TOOL_NAME_SQL_VALUES = ", ".join(
    f"'{tool_name}'" for tool_name in _RESEARCH_AGENT_TRACE_TOOL_NAMES
)
_RESEARCH_AGENT_FAILURE_CODE_SQL_VALUES = ", ".join(
    f"'{failure_code}'" for failure_code in _RESEARCH_AGENT_FAILURE_CODES
)


class ResearchAgentRunAuditEventRecord(Base):
    """Immutable, hash-only lifecycle fact for one ResearchAgent run.

    A row is an admission reservation or exactly one terminal outcome.  It is
    audit evidence only: all records are permanently ``RESEARCH_ONLY`` and
    never carry a capability, prompt, payload, rationale, or error text.
    """

    __tablename__ = "research_agent_run_audit_events"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "event_kind",
            name="uq_research_agent_audit_run_kind",
        ),
        UniqueConstraint(
            "run_id",
            "is_terminal",
            name="uq_research_agent_audit_run_terminal",
        ),
        UniqueConstraint(
            "record_hash",
            name="uq_research_agent_audit_record_hash",
        ),
        CheckConstraint(
            "event_kind IN ('ADMITTED', 'COMPLETED', 'FAILED')",
            name="ck_research_agent_audit_event_kind",
        ),
        CheckConstraint(
            "lifecycle = 'RESEARCH_ONLY'",
            name="ck_research_agent_audit_research_only",
        ),
        CheckConstraint(
            "eligible_for_trading = false",
            name="ck_research_agent_audit_non_tradable",
        ),
        CheckConstraint(
            "trace_count >= 0",
            name="ck_research_agent_audit_trace_count",
        ),
        CheckConstraint(
            f"request_hash ~ '{_RESEARCH_AGENT_AUDIT_SHA256_PATTERN}'",
            name="ck_research_agent_audit_request_hash",
        ),
        CheckConstraint(
            "result_hash IS NULL OR "
            f"result_hash ~ '{_RESEARCH_AGENT_AUDIT_SHA256_PATTERN}'",
            name="ck_research_agent_audit_result_hash",
        ),
        CheckConstraint(
            "trace_root_hash IS NULL OR "
            f"trace_root_hash ~ '{_RESEARCH_AGENT_AUDIT_SHA256_PATTERN}'",
            name="ck_research_agent_audit_trace_root_hash",
        ),
        CheckConstraint(
            "trace_tail_hash IS NULL OR "
            f"trace_tail_hash ~ '{_RESEARCH_AGENT_AUDIT_SHA256_PATTERN}'",
            name="ck_research_agent_audit_trace_tail_hash",
        ),
        CheckConstraint(
            "predecessor_record_hash IS NULL OR "
            f"predecessor_record_hash ~ '{_RESEARCH_AGENT_AUDIT_SHA256_PATTERN}'",
            name="ck_research_agent_audit_predecessor_record_hash",
        ),
        CheckConstraint(
            f"record_hash ~ '{_RESEARCH_AGENT_AUDIT_SHA256_PATTERN}'",
            name="ck_research_agent_audit_record_hash_shape",
        ),
        CheckConstraint(
            "failure_code IS NULL OR "
            f"failure_code IN ({_RESEARCH_AGENT_FAILURE_CODE_SQL_VALUES})",
            name="ck_research_agent_audit_failure_code",
        ),
        CheckConstraint(
            "(event_kind = 'ADMITTED' AND is_terminal = false "
            "AND result_hash IS NULL AND failure_code IS NULL "
            "AND trace_count = 0 AND trace_root_hash IS NULL "
            "AND trace_tail_hash IS NULL AND predecessor_record_hash IS NULL) "
            "OR (event_kind = 'COMPLETED' AND is_terminal = true "
            "AND result_hash IS NOT NULL AND failure_code IS NULL "
            "AND trace_count > 0 AND trace_root_hash IS NOT NULL "
            "AND trace_tail_hash IS NOT NULL AND predecessor_record_hash IS NOT NULL) "
            "OR (event_kind = 'FAILED' AND is_terminal = true "
            "AND result_hash IS NULL AND failure_code IS NOT NULL "
            "AND trace_count = 0 AND trace_root_hash IS NULL "
            "AND trace_tail_hash IS NULL AND predecessor_record_hash IS NOT NULL)",
            name="ck_research_agent_audit_event_shape",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    event_kind: Mapped[str] = mapped_column(String(16), index=True)
    is_terminal: Mapped[bool] = mapped_column(Boolean, index=True)
    request_hash: Mapped[str] = mapped_column(String(64), index=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    failure_code: Mapped[str | None] = mapped_column(String(128), index=True, default=None)
    trace_count: Mapped[int] = mapped_column(Integer)
    trace_root_hash: Mapped[str | None] = mapped_column(
        String(64), index=True, default=None
    )
    trace_tail_hash: Mapped[str | None] = mapped_column(
        String(64), index=True, default=None
    )
    as_of: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    predecessor_record_hash: Mapped[str | None] = mapped_column(
        String(64), index=True, default=None
    )
    lifecycle: Mapped[str] = mapped_column(String(32), index=True)
    eligible_for_trading: Mapped[bool] = mapped_column(Boolean, index=True)
    record_hash: Mapped[str] = mapped_column(String(64), index=True)


class ResearchAgentRunTraceEntryRecord(Base):
    """One immutable, hash-only ordered entry in a durable ResearchAgent trace."""

    __tablename__ = "research_agent_run_trace_entries"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_research_agent_trace_run_sequence",
        ),
        UniqueConstraint(
            "run_id",
            "trace_hash",
            name="uq_research_agent_trace_run_hash",
        ),
        UniqueConstraint(
            "record_hash",
            name="uq_research_agent_trace_record_hash",
        ),
        CheckConstraint(
            "sequence > 0",
            name="ck_research_agent_trace_positive_sequence",
        ),
        CheckConstraint(
            "lifecycle = 'RESEARCH_ONLY'",
            name="ck_research_agent_trace_research_only",
        ),
        CheckConstraint(
            "eligible_for_trading = false",
            name="ck_research_agent_trace_non_tradable",
        ),
        CheckConstraint(
            f"tool_name IN ({_RESEARCH_AGENT_TRACE_TOOL_NAME_SQL_VALUES})",
            name="ck_research_agent_trace_tool_name",
        ),
        CheckConstraint(
            f"request_hash ~ '{_RESEARCH_AGENT_AUDIT_SHA256_PATTERN}'",
            name="ck_research_agent_trace_request_hash",
        ),
        CheckConstraint(
            f"response_hash ~ '{_RESEARCH_AGENT_AUDIT_SHA256_PATTERN}'",
            name="ck_research_agent_trace_response_hash",
        ),
        CheckConstraint(
            "predecessor_trace_hash IS NULL OR "
            f"predecessor_trace_hash ~ '{_RESEARCH_AGENT_AUDIT_SHA256_PATTERN}'",
            name="ck_research_agent_trace_predecessor_hash",
        ),
        CheckConstraint(
            f"trace_hash ~ '{_RESEARCH_AGENT_AUDIT_SHA256_PATTERN}'",
            name="ck_research_agent_trace_hash_shape",
        ),
        CheckConstraint(
            f"record_hash ~ '{_RESEARCH_AGENT_AUDIT_SHA256_PATTERN}'",
            name="ck_research_agent_trace_record_hash_shape",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str] = mapped_column(String(64), index=True)
    request_hash: Mapped[str] = mapped_column(String(64), index=True)
    response_hash: Mapped[str] = mapped_column(String(64), index=True)
    predecessor_trace_hash: Mapped[str | None] = mapped_column(
        String(64), index=True, default=None
    )
    trace_hash: Mapped[str] = mapped_column(String(64), index=True)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    lifecycle: Mapped[str] = mapped_column(String(32), index=True)
    eligible_for_trading: Mapped[bool] = mapped_column(Boolean, index=True)
    record_hash: Mapped[str] = mapped_column(String(64), index=True)


class OrderRecord(Base):
    """订单记录表。"""

    __tablename__ = "order_records"
    __table_args__ = (
        UniqueConstraint(
            "broker",
            "account",
            "idempotency_key",
            name="uq_order_records_broker_account_idempotency_key",
        ),
        UniqueConstraint(
            "broker",
            "account",
            "order_ref",
            name="uq_order_records_broker_account_order_ref",
        ),
        UniqueConstraint(
            "broker",
            "account",
            "perm_id",
            name="uq_order_records_broker_account_perm_id",
        ),
        UniqueConstraint(
            "broker",
            "account",
            "client_id",
            "broker_order_id",
            name="uq_order_records_broker_account_client_order_id",
        ),
    )

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
    broker: Mapped[str | None] = mapped_column(String(32), index=True, default=None)
    account: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    instrument_id: Mapped[str | None] = mapped_column(String(32), index=True, default=None)
    exchange_id: Mapped[str | None] = mapped_column(String(16), default=None)
    ctp_offset: Mapped[str | None] = mapped_column(String(24), default=None)
    volume_multiple: Mapped[int | None] = mapped_column(Integer, default=None)
    margin_rate: Mapped[float | None] = mapped_column(Float, default=None)
    required_margin: Mapped[float | None] = mapped_column(Float, default=None)
    currency: Mapped[str | None] = mapped_column(String(8), default=None)
    reference_price: Mapped[float | None] = mapped_column(Float, default=None)
    reference_price_source: Mapped[str | None] = mapped_column(String(32), default=None)
    planned_trade_value: Mapped[float | None] = mapped_column(Float, default=None)
    execution_planner_id: Mapped[str | None] = mapped_column(String(64), default=None)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    batch_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    plan_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(80),
        index=True,
        default=None,
    )
    request_fingerprint: Mapped[str | None] = mapped_column(
        String(64),
        default=None,
    )
    execution_policy_fingerprint: Mapped[str | None] = mapped_column(
        String(64),
        index=True,
        default=None,
    )
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    order_ref: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    broker_order_id: Mapped[str | None] = mapped_column(String(128), default=None)
    client_id: Mapped[int | None] = mapped_column(Integer, default=None)
    perm_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    filled_qty: Mapped[float | None] = mapped_column(Float, default=None)
    remaining_qty: Mapped[float | None] = mapped_column(Float, default=None)
    avg_fill_price: Mapped[float | None] = mapped_column(Float, default=None)
    status: Mapped[str] = mapped_column(String(32), index=True)
    submission_owner: Mapped[str | None] = mapped_column(
        String(64),
        index=True,
        default=None,
    )
    lease_fencing_token: Mapped[int | None] = mapped_column(Integer, default=None)
    last_submission_error: Mapped[str | None] = mapped_column(Text, default=None)
    prepared_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    submission_started_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        default=None,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
        default=None,
    )
    broker_acknowledged_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        default=None,
    )
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class ExecutionLeaseRecord(Base):
    """跨进程执行租约。

    ``resource_key`` 是全局唯一资源；只有持有匹配 ``owner_token`` 且租约未
    过期的进程可以继续提交订单。
    """

    __tablename__ = "execution_lease_records"

    resource_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    owner_token: Mapped[str] = mapped_column(String(64), index=True)
    fencing_token: Mapped[int] = mapped_column(Integer, default=1)
    acquired_at: Mapped[datetime] = mapped_column(UTCDateTime())
    heartbeat_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class FillRecord(Base):
    """成交记录表。"""

    __tablename__ = "fill_records"
    __table_args__ = (
        UniqueConstraint(
            "broker",
            "account",
            "exec_id",
            name="uq_fill_records_broker_account_exec_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int | None] = mapped_column(
        Integer,
        index=True,
        nullable=True,
        default=None,
    )
    broker: Mapped[str | None] = mapped_column(String(32), index=True, default=None)
    account: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    exec_id: Mapped[str | None] = mapped_column(String(128), index=True, default=None)
    perm_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    client_id: Mapped[int | None] = mapped_column(Integer, default=None)
    instrument_id: Mapped[str | None] = mapped_column(String(32), index=True, default=None)
    exchange_id: Mapped[str | None] = mapped_column(String(16), default=None)
    ctp_offset: Mapped[str | None] = mapped_column(String(24), default=None)
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
    instrument_id: Mapped[str | None] = mapped_column(String(32), default=None)
    exchange_id: Mapped[str | None] = mapped_column(String(16), default=None)
    long_today_qty: Mapped[float | None] = mapped_column(Float, default=None)
    long_yesterday_qty: Mapped[float | None] = mapped_column(Float, default=None)
    short_today_qty: Mapped[float | None] = mapped_column(Float, default=None)
    short_yesterday_qty: Mapped[float | None] = mapped_column(Float, default=None)
    long_frozen_qty: Mapped[float | None] = mapped_column(Float, default=None)
    short_frozen_qty: Mapped[float | None] = mapped_column(Float, default=None)
    long_closable_qty: Mapped[float | None] = mapped_column(Float, default=None)
    short_closable_qty: Mapped[float | None] = mapped_column(Float, default=None)
    margin: Mapped[float | None] = mapped_column(Float, default=None)
    realized_pnl: Mapped[float | None] = mapped_column(Float, default=None)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float, default=None)
    asof: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, default=utc_now)
    snapshot_batch_id: Mapped[str] = mapped_column(String(64), index=True)


class PositionSnapshotBatchRecord(Base):
    """一次完整券商持仓快照的批次头。

    明细表无法表达“本次同步确认账户为空仓”，因此批次头必须独立存在。broker、
    account 与 profile_id 同时定义查询作用域，避免多账户环境读取到其他账户的最近
    一批持仓。
    """

    __tablename__ = "position_snapshot_batch_records"

    snapshot_batch_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    broker: Mapped[str | None] = mapped_column(String(32), index=True, default=None)
    account: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    position_count: Mapped[int] = mapped_column(Integer, default=0)
    asof: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, default=utc_now)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


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
    interest_cash_flow: Mapped[float | None] = mapped_column(Float, default=None)
    fee_cash_flow: Mapped[float | None] = mapped_column(Float, default=None)
    tax_cash_flow: Mapped[float | None] = mapped_column(Float, default=None)
    funding_cash_flow: Mapped[float | None] = mapped_column(Float, default=None)
    other_non_trade_cash_flow: Mapped[float | None] = mapped_column(Float, default=None)
    total_non_trade_cash_flow: Mapped[float | None] = mapped_column(Float, default=None)
    traded_notional: Mapped[float | None] = mapped_column(Float, default=None)
    fill_count: Mapped[int] = mapped_column(Integer, default=0)
    residual_pnl: Mapped[float | None] = mapped_column(Float, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class SettlementRecord(Base):
    """券商结算的追加式事实记录，不覆盖账户或持仓快照。"""

    __tablename__ = "settlement_records"
    __table_args__ = (
        UniqueConstraint(
            "broker",
            "account",
            "settlement_id",
            name="uq_settlement_records_broker_account_settlement_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    settlement_id: Mapped[str] = mapped_column(String(128), index=True)
    settlement_date: Mapped[date] = mapped_column(Date, index=True)
    broker: Mapped[str] = mapped_column(String(32), index=True)
    account: Mapped[str] = mapped_column(String(64), index=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    account_snapshot_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    cash_balance: Mapped[float | None] = mapped_column(Float, default=None)
    margin: Mapped[float | None] = mapped_column(Float, default=None)
    realized_pnl: Mapped[float | None] = mapped_column(Float, default=None)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float, default=None)
    fee: Mapped[float | None] = mapped_column(Float, default=None)
    currency: Mapped[str] = mapped_column(String(8))
    evidence_json: Mapped[str] = mapped_column(Text)
    settled_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class LedgerAdjustmentRecord(Base):
    """仅可追加、必须具名审批的账本调整记录。"""

    __tablename__ = "ledger_adjustment_records"
    __table_args__ = (
        UniqueConstraint(
            "adjustment_id",
            name="uq_ledger_adjustment_records_adjustment_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    adjustment_id: Mapped[str] = mapped_column(String(64), index=True)
    broker: Mapped[str] = mapped_column(String(32), index=True)
    account: Mapped[str] = mapped_column(String(64), index=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8))
    reason: Mapped[str] = mapped_column(Text)
    approver_id: Mapped[str] = mapped_column(String(128), index=True)
    evidence_json: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
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


class RuntimeRiskRecord(Base):
    """盘中实时风控的一次持久化结论。"""

    __tablename__ = "runtime_risk_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(String(64), index=True)
    broker: Mapped[str] = mapped_column(String(32), index=True)
    account: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    can_submit: Mapped[bool] = mapped_column(Boolean, index=True, default=False)
    blocking_failure_count: Mapped[int] = mapped_column(Integer, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    checks_json: Mapped[str] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        index=True,
        default=utc_now,
    )


class ReconciliationSafetyStateRecord(Base):
    """不可自动解除的对账交易安全状态审计链。"""

    __tablename__ = "reconciliation_safety_state_records"
    __table_args__ = (
        UniqueConstraint(
            "state_hash",
            name="uq_reconciliation_safety_state_records_state_hash",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(String(64), index=True)
    broker: Mapped[str] = mapped_column(String(32), index=True)
    account: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    state: Mapped[str] = mapped_column(String(32), index=True)
    reason: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
        server_default="{}",
    )
    predecessor_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    state_hash: Mapped[str] = mapped_column(String(64))
    recovery_approver_id: Mapped[str | None] = mapped_column(
        String(128), default=None
    )
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), index=True, default=utc_now
    )


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


class SimulatedBrokerStateRecord(Base):
    """Paper / CTP-sim 柜台的当前 PostgreSQL 状态。

    该表保存的是模拟柜台状态机的当前受控快照，不替代订单、成交、持仓、账户和
    对账账本的权威记录。每次变更必须同时写入不可变的
    :class:`SimulatedBrokerStateTransitionRecord`，由 repository 在同一账户级事务中
    校验 hash 链与 revision。
    """

    __tablename__ = "simulated_broker_state_records"
    __table_args__ = (
        UniqueConstraint(
            "broker",
            "account",
            name="uq_simulated_broker_state_records_broker_account",
        ),
        CheckConstraint(
            "broker IN ('paper', 'ctp_sim')",
            name="ck_simulated_broker_state_records_broker",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_simulated_broker_state_records_schema_version",
        ),
        CheckConstraint(
            "revision >= 0",
            name="ck_simulated_broker_state_records_revision",
        ),
        CheckConstraint(
            f"state_hash ~ '{_SIMULATED_BROKER_STATE_HASH_PATTERN}'",
            name="ck_simulated_broker_state_records_state_hash",
        ),
        CheckConstraint(
            f"last_transition_hash ~ '{_SIMULATED_BROKER_STATE_HASH_PATTERN}'",
            name="ck_simulated_broker_state_records_last_transition_hash",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    broker: Mapped[str] = mapped_column(String(32), index=True)
    account: Mapped[str] = mapped_column(String(64), index=True)
    schema_version: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    state_json: Mapped[str] = mapped_column(Text)
    state_hash: Mapped[str] = mapped_column(String(64), index=True)
    last_transition_hash: Mapped[str] = mapped_column(String(64), index=True)
    initialized_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class SimulatedBrokerStateTransitionRecord(Base):
    """模拟柜台状态变更的不可变审计链。"""

    __tablename__ = "simulated_broker_state_transition_records"
    __table_args__ = (
        UniqueConstraint(
            "broker",
            "account",
            "revision",
            name="uq_simulated_broker_state_transition_scope_revision",
        ),
        UniqueConstraint(
            "transition_hash",
            name="uq_simulated_broker_state_transition_hash",
        ),
        Index(
            "ix_sim_broker_state_transition_predecessor_hash",
            "predecessor_transition_hash",
        ),
        CheckConstraint(
            "broker IN ('paper', 'ctp_sim')",
            name="ck_simulated_broker_state_transition_broker",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_simulated_broker_state_transition_schema_version",
        ),
        CheckConstraint(
            "revision >= 0",
            name="ck_simulated_broker_state_transition_revision",
        ),
        CheckConstraint(
            "action ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_simulated_broker_state_transition_action",
        ),
        CheckConstraint(
            f"state_hash ~ '{_SIMULATED_BROKER_STATE_HASH_PATTERN}'",
            name="ck_simulated_broker_state_transition_state_hash",
        ),
        CheckConstraint(
            "predecessor_transition_hash IS NULL OR "
            f"predecessor_transition_hash ~ '{_SIMULATED_BROKER_STATE_HASH_PATTERN}'",
            name="ck_simulated_broker_state_transition_predecessor_hash",
        ),
        CheckConstraint(
            f"transition_hash ~ '{_SIMULATED_BROKER_STATE_HASH_PATTERN}'",
            name="ck_simulated_broker_state_transition_hash_shape",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    broker: Mapped[str] = mapped_column(String(32), index=True)
    account: Mapped[str] = mapped_column(String(64), index=True)
    schema_version: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(64), index=True)
    state_json: Mapped[str] = mapped_column(Text)
    state_hash: Mapped[str] = mapped_column(String(64), index=True)
    predecessor_transition_hash: Mapped[str | None] = mapped_column(
        String(64),
        default=None,
    )
    transition_hash: Mapped[str] = mapped_column(String(64), index=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class ContractMasterPublicationRecord(Base):
    """Append-only Contract Master authority publication.

    The payload is a strict, canonical serialization of the typed data-domain
    aggregate.  PostgreSQL stores the immutable release fact; reconstruction,
    domain validation, and point-in-time selection stay in the Data domain.
    """

    __tablename__ = "contract_master_publication_records"
    __table_args__ = (
        UniqueConstraint(
            "authority_id",
            "publication_id",
            name="uq_contract_master_publication_authority_publication",
        ),
        UniqueConstraint(
            "authority_id",
            "available_at",
            name="uq_contract_master_publication_authority_available_at",
        ),
        UniqueConstraint(
            "publication_hash",
            name="uq_contract_master_publication_hash",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_contract_master_publication_schema_version",
        ),
        CheckConstraint(
            "available_at >= observed_at",
            name="ck_contract_master_publication_time_order",
        ),
        CheckConstraint(
            "quality_status = 'pass'",
            name="ck_contract_master_publication_quality_pass",
        ),
        CheckConstraint(
            f"source_artifact_hash ~ '{_CONTRACT_AUTHORITY_HASH_PATTERN}'",
            name="ck_contract_master_publication_source_hash",
        ),
        CheckConstraint(
            f"master_fingerprint ~ '{_CONTRACT_AUTHORITY_HASH_PATTERN}'",
            name="ck_contract_master_publication_master_fingerprint",
        ),
        CheckConstraint(
            f"content_hash ~ '{_CONTRACT_AUTHORITY_HASH_PATTERN}'",
            name="ck_contract_master_publication_content_hash",
        ),
        CheckConstraint(
            f"publication_hash ~ '{_CONTRACT_AUTHORITY_HASH_PATTERN}'",
            name="ck_contract_master_publication_publication_hash_shape",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    authority_id: Mapped[str] = mapped_column(String(128), index=True)
    publication_id: Mapped[str] = mapped_column(String(128), index=True)
    schema_version: Mapped[int] = mapped_column(Integer)
    master_id: Mapped[str] = mapped_column(String(128), index=True)
    master_version: Mapped[str] = mapped_column(String(128))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    source_artifact_hash: Mapped[str] = mapped_column(String(64), index=True)
    source_authority: Mapped[str] = mapped_column(String(256))
    quality_status: Mapped[str] = mapped_column(String(16), default="pass")
    master_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    publication_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class CtpContractRegistryPublicationRecord(Base):
    """Append-only broker registry publication bound to one Master release."""

    __tablename__ = "ctp_contract_registry_publication_records"
    __table_args__ = (
        UniqueConstraint(
            "authority_id",
            "publication_id",
            "broker",
            name="uq_ctp_registry_publication_authority_publication_broker",
        ),
        UniqueConstraint(
            "authority_id",
            "broker",
            "available_at",
            name="uq_ctp_registry_publication_authority_broker_available_at",
        ),
        UniqueConstraint(
            "publication_hash",
            name="uq_ctp_registry_publication_hash",
        ),
        CheckConstraint(
            "broker IN ('ctp', 'ctp_sim')",
            name="ck_ctp_registry_publication_broker",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_ctp_registry_publication_schema_version",
        ),
        CheckConstraint(
            "available_at >= observed_at",
            name="ck_ctp_registry_publication_time_order",
        ),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_ctp_registry_publication_effective_window",
        ),
        CheckConstraint(
            "quality_status = 'pass'",
            name="ck_ctp_registry_publication_quality_pass",
        ),
        CheckConstraint(
            f"master_publication_hash ~ '{_CONTRACT_AUTHORITY_HASH_PATTERN}'",
            name="ck_ctp_registry_publication_master_hash",
        ),
        CheckConstraint(
            f"source_artifact_hash ~ '{_CONTRACT_AUTHORITY_HASH_PATTERN}'",
            name="ck_ctp_registry_publication_source_hash",
        ),
        CheckConstraint(
            f"registry_fingerprint ~ '{_CONTRACT_AUTHORITY_HASH_PATTERN}'",
            name="ck_ctp_registry_publication_registry_fingerprint",
        ),
        CheckConstraint(
            f"content_hash ~ '{_CONTRACT_AUTHORITY_HASH_PATTERN}'",
            name="ck_ctp_registry_publication_content_hash",
        ),
        CheckConstraint(
            f"publication_hash ~ '{_CONTRACT_AUTHORITY_HASH_PATTERN}'",
            name="ck_ctp_registry_publication_publication_hash_shape",
        ),
        Index(
            "ix_ctp_registry_publication_master_hash",
            "master_publication_hash",
        ),
        Index(
            "ix_ctp_registry_publication_source_hash",
            "source_artifact_hash",
        ),
        Index(
            "ix_ctp_registry_publication_fingerprint",
            "registry_fingerprint",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    authority_id: Mapped[str] = mapped_column(String(128), index=True)
    publication_id: Mapped[str] = mapped_column(String(128), index=True)
    broker: Mapped[str] = mapped_column(String(32), index=True)
    schema_version: Mapped[int] = mapped_column(Integer)
    master_publication_hash: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    effective_from: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    effective_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    source_artifact_hash: Mapped[str] = mapped_column(String(64))
    source_authority: Mapped[str] = mapped_column(String(256))
    quality_status: Mapped[str] = mapped_column(String(16), default="pass")
    registry_fingerprint: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    publication_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
