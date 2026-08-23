"""数据库写入辅助函数。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from hashlib import sha256
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, SupportsFloat, SupportsIndex
from uuid import uuid4

import polars as pl
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from northstar_quant.platform.common.order_identity import (
    build_order_idempotency_key,
    build_order_ref,
    build_order_request_fingerprint,
)
from northstar_quant.platform.common.order_status import (
    FINAL_ORDER_STATUSES,
    is_filled_order_status,
    is_final_order_status,
)
from northstar_quant.platform.common.time import ensure_utc, utc_now
from northstar_quant.platform.db.models import (
    _RESEARCH_AGENT_FAILURE_CODES,
    _RESEARCH_AGENT_TRACE_TOOL_NAMES,
    AccountAttributionRecord,
    AccountSnapshotRecord,
    AnomalyEventRecord,
    BrokerSyncLog,
    CancelRecord,
    ExecutionLeaseRecord,
    ExecutionPlanRecord,
    ExecutionProvenanceConsumptionRecord,
    FillRecord,
    LedgerAdjustmentRecord,
    OrderRecord,
    PortfolioRiskApprovalRecord,
    PositionSnapshotBatchRecord,
    PositionSnapshotRecord,
    ResearchAgentRunAuditEventRecord,
    ResearchAgentRunTraceEntryRecord,
    ReconciliationSafetyStateRecord,
    RuntimeRiskRecord,
    RunHealthRecord,
    SettlementRecord,
    StrategyRunRecord,
    StrategySnapshotRecord,
    TradeAttributionRecord,
    WorkingOrderSnapshotRecord,
)
if TYPE_CHECKING:
    # Repository 只读取这些交易 DTO 的字段；保留精确注解但不在运行时反向加载业务领域。
    from northstar_quant.trading_execution.execution.models import (
        BrokerStateSnapshot,
        FillSnapshot,
        OrderRequest,
        OrderResult,
        PositionSnapshot,
        RebalanceOrderPlan,
    )


_UNSCOPED_RECONCILIATION_PROFILE = "__unscoped__"


def save_position_snapshot_batch(
    session: Session,
    snapshots: list[PositionSnapshot],
    *,
    broker: str | None = None,
    account: str | None = None,
    profile_id: str | None = None,
    run_id: str | None = None,
    snapshot_batch_id: str | None = None,
    asof: datetime | None = None,
    commit: bool = True,
) -> PositionSnapshotBatchRecord:
    """批量保存真实持仓快照。

    每次保存都视为一次完整的“持仓批次”：
    - 同一批共享一个 snapshot_batch_id
    - 同一批共享一个 asof
    - 空仓也写入批次头，防止旧的非空批次继续被误读

    这样即便上游误传了逐行不同的时间戳，库里仍能保留稳定的批次边界。
    """

    asof_values = [ensure_utc(item.asof) for item in snapshots if item.asof is not None]
    batch_asof = ensure_utc(asof) if asof is not None else (
        max(asof_values) if asof_values else utc_now()
    )
    item_batch_ids = {
        str(item.snapshot_batch_id).strip()
        for item in snapshots
        if item.snapshot_batch_id
    }
    if snapshot_batch_id is not None:
        item_batch_ids.add(str(snapshot_batch_id).strip())
    if len(item_batch_ids) > 1:
        raise ValueError("同一持仓快照包含多个 snapshot_batch_id")
    batch_id = next(iter(item_batch_ids), f"position-batch-{uuid4().hex}")

    item_accounts = {
        str(item.account).strip()
        for item in snapshots
        if item.account and str(item.account).strip()
    }
    normalized_account = str(account or "").strip() or None
    if normalized_account is not None:
        item_accounts.add(normalized_account)
    if len(item_accounts) > 1:
        raise ValueError("同一持仓快照包含多个账户，已拒绝写入")
    resolved_account = next(iter(item_accounts), None)
    normalized_broker = str(broker or "").strip().lower() or None

    existing_batch = session.get(PositionSnapshotBatchRecord, batch_id)
    if existing_batch is not None:
        raise ValueError(f"持仓快照批次已存在：{batch_id}")

    batch_row = PositionSnapshotBatchRecord(
        snapshot_batch_id=batch_id,
        run_id=run_id,
        profile_id=profile_id,
        broker=normalized_broker,
        account=resolved_account,
        position_count=len(snapshots),
        asof=batch_asof,
    )
    session.add(batch_row)

    for item in snapshots:
        item.snapshot_batch_id = batch_id
        if item.account is None:
            item.account = resolved_account
        session.add(
            PositionSnapshotRecord(
                account=resolved_account,
                symbol=item.symbol,
                qty=item.qty,
                avg_cost=item.avg_cost,
                market_price=item.market_price,
                market_value=item.market_value,
                instrument_id=item.instrument_id,
                exchange_id=item.exchange_id,
                long_today_qty=item.long_today_qty,
                long_yesterday_qty=item.long_yesterday_qty,
                short_today_qty=item.short_today_qty,
                short_yesterday_qty=item.short_yesterday_qty,
                long_frozen_qty=item.long_frozen_qty,
                short_frozen_qty=item.short_frozen_qty,
                long_closable_qty=item.long_closable_qty,
                short_closable_qty=item.short_closable_qty,
                margin=item.margin,
                realized_pnl=item.realized_pnl,
                unrealized_pnl=item.unrealized_pnl,
                asof=batch_asof,
                snapshot_batch_id=batch_id,
            )
        )
    if commit:
        session.commit()
        session.refresh(batch_row)
    else:
        session.flush()
    return batch_row


def _serialize_json(payload: object | None) -> str | None:
    if payload is None:
        return None
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _affected_rows(result: object) -> int:
    """兼容 SQLAlchemy 不同 Result 类型读取受影响行数。"""

    return int(getattr(result, "rowcount", 0) or 0)


def _database_utc_now(session: Session) -> datetime:
    """读取数据库时钟，避免跨进程租约依赖各主机本地时间。"""

    value = session.scalar(select(func.current_timestamp()))
    if not isinstance(value, datetime):
        raise RuntimeError("数据库未返回可用的 CURRENT_TIMESTAMP。")
    return ensure_utc(value)


def _deserialize_json_dict(payload: str | None) -> dict[str, object]:
    if not payload:
        return {}
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _coerce_snapshot_time(value: object | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return ensure_utc(parsed)
    return None


def _latest_frame_asof(
    frame: pl.DataFrame | None,
    *,
    preferred_columns: Sequence[str] = ("date", "timestamp", "ts", "datetime", "asof"),
) -> datetime | None:
    if frame is None or frame.is_empty():
        return None

    for column in preferred_columns:
        if column not in frame.columns:
            continue
        return _coerce_snapshot_time(frame[column].max())
    return None


def _optional_float(value: object | None) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (str, bytes, bytearray, SupportsFloat, SupportsIndex)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_execution_provenance_text(
    value: object,
    *,
    field_name: str,
    sha256: bool = False,
) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{field_name} is required")
    if sha256 and re.fullmatch(r"[0-9a-f]{64}", text) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256")
    return text


def _first_float(*values: object | None) -> float | None:
    for value in values:
        parsed = _optional_float(value)
        if parsed is not None:
            return parsed
    return None


def _enum_text(value: object) -> str:
    enum_value = getattr(value, "value", value)
    return str(enum_value)


_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_NON_TRADE_KEY_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("interest", ("interest",)),
    ("tax", ("withholding tax", "withholding", "tax", "levy")),
    ("fee", ("commission", "commissions", "fee", "fees", "charge", "charges", "regulatory fee")),
    (
        "funding",
        (
            "deposit",
            "withdraw",
            "withdrawal",
            "transfer",
            "cash transfer",
            "funding",
            "wire",
            "journal",
            "incoming funds",
            "outgoing funds",
        ),
    ),
    ("other", ("adjustment", "adjust", "misc", "other")),
)


def _normalize_account_value_key(key: str) -> str:
    text = _CAMEL_CASE_BOUNDARY.sub(" ", str(key))
    text = _NON_ALNUM_RE.sub(" ", text.lower())
    return " ".join(text.split())


def _contains_normalized_phrase(normalized_key: str, phrase: str) -> bool:
    padded_key = f" {normalized_key} "
    padded_phrase = f" {phrase.strip().lower()} "
    return padded_phrase in padded_key


def _classify_non_trade_key(key: str) -> str | None:
    normalized = _normalize_account_value_key(key)
    if not normalized:
        return None
    for category, patterns in _NON_TRADE_KEY_CATEGORIES:
        if any(_contains_normalized_phrase(normalized, pattern) for pattern in patterns):
            return category
    return None


def _non_trade_cash_flow_components(
    start_values: dict[str, object],
    end_values: dict[str, object],
) -> dict[str, float]:
    components = {
        "interest": 0.0,
        "fee": 0.0,
        "tax": 0.0,
        "funding": 0.0,
        "other": 0.0,
    }
    for key in sorted(set(start_values) | set(end_values)):
        category = _classify_non_trade_key(key)
        if category is None:
            continue
        start_value = _optional_float(start_values.get(key)) or 0.0
        end_value = _optional_float(end_values.get(key)) or 0.0
        components[category] += end_value - start_value
    return components


def save_strategy_run_snapshot(
    session: Session,
    *,
    run_id: str,
    profile_id: str,
    pipeline_strategy_id: str,
    output_type: object,
    time_column: str,
    output_frame: pl.DataFrame,
    selected_strategy_ids: Sequence[str],
    strategy_params: dict[str, object] | None = None,
    risk_limits: dict[str, object] | None = None,
    market_data_frame: pl.DataFrame | None = None,
    signal_data_frame: pl.DataFrame | None = None,
) -> StrategyRunRecord:
    """保存一次策略账本快照。"""

    market_data_asof = _latest_frame_asof(market_data_frame)
    signal_data_asof = _latest_frame_asof(signal_data_frame)
    output_asof = _latest_frame_asof(
        output_frame,
        preferred_columns=(time_column, "asof", "timestamp", "date", "datetime", "ts"),
    )
    row = StrategyRunRecord(
        run_id=run_id,
        profile_id=profile_id,
        pipeline_strategy_id=pipeline_strategy_id,
        output_type=_enum_text(output_type),
        selected_strategy_ids_json=_serialize_json(list(selected_strategy_ids)),
        strategy_params_json=_serialize_json(strategy_params or {}),
        risk_limits_json=_serialize_json(risk_limits or {}),
        market_data_asof=market_data_asof,
        signal_data_asof=signal_data_asof,
        output_asof=output_asof,
        snapshot_count=int(output_frame.height),
    )
    session.add(row)

    fallback_asof = output_asof or signal_data_asof or market_data_asof or utc_now()
    for payload in output_frame.to_dicts():
        row_asof = _coerce_snapshot_time(payload.get(time_column)) or fallback_asof
        session.add(
            StrategySnapshotRecord(
                run_id=run_id,
                profile_id=profile_id,
                pipeline_strategy_id=pipeline_strategy_id,
                source_strategy_id=_optional_text(payload.get("strategy_id")),
                output_type=_enum_text(output_type),
                symbol=str(payload.get("symbol") or "").strip().upper(),
                signal_value=_optional_float(payload.get("signal_value")),
                target_weight=_optional_float(payload.get("target_weight")),
                side=_optional_text(payload.get("side")),
                size_fraction=_optional_float(payload.get("size_fraction")),
                order_semantic=_optional_text(payload.get("order_semantic")),
                order_type=_optional_text(payload.get("order_type")),
                limit_price=_optional_float(payload.get("limit_price")),
                reason=_optional_text(payload.get("reason")),
                asof=row_asof,
            )
        )

    session.commit()
    session.refresh(row)
    return row


def get_strategy_run_by_run_id(
    session: Session,
    run_id: str,
) -> StrategyRunRecord | None:
    """按稳定运行 ID 读取策略快照头。"""

    return session.scalar(
        select(StrategyRunRecord).where(
            StrategyRunRecord.run_id == str(run_id).strip()
        )
    )


def latest_strategy_run(
    session: Session,
    *,
    profile_id: str,
    run_id_prefix: str | None = None,
) -> StrategyRunRecord | None:
    """读取画像最近一次已经冻结的策略输出。"""

    stmt = select(StrategyRunRecord).where(
        StrategyRunRecord.profile_id == str(profile_id).strip()
    )
    normalized_prefix = str(run_id_prefix or "").strip()
    if normalized_prefix:
        stmt = stmt.where(StrategyRunRecord.run_id.startswith(normalized_prefix))
    return session.scalar(
        stmt
        .order_by(
            StrategyRunRecord.output_asof.desc(),
            StrategyRunRecord.created_at.desc(),
            StrategyRunRecord.id.desc(),
        )
        .limit(1)
    )


def list_strategy_snapshots_for_run(
    session: Session,
    *,
    run_id: str,
) -> list[StrategySnapshotRecord]:
    """按稳定运行 ID 读取逐标的策略输出。"""

    return list(
        session.scalars(
            select(StrategySnapshotRecord)
            .where(StrategySnapshotRecord.run_id == str(run_id).strip())
            .order_by(
                StrategySnapshotRecord.asof.asc(),
                StrategySnapshotRecord.symbol.asc(),
                StrategySnapshotRecord.id.asc(),
            )
        )
    )


def save_execution_plan_records(
    session: Session,
    plans: Sequence[RebalanceOrderPlan],
    *,
    run_id: str | None,
    batch_id: str | None,
    profile_id: str | None,
    execution_planner_id: str | None,
    commit: bool = True,
) -> int:
    """保存执行计划账本。"""

    if not plans:
        return 0

    count = 0
    for idx, plan in enumerate(plans, start=1):
        if plan.plan_id is None and batch_id is not None:
            plan.plan_id = f"{batch_id}-{idx:04d}-{str(plan.symbol).lower()}"
        session.add(
            ExecutionPlanRecord(
                run_id=run_id,
                batch_id=batch_id,
                plan_id=plan.plan_id,
                profile_id=profile_id,
                execution_planner_id=execution_planner_id,
                strategy_id=plan.strategy_id,
                symbol=str(plan.symbol).strip().upper(),
                side=plan.side,
                qty=float(plan.qty),
                target_weight=_optional_float(plan.target_weight),
                current_qty=_optional_float(plan.current_qty),
                target_qty=_optional_float(plan.target_qty),
                latest_price=_optional_float(plan.latest_price),
                execution_reference_price=_optional_float(plan.execution_reference_price),
                estimated_trade_value=_optional_float(plan.estimated_trade_value),
                order_semantic=_optional_text(plan.order_semantic),
                reason=_optional_text(plan.reason),
                order_type=_optional_text(plan.order_type),
                limit_price=_optional_float(plan.limit_price),
                instrument_id=_optional_text(plan.instrument_id),
                exchange_id=_optional_text(plan.exchange_id),
                ctp_offset=_optional_text(plan.ctp_offset),
                volume_multiple=plan.volume_multiple,
                margin_rate=_optional_float(plan.margin_rate),
                required_margin=_optional_float(plan.required_margin),
            )
        )
        count += 1

    if commit:
        session.commit()
    else:
        session.flush()
    return count


def _find_order_for_fill(
    session: Session,
    item: FillSnapshot,
    *,
    broker: str | None,
    account: str | None,
) -> OrderRecord | None:
    """按 orderRef → permId → (clientId, orderId) 关联成交。"""

    scope_conditions = []
    if broker:
        scope_conditions.append(func.lower(OrderRecord.broker) == broker)
    if account:
        scope_conditions.append(OrderRecord.account == account)

    candidate_queries = []
    order_ref = str(item.order_ref or "").strip() or None
    if order_ref:
        candidate_queries.append(
            [*scope_conditions, OrderRecord.order_ref == order_ref]
        )
    if item.perm_id is not None:
        candidate_queries.append(
            [*scope_conditions, OrderRecord.perm_id == int(item.perm_id)]
        )
    broker_order_id = str(item.broker_order_id or "").strip() or None
    if item.client_id is not None and broker_order_id:
        candidate_queries.append(
            [
                *scope_conditions,
                OrderRecord.broker_order_id == broker_order_id,
                OrderRecord.client_id == int(item.client_id),
            ]
        )

    for query_conditions in candidate_queries:
        candidates = list(
            session.scalars(
                select(OrderRecord)
                .where(*query_conditions)
                .order_by(OrderRecord.id.desc())
                .limit(2)
            )
        )
        if len(candidates) > 1:
            raise RuntimeError(
                "FILL_ORDER_IDENTITY_AMBIGUOUS: 成交身份对应多条本地订单，"
                "已停止自动归属。"
            )
        if candidates:
            return candidates[0]

    # 迁移前本地订单可能尚无强身份。此时只允许在 broker/account 范围内
    # 唯一回退到 orderId；后续完整身份断言仍会拒绝任何已存在字段冲突。
    if not broker_order_id:
        return None
    candidates = list(
        session.scalars(
            select(OrderRecord)
            .where(
                *scope_conditions,
                OrderRecord.broker_order_id == broker_order_id,
            )
            .order_by(OrderRecord.id.desc())
            .limit(2)
        )
    )
    if len(candidates) > 1:
        raise RuntimeError(
            "FILL_ORDER_IDENTITY_AMBIGUOUS: 旧成交 orderId 对应多条本地订单，"
            "已停止自动归属。"
        )
    if candidates:
        return candidates[0]
    return None


def _assert_fill_matches_order(
    order_row: OrderRecord,
    item: FillSnapshot,
) -> None:
    """验证成交与本地订单的强身份和交易语义完全一致。"""

    _assert_broker_order_identity(
        order_row,
        broker_order_id=str(item.broker_order_id or "").strip() or None,
        order_ref=str(item.order_ref or "").strip() or None,
        client_id=item.client_id,
        perm_id=item.perm_id,
        instrument_id=item.instrument_id,
        exchange_id=item.exchange_id,
        symbol=item.symbol,
    )
    if (
        order_row.side
        and item.side
        and str(order_row.side).strip().upper()
        != str(item.side).strip().upper()
    ):
        raise RuntimeError(
            "FILL_ORDER_IDENTITY_MISMATCH: "
            f"order_id={order_row.id}，field=side，"
            f"persisted={order_row.side}，observed={item.side}。"
        )
    if (
        order_row.ctp_offset
        and item.ctp_offset
        and str(order_row.ctp_offset).strip().lower()
        != str(item.ctp_offset).strip().lower()
    ):
        raise RuntimeError(
            "FILL_ORDER_IDENTITY_MISMATCH: "
            f"order_id={order_row.id}，field=ctp_offset，"
            f"persisted={order_row.ctp_offset}，observed={item.ctp_offset}。"
        )


def _local_filled_qty(session: Session, order_id: int) -> float:
    return abs(
        float(
            session.scalar(
                select(func.coalesce(func.sum(FillRecord.qty), 0.0)).where(
                    FillRecord.order_id == int(order_id)
                )
            )
            or 0.0
        )
    )


def _update_order_progress_from_fill_ledger(
    session: Session,
    order_row: OrderRecord,
) -> None:
    """用去重成交账本单调推进累计成交，不回退 completed-order 进度。"""

    total_qty = abs(float(order_row.qty))
    local_filled_qty = _local_filled_qty(session, order_row.id)
    if local_filled_qty > total_qty + 1e-8:
        raise RuntimeError(
            "FILL_QUANTITY_EXCEEDS_ORDER: "
            f"order_id={order_row.id}，ordered={total_qty}，"
            f"local_filled={local_filled_qty}。"
        )
    persisted_filled_qty = max(float(order_row.filled_qty or 0.0), 0.0)
    cumulative_filled_qty = max(persisted_filled_qty, local_filled_qty)
    if cumulative_filled_qty > total_qty + 1e-8:
        raise RuntimeError(
            "FILL_QUANTITY_EXCEEDS_ORDER: "
            f"order_id={order_row.id}，ordered={total_qty}，"
            f"cumulative_filled={cumulative_filled_qty}。"
        )
    derived_remaining_qty = max(total_qty - cumulative_filled_qty, 0.0)
    if order_row.remaining_qty is None:
        cumulative_remaining_qty = derived_remaining_qty
    else:
        cumulative_remaining_qty = min(
            max(float(order_row.remaining_qty), 0.0),
            derived_remaining_qty,
        )
    if (
        is_filled_order_status(order_row.status)
        and (
            cumulative_filled_qty < total_qty - 1e-8
            or cumulative_remaining_qty > 1e-8
        )
    ):
        raise RuntimeError(
            "ORDER_FILLED_PROGRESS_INCONSISTENT: 本地 Filled 终态与累计成交"
            f"不一致，order_id={order_row.id}。"
        )

    order_row.filled_qty = cumulative_filled_qty
    order_row.remaining_qty = cumulative_remaining_qty
    if not is_final_order_status(order_row.status):
        order_row.status = (
            "Filled"
            if cumulative_filled_qty >= total_qty - 1e-8
            else "PartiallyFilled"
        )


def save_fill_snapshots(
    session: Session,
    fills: list[FillSnapshot],
    *,
    broker: str | None = None,
    commit: bool = True,
) -> int:
    """批量保存成交快照。

    真实券商优先使用 ``broker + account + exec_id`` 去重；缺少稳定 execution
    identity 的旧数据和 paper 历史记录才回退到成交字段组合。
    """

    normalized_broker = str(broker or "").strip().lower() or None
    count = 0
    for item in fills:
        account = str(item.account or "").strip() or None
        exec_id = str(item.exec_id or "").strip() or None
        if normalized_broker and account and exec_id:
            identity_conditions = (
                FillRecord.broker == normalized_broker,
                FillRecord.account == account,
                FillRecord.exec_id == exec_id,
            )
        else:
            fallback_conditions: list = [
                FillRecord.broker_order_id == item.broker_order_id,
                FillRecord.symbol == item.symbol,
                FillRecord.qty == item.qty,
                FillRecord.price == item.price,
                FillRecord.filled_at == item.filled_at,
            ]
            if normalized_broker:
                fallback_conditions.append(FillRecord.broker == normalized_broker)
            if account:
                fallback_conditions.append(FillRecord.account == account)
            identity_conditions = tuple(fallback_conditions)

        existing_fill = session.scalar(
            select(FillRecord).where(*identity_conditions)
        )
        order_row = _find_order_for_fill(
            session,
            item,
            broker=normalized_broker,
            account=account,
        )
        if order_row is not None:
            _assert_fill_matches_order(order_row, item)
        if existing_fill is not None:
            if (
                order_row is not None
                and existing_fill.order_id is not None
                and existing_fill.order_id != order_row.id
            ):
                raise RuntimeError(
                    "FILL_ORDER_IDENTITY_MISMATCH: "
                    f"fill_id={existing_fill.id} 已关联 order_id="
                    f"{existing_fill.order_id}，observed_order_id={order_row.id}。"
                )
            if order_row is not None and existing_fill.order_id is None:
                existing_fill.order_id = order_row.id
                session.flush()
                _add_trade_attribution_for_fill(
                    session,
                    fill_row=existing_fill,
                    order_row=order_row,
                )
                _update_order_progress_from_fill_ledger(session, order_row)
                count += 1
            continue

        if order_row is not None:
            existing_filled_qty = _local_filled_qty(session, order_row.id)
            if existing_filled_qty + abs(float(item.qty)) > abs(
                float(order_row.qty)
            ) + 1e-8:
                raise RuntimeError(
                    "FILL_QUANTITY_EXCEEDS_ORDER: "
                    f"order_id={order_row.id}，ordered={order_row.qty}，"
                    f"existing_filled={existing_filled_qty}，"
                    f"incoming={item.qty}。"
                )
        fill_row = FillRecord(
            order_id=order_row.id if order_row is not None else None,
            broker=normalized_broker,
            account=account,
            exec_id=exec_id,
            perm_id=item.perm_id,
            client_id=item.client_id,
            instrument_id=item.instrument_id,
            exchange_id=item.exchange_id,
            ctp_offset=item.ctp_offset,
            broker_order_id=item.broker_order_id,
            symbol=item.symbol,
            side=item.side,
            qty=item.qty,
            price=item.price,
            filled_at=item.filled_at,
        )
        session.add(
            fill_row
        )
        session.flush()
        _add_trade_attribution_for_fill(
            session,
            fill_row=fill_row,
            order_row=order_row,
        )
        if order_row is not None:
            _update_order_progress_from_fill_ledger(session, order_row)
        count += 1
    if commit:
        session.commit()
    else:
        session.flush()
    return count


def _resolve_reference_price(order_row: OrderRecord) -> tuple[float | None, str | None]:
    if order_row.reference_price is not None:
        return float(order_row.reference_price), order_row.reference_price_source
    if order_row.limit_price is not None:
        return float(order_row.limit_price), "order_limit"
    if order_row.planned_trade_value is not None and abs(float(order_row.qty or 0.0)) > 1e-8:
        return (
            abs(float(order_row.planned_trade_value)) / abs(float(order_row.qty)),
            "planned_trade_value",
        )
    return None, None


def _implementation_shortfall(*, side: str | None, qty: float, fill_price: float, reference_price: float) -> float:
    normalized_side = str(side or "").strip().upper()
    if normalized_side == "SELL":
        return (reference_price - fill_price) * abs(float(qty))
    return (fill_price - reference_price) * abs(float(qty))


def _add_trade_attribution_for_fill(
    session: Session,
    *,
    fill_row: FillRecord,
    order_row: OrderRecord | None,
) -> None:
    if order_row is None:
        return

    reference_price, reference_source = _resolve_reference_price(order_row)
    if reference_price is None:
        return

    qty = abs(float(fill_row.qty))
    fill_price = float(fill_row.price)
    reference_notional = qty * abs(float(reference_price))
    actual_notional = qty * abs(fill_price)
    shortfall = _implementation_shortfall(
        side=fill_row.side or order_row.side,
        qty=qty,
        fill_price=fill_price,
        reference_price=float(reference_price),
    )
    session.add(
        TradeAttributionRecord(
            fill_id=fill_row.id,
            order_id=order_row.id,
            broker_order_id=fill_row.broker_order_id,
            run_id=order_row.run_id,
            batch_id=order_row.batch_id,
            plan_id=order_row.plan_id,
            profile_id=order_row.profile_id,
            account=order_row.account,
            strategy_id=order_row.strategy_id,
            execution_planner_id=order_row.execution_planner_id,
            symbol=fill_row.symbol,
            side=fill_row.side or order_row.side,
            qty=qty,
            fill_price=fill_price,
            reference_price=float(reference_price),
            reference_price_source=reference_source,
            actual_notional=actual_notional,
            reference_notional=reference_notional,
            implementation_shortfall=shortfall,
            implementation_shortfall_bps=(
                shortfall / reference_notional * 10000.0
                if reference_notional > 1e-8
                else None
            ),
            order_semantic=order_row.order_semantic,
            reason=order_row.reason,
            attributed_at=ensure_utc(fill_row.filled_at),
        )
    )


def save_working_order_snapshots(
    session: Session,
    broker_rows: Sequence[dict],
    *,
    broker: str,
    run_id: str | None = None,
    profile_id: str | None = None,
    default_account: str | None = None,
    observed_at: datetime | None = None,
    commit: bool = True,
) -> dict[str, object]:
    """保存挂单快照账本。"""

    if not broker_rows:
        return {"count": 0, "snapshot_batch_id": None}

    snapshot_batch_id = f"working-order-batch-{uuid4().hex[:12]}"
    observed_time = ensure_utc(observed_at)
    count = 0
    for row in broker_rows:
        broker_order_id = _optional_text(row.get("broker_order_id"))
        symbol = _optional_text(row.get("symbol"))
        if broker_order_id is None or symbol is None:
            continue
        session.add(
            WorkingOrderSnapshotRecord(
                run_id=run_id,
                profile_id=profile_id,
                broker=broker,
                account=_optional_text(row.get("account")) or default_account,
                open_order_snapshot_batch_id=snapshot_batch_id,
                broker_order_id=broker_order_id,
                symbol=symbol.upper(),
                side=_optional_text(row.get("side")),
                qty=float(row.get("qty", 0.0) or 0.0),
                filled_qty=_optional_float(row.get("filled_qty")),
                remaining_qty=_optional_float(row.get("remaining_qty")),
                avg_fill_price=_optional_float(row.get("avg_fill_price")),
                status=_optional_text(row.get("status")) or "open",
                order_type=_optional_text(row.get("order_type")),
                limit_price=_optional_float(row.get("limit_price")),
                submitted_at=_coerce_snapshot_time(row.get("submitted_at")),
                observed_at=observed_time,
            )
        )
        count += 1

    if commit:
        session.commit()
    else:
        session.flush()
    return {
        "count": count,
        "snapshot_batch_id": snapshot_batch_id if count > 0 else None,
    }


def save_account_snapshot(
    session: Session,
    *,
    broker: str,
    snapshot: BrokerStateSnapshot,
    run_id: str | None = None,
    profile_id: str | None = None,
    position_snapshot_batch_id: str | None = None,
    commit: bool = True,
) -> AccountSnapshotRecord:
    """保存账户账本快照。"""

    account_values = snapshot.account_values or {}
    account = (
        _optional_text(snapshot.account)
        or _optional_text(account_values.get("Account"))
        or next((item.account for item in snapshot.positions if item.account), None)
    )
    batch_id = position_snapshot_batch_id or next(
        (item.snapshot_batch_id for item in snapshot.positions if item.snapshot_batch_id),
        None,
    )

    net_position_value = 0.0
    gross_position_value = 0.0
    position_count = 0
    for position in snapshot.positions:
        qty = float(position.qty)
        if abs(qty) <= 1e-8:
            continue
        position_count += 1
        market_value = position.market_value
        if market_value is None and position.market_price is not None:
            market_value = qty * float(position.market_price)
        market_value = float(market_value or 0.0)
        net_position_value += market_value
        gross_position_value += abs(market_value)

    net_liquidation = _first_float(
        account_values.get("NetLiquidation"),
        account_values.get("EquityWithLoanValue"),
    )
    reported_gross_position_value = _optional_float(account_values.get("GrossPositionValue"))
    if reported_gross_position_value is not None:
        gross_position_value = reported_gross_position_value
    cash_balance = _first_float(
        account_values.get("CashBalance"),
        account_values.get("TotalCashValue"),
    )
    available_funds = _optional_float(account_values.get("AvailableFunds"))
    if net_liquidation is None or net_liquidation == 0.0:
        gross_exposure = None
        net_exposure = None
    else:
        gross_exposure = gross_position_value / net_liquidation
        net_exposure = net_position_value / net_liquidation

    row = AccountSnapshotRecord(
        run_id=run_id,
        profile_id=profile_id,
        broker=broker,
        account=account,
        position_snapshot_batch_id=batch_id,
        position_count=position_count,
        cash_balance=cash_balance,
        net_liquidation=net_liquidation,
        gross_position_value=gross_position_value,
        net_position_value=net_position_value,
        available_funds=available_funds,
        gross_exposure=gross_exposure,
        net_exposure=net_exposure,
        realized_pnl=_optional_float(account_values.get("RealizedPnL")),
        unrealized_pnl=_optional_float(account_values.get("UnrealizedPnL")),
        account_values_json=_serialize_json(account_values),
        asof=ensure_utc(snapshot.asof),
    )
    session.add(row)
    if commit:
        session.commit()
    else:
        session.flush()
    return row


def _position_rows_by_batch(
    session: Session,
    batch_id: str | None,
) -> dict[str, PositionSnapshotRecord]:
    if batch_id is None:
        return {}
    rows = list(
        session.scalars(
            select(PositionSnapshotRecord).where(
                PositionSnapshotRecord.snapshot_batch_id == batch_id
            )
        )
    )
    return {row.symbol: row for row in rows}


def _position_price(row: PositionSnapshotRecord) -> float | None:
    if row.market_price is not None:
        return float(row.market_price)
    if row.market_value is not None and abs(float(row.qty or 0.0)) > 1e-8:
        return float(row.market_value) / float(row.qty)
    return None


def _signed_trade_qty(side: str | None, qty: float) -> float:
    normalized_side = str(side or "").strip().upper()
    if normalized_side == "SELL":
        return -abs(float(qty))
    return abs(float(qty))


def _account_snapshot_scope_clause(
    stmt,
    *,
    profile_id: str | None,
    account: str | None,
):
    if profile_id is not None:
        stmt = stmt.where(AccountSnapshotRecord.profile_id == profile_id)
    if account is not None:
        stmt = stmt.where(AccountSnapshotRecord.account == account)
    return stmt


def _trade_attribution_scope_clause(
    stmt,
    *,
    profile_id: str | None,
    account: str | None,
):
    if profile_id is not None:
        stmt = stmt.where(TradeAttributionRecord.profile_id == profile_id)
    if account is not None:
        stmt = stmt.where(TradeAttributionRecord.account == account)
    return stmt


def save_account_attribution_for_snapshot(
    session: Session,
    ending_snapshot: AccountSnapshotRecord,
    *,
    commit: bool = True,
) -> AccountAttributionRecord | None:
    """基于相邻账户快照生成区间收益归因。"""

    if ending_snapshot.id is None:
        return None

    exists = session.scalar(
        select(AccountAttributionRecord).where(
            AccountAttributionRecord.end_account_snapshot_id == ending_snapshot.id
        )
    )
    if exists is not None:
        return exists

    stmt = select(AccountSnapshotRecord).where(
        AccountSnapshotRecord.broker == ending_snapshot.broker,
    )
    stmt = _account_snapshot_scope_clause(
        stmt,
        profile_id=ending_snapshot.profile_id,
        account=ending_snapshot.account,
    )
    previous_snapshot = session.scalar(
        stmt.where(AccountSnapshotRecord.id != ending_snapshot.id)
        .where(AccountSnapshotRecord.asof <= ending_snapshot.asof)
        .order_by(AccountSnapshotRecord.asof.desc(), AccountSnapshotRecord.id.desc())
        .limit(1)
    )
    if previous_snapshot is None:
        return None
    if previous_snapshot.id == ending_snapshot.id:
        return None

    start_positions = _position_rows_by_batch(
        session,
        previous_snapshot.position_snapshot_batch_id,
    )
    end_positions = _position_rows_by_batch(
        session,
        ending_snapshot.position_snapshot_batch_id,
    )

    trades_stmt = select(TradeAttributionRecord).where(
        TradeAttributionRecord.attributed_at > previous_snapshot.asof,
        TradeAttributionRecord.attributed_at <= ending_snapshot.asof,
    )
    trades_stmt = _trade_attribution_scope_clause(
        trades_stmt,
        profile_id=ending_snapshot.profile_id,
        account=ending_snapshot.account,
    )
    interval_trades = list(
        session.scalars(
            trades_stmt.order_by(
                TradeAttributionRecord.attributed_at.asc(),
                TradeAttributionRecord.id.asc(),
            )
        )
    )

    end_price_by_symbol: dict[str, float] = {}
    for symbol, position in end_positions.items():
        closing_price = _position_price(position)
        if closing_price is not None:
            end_price_by_symbol[symbol] = closing_price
    for trade in interval_trades:
        end_price_by_symbol.setdefault(trade.symbol, float(trade.fill_price))

    price_pnl = 0.0
    for symbol, row in start_positions.items():
        start_price = _position_price(row)
        if start_price is None:
            continue
        end_price = end_price_by_symbol.get(symbol, start_price)
        price_pnl += float(row.qty) * (float(end_price) - float(start_price))

    rebalance_pnl = 0.0
    execution_shortfall = 0.0
    traded_notional = 0.0
    for trade in interval_trades:
        signed_qty = _signed_trade_qty(trade.side, float(trade.qty))
        end_price = end_price_by_symbol.get(trade.symbol, float(trade.fill_price))
        rebalance_pnl += signed_qty * (float(end_price) - float(trade.fill_price))
        execution_shortfall += float(trade.implementation_shortfall)
        traded_notional += float(trade.actual_notional)

    starting_equity = _optional_float(previous_snapshot.net_liquidation)
    ending_equity = _optional_float(ending_snapshot.net_liquidation)
    equity_change = None
    if starting_equity is not None and ending_equity is not None:
        equity_change = ending_equity - starting_equity

    starting_cash = _optional_float(previous_snapshot.cash_balance)
    ending_cash = _optional_float(ending_snapshot.cash_balance)
    cash_change = None
    if starting_cash is not None and ending_cash is not None:
        cash_change = ending_cash - starting_cash

    non_trade_components = _non_trade_cash_flow_components(
        _deserialize_json_dict(previous_snapshot.account_values_json),
        _deserialize_json_dict(ending_snapshot.account_values_json),
    )
    total_non_trade_cash_flow = sum(non_trade_components.values())

    residual_pnl = None
    if equity_change is not None:
        residual_pnl = equity_change - price_pnl - rebalance_pnl - total_non_trade_cash_flow

    attribution_row = AccountAttributionRecord(
        start_account_snapshot_id=previous_snapshot.id,
        end_account_snapshot_id=ending_snapshot.id,
        run_id=ending_snapshot.run_id,
        profile_id=ending_snapshot.profile_id,
        broker=ending_snapshot.broker,
        account=ending_snapshot.account,
        start_position_snapshot_batch_id=previous_snapshot.position_snapshot_batch_id,
        end_position_snapshot_batch_id=ending_snapshot.position_snapshot_batch_id,
        start_asof=previous_snapshot.asof,
        end_asof=ending_snapshot.asof,
        starting_equity=starting_equity,
        ending_equity=ending_equity,
        equity_change=equity_change,
        starting_cash=starting_cash,
        ending_cash=ending_cash,
        cash_change=cash_change,
        price_pnl=price_pnl,
        rebalance_pnl=rebalance_pnl,
        execution_shortfall=execution_shortfall,
        interest_cash_flow=non_trade_components["interest"],
        fee_cash_flow=non_trade_components["fee"],
        tax_cash_flow=non_trade_components["tax"],
        funding_cash_flow=non_trade_components["funding"],
        other_non_trade_cash_flow=non_trade_components["other"],
        total_non_trade_cash_flow=total_non_trade_cash_flow,
        traded_notional=traded_notional,
        fill_count=len(interval_trades),
        residual_pnl=residual_pnl,
    )
    session.add(attribution_row)
    if commit:
        session.commit()
        session.refresh(attribution_row)
    else:
        session.flush()
    return attribution_row


def _required_ledger_text(value: object, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"账本记录缺少 {field_name}")
    return normalized


def _finite_ledger_amount(value: float | None, *, field_name: str) -> float | None:
    if value is None:
        return None
    amount = float(value)
    if not math.isfinite(amount):
        raise ValueError(f"账本记录 {field_name} 必须是有限数值")
    return amount


def save_settlement_record(
    session: Session,
    *,
    settlement_id: str,
    settlement_date: date,
    broker: str,
    account: str,
    profile_id: str | None,
    account_snapshot_id: int | None,
    cash_balance: float | None,
    margin: float | None,
    realized_pnl: float | None,
    unrealized_pnl: float | None,
    fee: float | None,
    currency: str,
    evidence: dict[str, object],
    settled_at: datetime,
) -> SettlementRecord:
    """追加结算事实；同一券商/账户/结算身份只能幂等重放。"""

    normalized_broker = _required_ledger_text(broker, field_name="broker").lower()
    normalized_account = _required_ledger_text(account, field_name="account")
    normalized_id = _required_ledger_text(settlement_id, field_name="settlement_id")
    normalized_currency = _required_ledger_text(currency, field_name="currency").upper()
    if not isinstance(settlement_date, date) or isinstance(settlement_date, datetime):
        raise ValueError("账本结算日期必须明确")
    if settled_at.tzinfo is None:
        raise ValueError("账本结算时间必须带时区")
    if not evidence:
        raise ValueError("账本结算必须包含券商证据")
    values = {
        "cash_balance": _finite_ledger_amount(cash_balance, field_name="cash_balance"),
        "margin": _finite_ledger_amount(margin, field_name="margin"),
        "realized_pnl": _finite_ledger_amount(realized_pnl, field_name="realized_pnl"),
        "unrealized_pnl": _finite_ledger_amount(unrealized_pnl, field_name="unrealized_pnl"),
        "fee": _finite_ledger_amount(fee, field_name="fee"),
    }
    serialized_evidence = _serialize_json(evidence) or "{}"
    normalized_profile = _optional_text(profile_id)
    existing = session.scalar(
        select(SettlementRecord).where(
            SettlementRecord.broker == normalized_broker,
            SettlementRecord.account == normalized_account,
            SettlementRecord.settlement_id == normalized_id,
        )
    )
    if existing is not None:
        persisted = (
            existing.settlement_date,
            existing.profile_id,
            existing.account_snapshot_id,
            existing.cash_balance,
            existing.margin,
            existing.realized_pnl,
            existing.unrealized_pnl,
            existing.fee,
            existing.currency,
            existing.evidence_json,
            existing.settled_at,
        )
        incoming = (
            settlement_date,
            normalized_profile,
            account_snapshot_id,
            values["cash_balance"],
            values["margin"],
            values["realized_pnl"],
            values["unrealized_pnl"],
            values["fee"],
            normalized_currency,
            serialized_evidence,
            ensure_utc(settled_at),
        )
        if persisted != incoming:
            raise RuntimeError("SETTLEMENT_IDENTITY_MISMATCH: 同一结算身份内容不一致。")
        return existing
    row = SettlementRecord(
        settlement_id=normalized_id,
        settlement_date=settlement_date,
        broker=normalized_broker,
        account=normalized_account,
        profile_id=normalized_profile,
        account_snapshot_id=account_snapshot_id,
        currency=normalized_currency,
        evidence_json=serialized_evidence,
        settled_at=ensure_utc(settled_at),
        **values,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def record_controlled_ledger_adjustment(
    session: Session,
    *,
    adjustment_id: str,
    broker: str,
    account: str,
    profile_id: str | None,
    amount: float,
    currency: str,
    reason: str,
    approver_id: str,
    evidence: dict[str, object],
    occurred_at: datetime,
) -> LedgerAdjustmentRecord:
    """追加具名审批的调整；绝不修改既有订单、成交、结算或快照。"""

    normalized_id = _required_ledger_text(adjustment_id, field_name="adjustment_id")
    normalized_broker = _required_ledger_text(broker, field_name="broker").lower()
    normalized_account = _required_ledger_text(account, field_name="account")
    normalized_currency = _required_ledger_text(currency, field_name="currency").upper()
    normalized_reason = _required_ledger_text(reason, field_name="reason")
    normalized_approver = _required_ledger_text(approver_id, field_name="approver_id")
    if not evidence:
        raise ValueError("账本调整必须包含审批证据")
    normalized_amount = _finite_ledger_amount(amount, field_name="amount")
    assert normalized_amount is not None
    serialized_evidence = _serialize_json(evidence) or "{}"
    if occurred_at.tzinfo is None:
        raise ValueError("账本调整时间必须带时区")
    normalized_profile = _optional_text(profile_id)
    existing = session.scalar(
        select(LedgerAdjustmentRecord).where(
            LedgerAdjustmentRecord.adjustment_id == normalized_id
        )
    )
    if existing is not None:
        persisted = (
            existing.broker, existing.account, existing.profile_id, existing.amount,
            existing.currency, existing.reason, existing.approver_id,
            existing.evidence_json, existing.occurred_at,
        )
        incoming = (
            normalized_broker, normalized_account, normalized_profile, normalized_amount,
            normalized_currency, normalized_reason, normalized_approver,
            serialized_evidence, ensure_utc(occurred_at),
        )
        if persisted != incoming:
            raise RuntimeError("LEDGER_ADJUSTMENT_IDENTITY_MISMATCH: 调整身份内容不一致。")
        return existing
    row = LedgerAdjustmentRecord(
        adjustment_id=normalized_id,
        broker=normalized_broker,
        account=normalized_account,
        profile_id=normalized_profile,
        amount=normalized_amount,
        currency=normalized_currency,
        reason=normalized_reason,
        approver_id=normalized_approver,
        evidence_json=serialized_evidence,
        occurred_at=ensure_utc(occurred_at),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def add_cancel_record(
    session: Session,
    *,
    order: OrderRecord,
    broker: str,
    cancel_batch_id: str,
    reason: str,
    requested_at: datetime | None = None,
    status: str = "Canceled",
) -> None:
    """追加一条撤单记录。"""

    session.add(
        CancelRecord(
            cancel_batch_id=cancel_batch_id,
            order_id=order.id,
            broker=broker,
            broker_order_id=order.broker_order_id,
            run_id=order.run_id,
            profile_id=order.profile_id,
            account=order.account,
            reason=reason,
            status=status,
            requested_at=ensure_utc(requested_at),
        )
    )


def prepare_order_cancel(
    session: Session,
    *,
    broker: str,
    account: str,
    broker_order_id: str,
    reason: str,
    cancel_batch_id: str | None = None,
    local_order_id: int | None = None,
    client_id: int | None = None,
) -> tuple[CancelRecord, bool]:
    """在调用券商撤单前持久化撤单意图。"""

    normalized_broker = str(broker or "").strip().lower()
    normalized_account = str(account or "").strip()
    normalized_order_id = str(broker_order_id or "").strip()
    if not normalized_broker or not normalized_account or not normalized_order_id:
        raise ValueError("持久化撤单意图前必须提供 broker/account/order ID。")

    order_conditions = [
        OrderRecord.broker == normalized_broker,
        OrderRecord.account == normalized_account,
        OrderRecord.broker_order_id == normalized_order_id,
    ]
    if local_order_id is not None:
        order_conditions.append(OrderRecord.id == int(local_order_id))
    if client_id is not None:
        order_conditions.append(OrderRecord.client_id == int(client_id))
    order_candidates = list(
        session.scalars(
            select(OrderRecord)
            .where(*order_conditions)
            .order_by(OrderRecord.id.desc())
            .limit(2)
        )
    )
    if len(order_candidates) > 1:
        raise RuntimeError(
            "CANCEL_ORDER_IDENTITY_AMBIGUOUS: broker/account/clientId/orderId "
            "对应多条本地订单，禁止撤单。"
        )
    order = order_candidates[0] if order_candidates else None
    if order is None:
        raise RuntimeError(
            "CANCEL_ORDER_NOT_PERSISTED: 本地没有匹配订单，禁止执行无审计撤单。"
        )
    if is_final_order_status(order.status):
        raise RuntimeError(
            "CANCEL_NOT_REQUIRED_ORDER_FINAL: 本地订单已经是券商确认终态，"
            "不会创建新的撤单意图。"
        )

    existing = session.scalar(
        select(CancelRecord)
        .where(
            CancelRecord.order_id == order.id,
            CancelRecord.broker == normalized_broker,
            CancelRecord.account == normalized_account,
            CancelRecord.broker_order_id == normalized_order_id,
            func.lower(CancelRecord.status).in_(
                ("cancelprepared", "pendingcancel", "cancelrequestfailed")
            ),
        )
        .order_by(CancelRecord.id.desc())
        .limit(1)
    )
    if existing is not None:
        return existing, False

    row = CancelRecord(
        cancel_batch_id=cancel_batch_id or f"cancel-{uuid4().hex[:16]}",
        order_id=order.id,
        broker=normalized_broker,
        broker_order_id=normalized_order_id,
        run_id=order.run_id,
        profile_id=order.profile_id,
        account=normalized_account,
        reason=str(reason or "broker_cancel_request"),
        status="CancelPrepared",
        requested_at=utc_now(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row, True


def finalize_order_cancel_request(
    session: Session,
    *,
    cancel_id: int,
    accepted: bool,
) -> None:
    """保存券商是否接受撤单请求；接受不代表已经进入撤单终态。"""

    target_status = "PendingCancel" if accepted else "CancelRequestFailed"
    result = session.execute(
        update(CancelRecord)
        .where(
            CancelRecord.id == int(cancel_id),
            func.lower(CancelRecord.status) == "cancelprepared",
        )
        .values(status=target_status)
    )
    if _affected_rows(result) != 1:
        session.rollback()
        session.expire_all()
        current = session.get(CancelRecord, int(cancel_id))
        if current is None:
            raise RuntimeError("撤单请求记录不存在，无法保存券商响应。")
        if (
            is_final_order_status(current.status)
            or current.status in {"PendingCancel", "CancelRequestFailed"}
        ):
            # completed-order 对账或先前响应已经推进状态，晚到的撤单响应不能
            # 把终态降级。
            return
        raise RuntimeError(
            "撤单请求状态已变化，已拒绝覆盖现有状态："
            f"cancel_id={cancel_id}，status={current.status}。"
        )

    cancel_row = session.get(CancelRecord, int(cancel_id))
    if (
        cancel_row is not None
        and cancel_row.order_id is not None
        and accepted
    ):
        session.execute(
            update(OrderRecord)
            .where(
                OrderRecord.id == cancel_row.order_id,
                ~func.lower(OrderRecord.status).in_(
                    tuple(FINAL_ORDER_STATUSES)
                ),
            )
            .values(status="PendingCancel", updated_at=utc_now())
        )
    session.commit()


def save_order_result(
    session: Session,
    order: OrderRequest,
    result: OrderResult,
    *,
    broker: str | None = None,
) -> OrderRecord:
    """保存订单记录。"""

    planned_trade_value = order.planned_trade_value
    if planned_trade_value is None:
        reference_price = order.reference_price or order.limit_price
        if reference_price is not None:
            planned_trade_value = abs(float(order.qty)) * float(reference_price)

    row = OrderRecord(
        profile_id=order.profile_id,
        strategy_id=order.strategy_id,
        symbol=order.symbol,
        side=order.side,
        qty=order.qty,
        target_weight=order.target_weight,
        order_type=order.order_type,
        limit_price=order.limit_price,
        order_semantic=order.order_semantic,
        reason=order.reason,
        broker=broker,
        account=order.account,
        instrument_id=order.instrument_id,
        exchange_id=order.exchange_id,
        ctp_offset=order.ctp_offset,
        volume_multiple=order.volume_multiple,
        margin_rate=order.margin_rate,
        required_margin=order.required_margin,
        currency=order.currency,
        reference_price=order.reference_price,
        reference_price_source=order.reference_price_source,
        planned_trade_value=planned_trade_value,
        execution_planner_id=order.execution_planner_id,
        run_id=order.run_id,
        batch_id=order.batch_id,
        plan_id=order.plan_id,
        attempt_no=int(order.attempt_no),
        execution_policy_fingerprint=order.execution_policy_fingerprint,
        order_ref=(
            build_order_ref(order.plan_id, order.attempt_no)
            if order.plan_id
            else None
        ),
        broker_order_id=result.broker_order_id,
        client_id=result.client_id,
        perm_id=result.perm_id,
        status=result.status,
        prepared_at=ensure_utc(result.submitted_at),
        submitted_at=ensure_utc(result.submitted_at),
        broker_acknowledged_at=ensure_utc(result.submitted_at),
        updated_at=ensure_utc(result.submitted_at),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def record_execution_provenance_consumption(
    session: Session,
    *,
    preflight_id: str,
    receipt_hash: str,
    plan_hash: str,
    order_hash: str,
    profile_id: str,
    broker: str,
    account: str,
    order_ref: str,
    checked_at: datetime,
    valid_until: datetime,
    consumed_at: datetime,
) -> ExecutionProvenanceConsumptionRecord:
    """Stage one exact CTP-sim commitment consumption in the current transaction.

    The caller must invoke this only after replaying the evidence at the final
    submit boundary.  It intentionally does not commit: ``prepare_order_submission``
    commits this fact atomically with the durable broker intent.
    """

    normalized_preflight_id = _required_execution_provenance_text(
        preflight_id,
        field_name="preflight_id",
    )
    normalized_receipt_hash = _required_execution_provenance_text(
        receipt_hash,
        field_name="receipt_hash",
        sha256=True,
    )
    normalized_plan_hash = _required_execution_provenance_text(
        plan_hash,
        field_name="plan_hash",
        sha256=True,
    )
    normalized_order_hash = _required_execution_provenance_text(
        order_hash,
        field_name="order_hash",
        sha256=True,
    )
    normalized_profile_id = _required_execution_provenance_text(
        profile_id,
        field_name="profile_id",
    )
    normalized_broker = _required_execution_provenance_text(
        broker,
        field_name="broker",
    ).lower()
    normalized_account = _required_execution_provenance_text(
        account,
        field_name="account",
    )
    normalized_order_ref = _required_execution_provenance_text(
        order_ref,
        field_name="order_ref",
    )
    normalized_checked_at = ensure_utc(checked_at)
    normalized_valid_until = ensure_utc(valid_until)
    normalized_consumed_at = ensure_utc(consumed_at)
    if normalized_valid_until <= normalized_checked_at:
        raise ValueError("execution provenance receipt validity window is invalid")
    if not normalized_checked_at <= normalized_consumed_at < normalized_valid_until:
        raise PermissionError("EXECUTION_PROVENANCE_RECEIPT_EXPIRED")

    existing = session.scalar(
        select(ExecutionProvenanceConsumptionRecord).where(
            ExecutionProvenanceConsumptionRecord.broker == normalized_broker,
            ExecutionProvenanceConsumptionRecord.account == normalized_account,
            ExecutionProvenanceConsumptionRecord.plan_hash == normalized_plan_hash,
            ExecutionProvenanceConsumptionRecord.order_hash == normalized_order_hash,
        )
    )
    if existing is not None:
        raise PermissionError(
            "EXECUTION_PROVENANCE_ORDER_ALREADY_CONSUMED: "
            "the exact CTP-sim plan/order commitment was already reserved"
        )

    row = ExecutionProvenanceConsumptionRecord(
        preflight_id=normalized_preflight_id,
        receipt_hash=normalized_receipt_hash,
        plan_hash=normalized_plan_hash,
        order_hash=normalized_order_hash,
        profile_id=normalized_profile_id,
        broker=normalized_broker,
        account=normalized_account,
        order_ref=normalized_order_ref,
        checked_at=normalized_checked_at,
        valid_until=normalized_valid_until,
        consumed_at=normalized_consumed_at,
    )
    session.add(row)
    return row


def find_execution_provenance_consumption(
    session: Session,
    *,
    broker: str,
    account: str,
    plan_hash: str | None = None,
    order_hash: str | None = None,
    order_ref: str | None = None,
) -> ExecutionProvenanceConsumptionRecord | None:
    """Find one append-only P8 CTP-sim commitment consumption.

    This lookup deliberately never creates a fact or falls back to an order row.
    Reconciliation may use ``order_ref`` to prove that an observed broker order was
    submitted through the candidate gate; the final gate uses the hash pair as the
    stronger exact binding.
    """

    normalized_broker = _required_execution_provenance_text(
        broker,
        field_name="broker",
    ).lower()
    normalized_account = _required_execution_provenance_text(
        account,
        field_name="account",
    )
    conditions = [
        ExecutionProvenanceConsumptionRecord.broker == normalized_broker,
        ExecutionProvenanceConsumptionRecord.account == normalized_account,
    ]
    if plan_hash is not None:
        conditions.append(
            ExecutionProvenanceConsumptionRecord.plan_hash
            == _required_execution_provenance_text(
                plan_hash,
                field_name="plan_hash",
                sha256=True,
            )
        )
    if order_hash is not None:
        conditions.append(
            ExecutionProvenanceConsumptionRecord.order_hash
            == _required_execution_provenance_text(
                order_hash,
                field_name="order_hash",
                sha256=True,
            )
        )
    if order_ref is not None:
        conditions.append(
            ExecutionProvenanceConsumptionRecord.order_ref
            == _required_execution_provenance_text(
                order_ref,
                field_name="order_ref",
            )
        )
    rows = list(
        session.scalars(
            select(ExecutionProvenanceConsumptionRecord)
            .where(*conditions)
            .order_by(ExecutionProvenanceConsumptionRecord.id.asc())
            .limit(2)
        )
    )
    if len(rows) > 1:
        raise RuntimeError(
            "EXECUTION_PROVENANCE_CONSUMPTION_AMBIGUOUS: "
            "multiple candidate-gate consumptions matched one observed identity"
        )
    return rows[0] if rows else None


_PORTFOLIO_RISK_APPROVAL_IDENTIFIER_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
)
_PORTFOLIO_RISK_APPROVAL_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_PORTFOLIO_RISK_APPROVAL_MAX_TEXT_LENGTH = 2048


def _required_portfolio_risk_approval_text(
    value: object,
    *,
    field_name: str,
    sha256: bool = False,
    identifier: bool = False,
) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\x00" in value
        or "\r" in value
        or "\n" in value
        or len(value) > _PORTFOLIO_RISK_APPROVAL_MAX_TEXT_LENGTH
    ):
        raise ValueError(f"{field_name} must be non-empty single-line text")
    normalized = value.strip()
    if sha256 and _PORTFOLIO_RISK_APPROVAL_HASH_RE.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256")
    if identifier and _PORTFOLIO_RISK_APPROVAL_IDENTIFIER_RE.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be a stable identifier")
    return normalized


def _required_portfolio_risk_approval_time(
    value: object,
    *,
    field_name: str,
) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class _PortfolioRiskApprovalRecordValues:
    """Normalized append-only content used for record hashing and equality."""

    approval_id: str
    profile_id: str
    broker: str
    account: str
    review_hash: str
    evidence_hash: str
    portfolio_target_hash: str
    approved_target_hash: str
    composition_hash: str
    composition_evidence_hash: str
    authority_hash: str
    policy_hash: str
    reconciliation_state_hash: str
    binding_hash: str
    attestation_hash: str
    approver_id: str
    verifier_id: str
    verifier_receipt_hash: str
    rationale: str
    review_evaluated_at: datetime
    approved_at: datetime
    verified_at: datetime
    valid_until: datetime
    issued_at: datetime

    def as_hash_payload(self) -> dict[str, object]:
        return {
            "format": "northstar.portfolio-risk-manual-approval-record.v1",
            "approval_id": self.approval_id,
            "profile_id": self.profile_id,
            "broker": self.broker,
            "account": self.account,
            "review_hash": self.review_hash,
            "evidence_hash": self.evidence_hash,
            "portfolio_target_hash": self.portfolio_target_hash,
            "approved_target_hash": self.approved_target_hash,
            "composition_hash": self.composition_hash,
            "composition_evidence_hash": self.composition_evidence_hash,
            "authority_hash": self.authority_hash,
            "policy_hash": self.policy_hash,
            "reconciliation_state_hash": self.reconciliation_state_hash,
            "binding_hash": self.binding_hash,
            "attestation_hash": self.attestation_hash,
            "approver_id": self.approver_id,
            "verifier_id": self.verifier_id,
            "verifier_receipt_hash": self.verifier_receipt_hash,
            "rationale": self.rationale,
            "review_evaluated_at": self.review_evaluated_at.isoformat(),
            "approved_at": self.approved_at.isoformat(),
            "verified_at": self.verified_at.isoformat(),
            "valid_until": self.valid_until.isoformat(),
            "issued_at": self.issued_at.isoformat(),
        }

    @property
    def record_hash(self) -> str:
        encoded = json.dumps(
            self.as_hash_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


def _portfolio_risk_approval_record_values(
    *,
    approval_id: object,
    profile_id: object,
    broker: object,
    account: object,
    review_hash: object,
    evidence_hash: object,
    portfolio_target_hash: object,
    approved_target_hash: object,
    composition_hash: object,
    composition_evidence_hash: object,
    authority_hash: object,
    policy_hash: object,
    reconciliation_state_hash: object,
    binding_hash: object,
    attestation_hash: object,
    approver_id: object,
    verifier_id: object,
    verifier_receipt_hash: object,
    rationale: object,
    review_evaluated_at: object,
    approved_at: object,
    verified_at: object,
    valid_until: object,
    issued_at: object,
) -> _PortfolioRiskApprovalRecordValues:
    normalized_broker = _required_portfolio_risk_approval_text(
        broker,
        field_name="broker",
        identifier=True,
    ).lower()
    if normalized_broker != "ctp_sim":
        raise PermissionError("PORTFOLIO_RISK_APPROVAL_BROKER_REFUSED")
    values = _PortfolioRiskApprovalRecordValues(
        approval_id=_required_portfolio_risk_approval_text(
            approval_id,
            field_name="approval_id",
            identifier=True,
        ),
        profile_id=_required_portfolio_risk_approval_text(
            profile_id,
            field_name="profile_id",
            identifier=True,
        ),
        broker=normalized_broker,
        account=_required_portfolio_risk_approval_text(
            account,
            field_name="account",
            identifier=True,
        ),
        review_hash=_required_portfolio_risk_approval_text(
            review_hash,
            field_name="review_hash",
            sha256=True,
        ),
        evidence_hash=_required_portfolio_risk_approval_text(
            evidence_hash,
            field_name="evidence_hash",
            sha256=True,
        ),
        portfolio_target_hash=_required_portfolio_risk_approval_text(
            portfolio_target_hash,
            field_name="portfolio_target_hash",
            sha256=True,
        ),
        approved_target_hash=_required_portfolio_risk_approval_text(
            approved_target_hash,
            field_name="approved_target_hash",
            sha256=True,
        ),
        composition_hash=_required_portfolio_risk_approval_text(
            composition_hash,
            field_name="composition_hash",
            sha256=True,
        ),
        composition_evidence_hash=_required_portfolio_risk_approval_text(
            composition_evidence_hash,
            field_name="composition_evidence_hash",
            sha256=True,
        ),
        authority_hash=_required_portfolio_risk_approval_text(
            authority_hash,
            field_name="authority_hash",
            sha256=True,
        ),
        policy_hash=_required_portfolio_risk_approval_text(
            policy_hash,
            field_name="policy_hash",
            sha256=True,
        ),
        reconciliation_state_hash=_required_portfolio_risk_approval_text(
            reconciliation_state_hash,
            field_name="reconciliation_state_hash",
            sha256=True,
        ),
        binding_hash=_required_portfolio_risk_approval_text(
            binding_hash,
            field_name="binding_hash",
            sha256=True,
        ),
        attestation_hash=_required_portfolio_risk_approval_text(
            attestation_hash,
            field_name="attestation_hash",
            sha256=True,
        ),
        approver_id=_required_portfolio_risk_approval_text(
            approver_id,
            field_name="approver_id",
            identifier=True,
        ),
        verifier_id=_required_portfolio_risk_approval_text(
            verifier_id,
            field_name="verifier_id",
            identifier=True,
        ),
        verifier_receipt_hash=_required_portfolio_risk_approval_text(
            verifier_receipt_hash,
            field_name="verifier_receipt_hash",
            sha256=True,
        ),
        rationale=_required_portfolio_risk_approval_text(
            rationale,
            field_name="rationale",
        ),
        review_evaluated_at=_required_portfolio_risk_approval_time(
            review_evaluated_at,
            field_name="review_evaluated_at",
        ),
        approved_at=_required_portfolio_risk_approval_time(
            approved_at,
            field_name="approved_at",
        ),
        verified_at=_required_portfolio_risk_approval_time(
            verified_at,
            field_name="verified_at",
        ),
        valid_until=_required_portfolio_risk_approval_time(
            valid_until,
            field_name="valid_until",
        ),
        issued_at=_required_portfolio_risk_approval_time(
            issued_at,
            field_name="issued_at",
        ),
    )
    if not (
        values.review_evaluated_at
        <= values.approved_at
        <= values.verified_at
        <= values.issued_at
        < values.valid_until
    ):
        raise ValueError("portfolio risk approval time ordering is invalid")
    return values


def _record_values_from_portfolio_risk_approval_record(
    record: PortfolioRiskApprovalRecord,
) -> _PortfolioRiskApprovalRecordValues:
    values = _portfolio_risk_approval_record_values(
        approval_id=record.approval_id,
        profile_id=record.profile_id,
        broker=record.broker,
        account=record.account,
        review_hash=record.review_hash,
        evidence_hash=record.evidence_hash,
        portfolio_target_hash=record.portfolio_target_hash,
        approved_target_hash=record.approved_target_hash,
        composition_hash=record.composition_hash,
        composition_evidence_hash=record.composition_evidence_hash,
        authority_hash=record.authority_hash,
        policy_hash=record.policy_hash,
        reconciliation_state_hash=record.reconciliation_state_hash,
        binding_hash=record.binding_hash,
        attestation_hash=record.attestation_hash,
        approver_id=record.approver_id,
        verifier_id=record.verifier_id,
        verifier_receipt_hash=record.verifier_receipt_hash,
        rationale=record.rationale,
        review_evaluated_at=record.review_evaluated_at,
        approved_at=record.approved_at,
        verified_at=record.verified_at,
        valid_until=record.valid_until,
        issued_at=record.issued_at,
    )
    if record.record_hash != values.record_hash:
        raise RuntimeError("PORTFOLIO_RISK_APPROVAL_RECORD_TAMPERED")
    return values


def _assert_portfolio_risk_approval_record_matches(
    record: PortfolioRiskApprovalRecord,
    expected: _PortfolioRiskApprovalRecordValues,
) -> None:
    actual = _record_values_from_portfolio_risk_approval_record(record)
    if actual != expected:
        raise RuntimeError("PORTFOLIO_RISK_APPROVAL_IDEMPOTENCY_CONFLICT")


def record_portfolio_risk_approval(
    session: Session,
    *,
    approval_id: str,
    profile_id: str,
    broker: str,
    account: str,
    review_hash: str,
    evidence_hash: str,
    portfolio_target_hash: str,
    approved_target_hash: str,
    composition_hash: str,
    composition_evidence_hash: str,
    authority_hash: str,
    policy_hash: str,
    reconciliation_state_hash: str,
    binding_hash: str,
    attestation_hash: str,
    approver_id: str,
    verifier_id: str,
    verifier_receipt_hash: str,
    rationale: str,
    review_evaluated_at: datetime,
    approved_at: datetime,
    verified_at: datetime,
    valid_until: datetime,
    issued_at: datetime,
    commit: bool = True,
) -> PortfolioRiskApprovalRecord:
    """Persist one immutable verifier-backed approval with strict idempotency.

    Only exact repeats of all immutable fields may reuse an approval ID.  The
    content hash and time ordering are checked both before writes and when the
    record is later read, so a tampered ORM object or database row fails closed.
    """

    values = _portfolio_risk_approval_record_values(
        approval_id=approval_id,
        profile_id=profile_id,
        broker=broker,
        account=account,
        review_hash=review_hash,
        evidence_hash=evidence_hash,
        portfolio_target_hash=portfolio_target_hash,
        approved_target_hash=approved_target_hash,
        composition_hash=composition_hash,
        composition_evidence_hash=composition_evidence_hash,
        authority_hash=authority_hash,
        policy_hash=policy_hash,
        reconciliation_state_hash=reconciliation_state_hash,
        binding_hash=binding_hash,
        attestation_hash=attestation_hash,
        approver_id=approver_id,
        verifier_id=verifier_id,
        verifier_receipt_hash=verifier_receipt_hash,
        rationale=rationale,
        review_evaluated_at=review_evaluated_at,
        approved_at=approved_at,
        verified_at=verified_at,
        valid_until=valid_until,
        issued_at=issued_at,
    )
    by_approval_id = session.scalar(
        select(PortfolioRiskApprovalRecord).where(
            PortfolioRiskApprovalRecord.approval_id == values.approval_id
        )
    )
    if by_approval_id is not None:
        _assert_portfolio_risk_approval_record_matches(by_approval_id, values)
        return by_approval_id
    by_scope_binding = session.scalar(
        select(PortfolioRiskApprovalRecord).where(
            PortfolioRiskApprovalRecord.profile_id == values.profile_id,
            PortfolioRiskApprovalRecord.broker == values.broker,
            PortfolioRiskApprovalRecord.account == values.account,
            PortfolioRiskApprovalRecord.binding_hash == values.binding_hash,
        )
    )
    if by_scope_binding is not None:
        _assert_portfolio_risk_approval_record_matches(by_scope_binding, values)
        return by_scope_binding

    row = PortfolioRiskApprovalRecord(
        approval_id=values.approval_id,
        profile_id=values.profile_id,
        broker=values.broker,
        account=values.account,
        review_hash=values.review_hash,
        evidence_hash=values.evidence_hash,
        portfolio_target_hash=values.portfolio_target_hash,
        approved_target_hash=values.approved_target_hash,
        composition_hash=values.composition_hash,
        composition_evidence_hash=values.composition_evidence_hash,
        authority_hash=values.authority_hash,
        policy_hash=values.policy_hash,
        reconciliation_state_hash=values.reconciliation_state_hash,
        binding_hash=values.binding_hash,
        attestation_hash=values.attestation_hash,
        approver_id=values.approver_id,
        verifier_id=values.verifier_id,
        verifier_receipt_hash=values.verifier_receipt_hash,
        rationale=values.rationale,
        review_evaluated_at=values.review_evaluated_at,
        approved_at=values.approved_at,
        verified_at=values.verified_at,
        valid_until=values.valid_until,
        issued_at=values.issued_at,
        record_hash=values.record_hash,
    )
    session.add(row)
    try:
        if commit:
            session.commit()
            session.refresh(row)
        else:
            session.flush()
    except IntegrityError as exc:
        session.rollback()
        existing = session.scalar(
            select(PortfolioRiskApprovalRecord).where(
                PortfolioRiskApprovalRecord.approval_id == values.approval_id
            )
        )
        if existing is None:
            raise RuntimeError("PORTFOLIO_RISK_APPROVAL_WRITE_CONFLICT") from exc
        _assert_portfolio_risk_approval_record_matches(existing, values)
        return existing
    return row


def find_portfolio_risk_approval(
    session: Session,
    *,
    approval_id: str,
    profile_id: str,
    broker: str,
    account: str,
) -> PortfolioRiskApprovalRecord | None:
    """Find one scoped immutable approval and fail closed on any corruption."""

    normalized_approval_id = _required_portfolio_risk_approval_text(
        approval_id,
        field_name="approval_id",
        identifier=True,
    )
    normalized_profile_id = _required_portfolio_risk_approval_text(
        profile_id,
        field_name="profile_id",
        identifier=True,
    )
    normalized_broker = _required_portfolio_risk_approval_text(
        broker,
        field_name="broker",
        identifier=True,
    ).lower()
    if normalized_broker != "ctp_sim":
        raise PermissionError("PORTFOLIO_RISK_APPROVAL_BROKER_REFUSED")
    normalized_account = _required_portfolio_risk_approval_text(
        account,
        field_name="account",
        identifier=True,
    )
    rows = list(
        session.scalars(
            select(PortfolioRiskApprovalRecord)
            .where(
                PortfolioRiskApprovalRecord.approval_id == normalized_approval_id,
                PortfolioRiskApprovalRecord.profile_id == normalized_profile_id,
                PortfolioRiskApprovalRecord.broker == normalized_broker,
                PortfolioRiskApprovalRecord.account == normalized_account,
            )
            .order_by(PortfolioRiskApprovalRecord.id.asc())
            .limit(2)
        )
    )
    if len(rows) > 1:
        raise RuntimeError("PORTFOLIO_RISK_APPROVAL_RECORD_AMBIGUOUS")
    if not rows:
        return None
    _record_values_from_portfolio_risk_approval_record(rows[0])
    return rows[0]


_RESEARCH_AGENT_AUDIT_IDENTIFIER_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
)
_RESEARCH_AGENT_AUDIT_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RESEARCH_AGENT_LIFECYCLE = "RESEARCH_ONLY"
_RESEARCH_AGENT_AUDIT_EVENT_KINDS = frozenset({"ADMITTED", "COMPLETED", "FAILED"})


class ResearchAgentRunAuditError(RuntimeError):
    """Raised when hash-only durable ResearchAgent audit evidence is unsafe."""


def _research_agent_audit_identifier(value: object, *, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or value.strip() != value
        or _RESEARCH_AGENT_AUDIT_IDENTIFIER_RE.fullmatch(value) is None
    ):
        raise ResearchAgentRunAuditError(
            f"RESEARCH_AGENT_RUN_AUDIT_INVALID_{field_name.upper()}"
        )
    return value


def _research_agent_audit_hash(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _RESEARCH_AGENT_AUDIT_SHA256_RE.fullmatch(value) is None:
        raise ResearchAgentRunAuditError(
            f"RESEARCH_AGENT_RUN_AUDIT_INVALID_{field_name.upper()}"
        )
    return value


def _research_agent_audit_optional_hash(
    value: object | None,
    *,
    field_name: str,
) -> str | None:
    if value is None:
        return None
    return _research_agent_audit_hash(value, field_name=field_name)


def _research_agent_audit_time(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ResearchAgentRunAuditError(
            f"RESEARCH_AGENT_RUN_AUDIT_INVALID_{field_name.upper()}"
        )
    return value.astimezone(UTC)


def _research_agent_audit_count(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResearchAgentRunAuditError(
            f"RESEARCH_AGENT_RUN_AUDIT_INVALID_{field_name.upper()}"
        )
    return value


def _research_agent_failure_code(value: object) -> str:
    if not isinstance(value, str) or value not in _RESEARCH_AGENT_FAILURE_CODES:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_INVALID_FAILURE_CODE")
    return value


def _research_agent_trace_tool_name(value: object) -> str:
    if not isinstance(value, str) or value not in _RESEARCH_AGENT_TRACE_TOOL_NAMES:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_INVALID_TOOL_NAME")
    return value


def _research_agent_audit_hash_payload(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ResearchAgentRunTraceInput:
    """One safe, pre-hashed trace entry supplied only to terminal completion.

    The existing in-memory ResearchAgent trace hash is recomputed here rather
    than trusted.  This data contract contains no prompt, query, payload,
    rationale, document, credential, or chain-of-thought field.
    """

    sequence: int
    tool_name: str
    request_hash: str
    response_hash: str
    predecessor_trace_hash: str | None
    trace_hash: str

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_INVALID_TRACE_SEQUENCE")
        tool_name = _research_agent_trace_tool_name(self.tool_name)
        request_hash = _research_agent_audit_hash(
            self.request_hash,
            field_name="trace_request_hash",
        )
        response_hash = _research_agent_audit_hash(
            self.response_hash,
            field_name="trace_response_hash",
        )
        predecessor_trace_hash = _research_agent_audit_optional_hash(
            self.predecessor_trace_hash,
            field_name="predecessor_trace_hash",
        )
        trace_hash = _research_agent_audit_hash(
            self.trace_hash,
            field_name="trace_hash",
        )
        expected_trace_hash = _research_agent_audit_hash_payload(
            {
                "format": "northstar.research-agent-trace.v1",
                "predecessor_trace_hash": predecessor_trace_hash,
                "request_hash": request_hash,
                "response_hash": response_hash,
                "sequence": self.sequence,
                "tool_name": tool_name,
            }
        )
        if trace_hash != expected_trace_hash:
            raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_TRACE_HASH_MISMATCH")
        object.__setattr__(self, "tool_name", tool_name)
        object.__setattr__(self, "request_hash", request_hash)
        object.__setattr__(self, "response_hash", response_hash)
        object.__setattr__(self, "predecessor_trace_hash", predecessor_trace_hash)
        object.__setattr__(self, "trace_hash", trace_hash)


@dataclass(frozen=True, slots=True)
class _ResearchAgentRunAuditEventValues:
    run_id: str
    event_kind: str
    is_terminal: bool
    request_hash: str
    result_hash: str | None
    failure_code: str | None
    trace_count: int
    trace_root_hash: str | None
    trace_tail_hash: str | None
    as_of: datetime
    occurred_at: datetime
    predecessor_record_hash: str | None
    lifecycle: str
    eligible_for_trading: bool

    def as_hash_payload(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "eligible_for_trading": self.eligible_for_trading,
            "event_kind": self.event_kind,
            "failure_code": self.failure_code,
            "format": "northstar.research-agent-run-audit-event.v1",
            "is_terminal": self.is_terminal,
            "lifecycle": self.lifecycle,
            "occurred_at": self.occurred_at.isoformat(),
            "predecessor_record_hash": self.predecessor_record_hash,
            "request_hash": self.request_hash,
            "result_hash": self.result_hash,
            "run_id": self.run_id,
            "trace_count": self.trace_count,
            "trace_root_hash": self.trace_root_hash,
            "trace_tail_hash": self.trace_tail_hash,
        }

    @property
    def record_hash(self) -> str:
        return _research_agent_audit_hash_payload(self.as_hash_payload())


@dataclass(frozen=True, slots=True)
class _ResearchAgentRunTraceValues:
    run_id: str
    sequence: int
    tool_name: str
    request_hash: str
    response_hash: str
    predecessor_trace_hash: str | None
    trace_hash: str
    recorded_at: datetime
    lifecycle: str
    eligible_for_trading: bool

    def as_hash_payload(self) -> dict[str, object]:
        return {
            "eligible_for_trading": self.eligible_for_trading,
            "format": "northstar.research-agent-run-trace-entry.v1",
            "lifecycle": self.lifecycle,
            "predecessor_trace_hash": self.predecessor_trace_hash,
            "recorded_at": self.recorded_at.isoformat(),
            "request_hash": self.request_hash,
            "response_hash": self.response_hash,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "tool_name": self.tool_name,
            "trace_hash": self.trace_hash,
        }

    @property
    def record_hash(self) -> str:
        return _research_agent_audit_hash_payload(self.as_hash_payload())


def _research_agent_run_audit_event_values(
    *,
    run_id: object,
    event_kind: object,
    is_terminal: object,
    request_hash: object,
    result_hash: object | None,
    failure_code: object | None,
    trace_count: object,
    trace_root_hash: object | None,
    trace_tail_hash: object | None,
    as_of: object,
    occurred_at: object,
    predecessor_record_hash: object | None,
    lifecycle: object,
    eligible_for_trading: object,
) -> _ResearchAgentRunAuditEventValues:
    normalized_event_kind = str(event_kind) if isinstance(event_kind, str) else ""
    if normalized_event_kind not in _RESEARCH_AGENT_AUDIT_EVENT_KINDS:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_INVALID_EVENT_KIND")
    if type(is_terminal) is not bool or type(eligible_for_trading) is not bool:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_INVALID_BOOLEAN")
    if lifecycle != _RESEARCH_AGENT_LIFECYCLE or eligible_for_trading:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_NOT_RESEARCH_ONLY")
    values = _ResearchAgentRunAuditEventValues(
        run_id=_research_agent_audit_identifier(run_id, field_name="run_id"),
        event_kind=normalized_event_kind,
        is_terminal=is_terminal,
        request_hash=_research_agent_audit_hash(request_hash, field_name="request_hash"),
        result_hash=_research_agent_audit_optional_hash(
            result_hash,
            field_name="result_hash",
        ),
        failure_code=(
            _research_agent_failure_code(failure_code)
            if failure_code is not None
            else None
        ),
        trace_count=_research_agent_audit_count(trace_count, field_name="trace_count"),
        trace_root_hash=_research_agent_audit_optional_hash(
            trace_root_hash,
            field_name="trace_root_hash",
        ),
        trace_tail_hash=_research_agent_audit_optional_hash(
            trace_tail_hash,
            field_name="trace_tail_hash",
        ),
        as_of=_research_agent_audit_time(as_of, field_name="as_of"),
        occurred_at=_research_agent_audit_time(occurred_at, field_name="occurred_at"),
        predecessor_record_hash=_research_agent_audit_optional_hash(
            predecessor_record_hash,
            field_name="predecessor_record_hash",
        ),
        lifecycle=_RESEARCH_AGENT_LIFECYCLE,
        eligible_for_trading=False,
    )
    if normalized_event_kind == "ADMITTED":
        valid_shape = (
            values.is_terminal is False
            and values.result_hash is None
            and values.failure_code is None
            and values.trace_count == 0
            and values.trace_root_hash is None
            and values.trace_tail_hash is None
            and values.predecessor_record_hash is None
        )
    elif normalized_event_kind == "COMPLETED":
        valid_shape = (
            values.is_terminal is True
            and values.result_hash is not None
            and values.failure_code is None
            and values.trace_count > 0
            and values.trace_root_hash is not None
            and values.trace_tail_hash is not None
            and values.predecessor_record_hash is not None
        )
    else:
        valid_shape = (
            values.is_terminal is True
            and values.result_hash is None
            and values.failure_code is not None
            and values.trace_count == 0
            and values.trace_root_hash is None
            and values.trace_tail_hash is None
            and values.predecessor_record_hash is not None
        )
    if not valid_shape:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_INVALID_EVENT_SHAPE")
    return values


def _research_agent_run_trace_values(
    *,
    run_id: object,
    sequence: object,
    tool_name: object,
    request_hash: object,
    response_hash: object,
    predecessor_trace_hash: object | None,
    trace_hash: object,
    recorded_at: object,
    lifecycle: object,
    eligible_for_trading: object,
) -> _ResearchAgentRunTraceValues:
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_INVALID_TRACE_SEQUENCE")
    if type(eligible_for_trading) is not bool:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_INVALID_BOOLEAN")
    if lifecycle != _RESEARCH_AGENT_LIFECYCLE or eligible_for_trading:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_NOT_RESEARCH_ONLY")
    normalized_tool_name = _research_agent_trace_tool_name(tool_name)
    normalized_request_hash = _research_agent_audit_hash(
        request_hash,
        field_name="trace_request_hash",
    )
    normalized_response_hash = _research_agent_audit_hash(
        response_hash,
        field_name="trace_response_hash",
    )
    normalized_predecessor = _research_agent_audit_optional_hash(
        predecessor_trace_hash,
        field_name="predecessor_trace_hash",
    )
    normalized_trace_hash = _research_agent_audit_hash(trace_hash, field_name="trace_hash")
    expected_trace_hash = _research_agent_audit_hash_payload(
        {
            "format": "northstar.research-agent-trace.v1",
            "predecessor_trace_hash": normalized_predecessor,
            "request_hash": normalized_request_hash,
            "response_hash": normalized_response_hash,
            "sequence": sequence,
            "tool_name": normalized_tool_name,
        }
    )
    if normalized_trace_hash != expected_trace_hash:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_TRACE_HASH_MISMATCH")
    return _ResearchAgentRunTraceValues(
        run_id=_research_agent_audit_identifier(run_id, field_name="run_id"),
        sequence=sequence,
        tool_name=normalized_tool_name,
        request_hash=normalized_request_hash,
        response_hash=normalized_response_hash,
        predecessor_trace_hash=normalized_predecessor,
        trace_hash=normalized_trace_hash,
        recorded_at=_research_agent_audit_time(recorded_at, field_name="recorded_at"),
        lifecycle=_RESEARCH_AGENT_LIFECYCLE,
        eligible_for_trading=False,
    )


def _research_agent_audit_event_values_from_record(
    record: ResearchAgentRunAuditEventRecord,
) -> _ResearchAgentRunAuditEventValues:
    values = _research_agent_run_audit_event_values(
        run_id=record.run_id,
        event_kind=record.event_kind,
        is_terminal=record.is_terminal,
        request_hash=record.request_hash,
        result_hash=record.result_hash,
        failure_code=record.failure_code,
        trace_count=record.trace_count,
        trace_root_hash=record.trace_root_hash,
        trace_tail_hash=record.trace_tail_hash,
        as_of=record.as_of,
        occurred_at=record.occurred_at,
        predecessor_record_hash=record.predecessor_record_hash,
        lifecycle=record.lifecycle,
        eligible_for_trading=record.eligible_for_trading,
    )
    if record.record_hash != values.record_hash:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_RECORD_TAMPERED")
    return values


def _research_agent_trace_values_from_record(
    record: ResearchAgentRunTraceEntryRecord,
) -> _ResearchAgentRunTraceValues:
    values = _research_agent_run_trace_values(
        run_id=record.run_id,
        sequence=record.sequence,
        tool_name=record.tool_name,
        request_hash=record.request_hash,
        response_hash=record.response_hash,
        predecessor_trace_hash=record.predecessor_trace_hash,
        trace_hash=record.trace_hash,
        recorded_at=record.recorded_at,
        lifecycle=record.lifecycle,
        eligible_for_trading=record.eligible_for_trading,
    )
    if record.record_hash != values.record_hash:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_TRACE_RECORD_TAMPERED")
    return values


def _validate_research_agent_trace_values(
    values: Sequence[_ResearchAgentRunTraceValues],
) -> None:
    for expected_sequence, current in enumerate(values, start=1):
        if current.sequence != expected_sequence:
            raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_TRACE_ORDER_INVALID")
        expected_predecessor = (
            None if expected_sequence == 1 else values[expected_sequence - 2].trace_hash
        )
        if current.predecessor_trace_hash != expected_predecessor:
            raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_TRACE_PREDECESSOR_INVALID")


def _validate_research_agent_run_audit_rows(
    *,
    run_id: str,
    event_records: Sequence[ResearchAgentRunAuditEventRecord],
    trace_records: Sequence[ResearchAgentRunTraceEntryRecord],
) -> None:
    event_values = tuple(
        _research_agent_audit_event_values_from_record(record) for record in event_records
    )
    trace_values = tuple(
        _research_agent_trace_values_from_record(record) for record in trace_records
    )
    if any(values.run_id != run_id for values in event_values) or any(
        values.run_id != run_id for values in trace_values
    ):
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_SCOPE_TAMPERED")
    if not event_values:
        if trace_values:
            raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_ORPHANED_TRACE")
        return
    admitted = tuple(values for values in event_values if values.event_kind == "ADMITTED")
    terminal = tuple(values for values in event_values if values.is_terminal)
    if len(admitted) != 1 or len(terminal) > 1 or len(event_values) != 1 + len(terminal):
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_EVENT_CHAIN_INVALID")
    admission = admitted[0]
    if terminal:
        terminal_event = terminal[0]
        if (
            terminal_event.predecessor_record_hash != admission.record_hash
            or terminal_event.request_hash != admission.request_hash
            or terminal_event.as_of != admission.as_of
            or terminal_event.lifecycle != admission.lifecycle
            or terminal_event.eligible_for_trading != admission.eligible_for_trading
            or terminal_event.occurred_at < admission.occurred_at
        ):
            raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_TERMINAL_CHAIN_INVALID")
        if terminal_event.event_kind == "COMPLETED":
            _validate_research_agent_trace_values(trace_values)
            if (
                len(trace_values) != terminal_event.trace_count
                or not trace_values
                or trace_values[0].trace_hash != terminal_event.trace_root_hash
                or trace_values[-1].trace_hash != terminal_event.trace_tail_hash
                or any(values.recorded_at != terminal_event.occurred_at for values in trace_values)
            ):
                raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_COMPLETION_TRACE_INVALID")
        elif trace_values:
            raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_FAILURE_TRACE_INVALID")
    elif trace_values:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_ORPHANED_TRACE")


def _require_clean_research_agent_audit_write_session(session: Session) -> None:
    if session.in_transaction() or session.new or session.dirty or session.deleted:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_SESSION_MUST_BE_CLEAN")


def _load_research_agent_run_audit_rows(
    session: Session,
    *,
    run_id: str,
    lock_events: bool,
) -> tuple[
    tuple[ResearchAgentRunAuditEventRecord, ...],
    tuple[ResearchAgentRunTraceEntryRecord, ...],
]:
    event_statement = (
        select(ResearchAgentRunAuditEventRecord)
        .where(ResearchAgentRunAuditEventRecord.run_id == run_id)
        .order_by(ResearchAgentRunAuditEventRecord.id.asc())
    )
    if lock_events:
        event_statement = event_statement.with_for_update()
    event_records = tuple(session.scalars(event_statement))
    trace_records = tuple(
        session.scalars(
            select(ResearchAgentRunTraceEntryRecord)
            .where(ResearchAgentRunTraceEntryRecord.run_id == run_id)
            .order_by(ResearchAgentRunTraceEntryRecord.sequence.asc())
        )
    )
    return event_records, trace_records


def _admitted_research_agent_run_values(
    event_records: Sequence[ResearchAgentRunAuditEventRecord],
) -> _ResearchAgentRunAuditEventValues:
    admitted_records = tuple(
        record for record in event_records if record.event_kind == "ADMITTED"
    )
    if len(admitted_records) != 1:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_ADMISSION_MISSING")
    return _research_agent_audit_event_values_from_record(admitted_records[0])


def admit_research_agent_run(
    session: Session,
    *,
    run_id: str,
    request_hash: str,
    as_of: datetime,
    admitted_at: datetime,
) -> ResearchAgentRunAuditEventRecord:
    """Atomically reserve one ResearchAgent run before its first tool call.

    This function always commits its isolated reservation.  A repeated
    ``run_id`` is never idempotent: it is an unsafe retry/unknown-side-effect
    condition and therefore always raises rather than returning an old row.
    """

    values = _research_agent_run_audit_event_values(
        run_id=run_id,
        event_kind="ADMITTED",
        is_terminal=False,
        request_hash=request_hash,
        result_hash=None,
        failure_code=None,
        trace_count=0,
        trace_root_hash=None,
        trace_tail_hash=None,
        as_of=as_of,
        occurred_at=admitted_at,
        predecessor_record_hash=None,
        lifecycle=_RESEARCH_AGENT_LIFECYCLE,
        eligible_for_trading=False,
    )
    _require_clean_research_agent_audit_write_session(session)
    row = ResearchAgentRunAuditEventRecord(
        run_id=values.run_id,
        event_kind=values.event_kind,
        is_terminal=values.is_terminal,
        request_hash=values.request_hash,
        result_hash=values.result_hash,
        failure_code=values.failure_code,
        trace_count=values.trace_count,
        trace_root_hash=values.trace_root_hash,
        trace_tail_hash=values.trace_tail_hash,
        as_of=values.as_of,
        occurred_at=values.occurred_at,
        predecessor_record_hash=values.predecessor_record_hash,
        lifecycle=values.lifecycle,
        eligible_for_trading=values.eligible_for_trading,
        record_hash=values.record_hash,
    )
    try:
        with session.begin():
            event_records, trace_records = _load_research_agent_run_audit_rows(
                session,
                run_id=values.run_id,
                lock_events=True,
            )
            _validate_research_agent_run_audit_rows(
                run_id=values.run_id,
                event_records=event_records,
                trace_records=trace_records,
            )
            if event_records:
                raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_ALREADY_RESERVED")
            session.add(row)
            session.flush()
    except ResearchAgentRunAuditError:
        if session.in_transaction():
            session.rollback()
        raise
    except IntegrityError as exc:
        if session.in_transaction():
            session.rollback()
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_ALREADY_RESERVED") from exc
    return row


def _terminal_research_agent_run_preconditions(
    *,
    session: Session,
    run_id: str,
    request_hash: str,
    occurred_at: datetime,
) -> _ResearchAgentRunAuditEventValues:
    event_records, trace_records = _load_research_agent_run_audit_rows(
        session,
        run_id=run_id,
        lock_events=True,
    )
    _validate_research_agent_run_audit_rows(
        run_id=run_id,
        event_records=event_records,
        trace_records=trace_records,
    )
    admission = _admitted_research_agent_run_values(event_records)
    if any(record.is_terminal for record in event_records):
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_TERMINAL_ALREADY_RECORDED")
    if admission.request_hash != request_hash:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_REQUEST_HASH_MISMATCH")
    if occurred_at < admission.occurred_at:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_TERMINAL_TIME_INVALID")
    return admission


def complete_research_agent_run(
    session: Session,
    *,
    run_id: str,
    request_hash: str,
    result_hash: str,
    trace_entries: Sequence[ResearchAgentRunTraceInput],
    completed_at: datetime,
) -> ResearchAgentRunAuditEventRecord:
    """Atomically append a terminal completion and its full ordered hash trace."""

    normalized_run_id = _research_agent_audit_identifier(run_id, field_name="run_id")
    normalized_request_hash = _research_agent_audit_hash(
        request_hash,
        field_name="request_hash",
    )
    normalized_result_hash = _research_agent_audit_hash(result_hash, field_name="result_hash")
    normalized_completed_at = _research_agent_audit_time(
        completed_at,
        field_name="completed_at",
    )
    inputs = tuple(trace_entries)
    if not inputs or not all(type(item) is ResearchAgentRunTraceInput for item in inputs):
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_TRACE_REQUIRED")
    trace_values = tuple(
        _research_agent_run_trace_values(
            run_id=normalized_run_id,
            sequence=item.sequence,
            tool_name=item.tool_name,
            request_hash=item.request_hash,
            response_hash=item.response_hash,
            predecessor_trace_hash=item.predecessor_trace_hash,
            trace_hash=item.trace_hash,
            recorded_at=normalized_completed_at,
            lifecycle=_RESEARCH_AGENT_LIFECYCLE,
            eligible_for_trading=False,
        )
        for item in inputs
    )
    _validate_research_agent_trace_values(trace_values)
    _require_clean_research_agent_audit_write_session(session)
    terminal_row: ResearchAgentRunAuditEventRecord | None = None
    try:
        with session.begin():
            admission = _terminal_research_agent_run_preconditions(
                session=session,
                run_id=normalized_run_id,
                request_hash=normalized_request_hash,
                occurred_at=normalized_completed_at,
            )
            terminal_values = _research_agent_run_audit_event_values(
                run_id=normalized_run_id,
                event_kind="COMPLETED",
                is_terminal=True,
                request_hash=normalized_request_hash,
                result_hash=normalized_result_hash,
                failure_code=None,
                trace_count=len(trace_values),
                trace_root_hash=trace_values[0].trace_hash,
                trace_tail_hash=trace_values[-1].trace_hash,
                as_of=admission.as_of,
                occurred_at=normalized_completed_at,
                predecessor_record_hash=admission.record_hash,
                lifecycle=_RESEARCH_AGENT_LIFECYCLE,
                eligible_for_trading=False,
            )
            trace_rows = [
                ResearchAgentRunTraceEntryRecord(
                    run_id=values.run_id,
                    sequence=values.sequence,
                    tool_name=values.tool_name,
                    request_hash=values.request_hash,
                    response_hash=values.response_hash,
                    predecessor_trace_hash=values.predecessor_trace_hash,
                    trace_hash=values.trace_hash,
                    recorded_at=values.recorded_at,
                    lifecycle=values.lifecycle,
                    eligible_for_trading=values.eligible_for_trading,
                    record_hash=values.record_hash,
                )
                for values in trace_values
            ]
            terminal_row = ResearchAgentRunAuditEventRecord(
                run_id=terminal_values.run_id,
                event_kind=terminal_values.event_kind,
                is_terminal=terminal_values.is_terminal,
                request_hash=terminal_values.request_hash,
                result_hash=terminal_values.result_hash,
                failure_code=terminal_values.failure_code,
                trace_count=terminal_values.trace_count,
                trace_root_hash=terminal_values.trace_root_hash,
                trace_tail_hash=terminal_values.trace_tail_hash,
                as_of=terminal_values.as_of,
                occurred_at=terminal_values.occurred_at,
                predecessor_record_hash=terminal_values.predecessor_record_hash,
                lifecycle=terminal_values.lifecycle,
                eligible_for_trading=terminal_values.eligible_for_trading,
                record_hash=terminal_values.record_hash,
            )
            session.add_all([*trace_rows, terminal_row])
            session.flush()
    except ResearchAgentRunAuditError:
        if session.in_transaction():
            session.rollback()
        raise
    except IntegrityError as exc:
        if session.in_transaction():
            session.rollback()
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_WRITE_CONFLICT") from exc
    if terminal_row is None:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_WRITE_CONFLICT")
    return terminal_row


def fail_research_agent_run(
    session: Session,
    *,
    run_id: str,
    request_hash: str,
    failure_code: str,
    failed_at: datetime,
) -> ResearchAgentRunAuditEventRecord:
    """Atomically append a terminal failed fact with only a stable failure code."""

    normalized_run_id = _research_agent_audit_identifier(run_id, field_name="run_id")
    normalized_request_hash = _research_agent_audit_hash(
        request_hash,
        field_name="request_hash",
    )
    normalized_failure_code = _research_agent_failure_code(failure_code)
    normalized_failed_at = _research_agent_audit_time(failed_at, field_name="failed_at")
    _require_clean_research_agent_audit_write_session(session)
    terminal_row: ResearchAgentRunAuditEventRecord | None = None
    try:
        with session.begin():
            admission = _terminal_research_agent_run_preconditions(
                session=session,
                run_id=normalized_run_id,
                request_hash=normalized_request_hash,
                occurred_at=normalized_failed_at,
            )
            terminal_values = _research_agent_run_audit_event_values(
                run_id=normalized_run_id,
                event_kind="FAILED",
                is_terminal=True,
                request_hash=normalized_request_hash,
                result_hash=None,
                failure_code=normalized_failure_code,
                trace_count=0,
                trace_root_hash=None,
                trace_tail_hash=None,
                as_of=admission.as_of,
                occurred_at=normalized_failed_at,
                predecessor_record_hash=admission.record_hash,
                lifecycle=_RESEARCH_AGENT_LIFECYCLE,
                eligible_for_trading=False,
            )
            terminal_row = ResearchAgentRunAuditEventRecord(
                run_id=terminal_values.run_id,
                event_kind=terminal_values.event_kind,
                is_terminal=terminal_values.is_terminal,
                request_hash=terminal_values.request_hash,
                result_hash=terminal_values.result_hash,
                failure_code=terminal_values.failure_code,
                trace_count=terminal_values.trace_count,
                trace_root_hash=terminal_values.trace_root_hash,
                trace_tail_hash=terminal_values.trace_tail_hash,
                as_of=terminal_values.as_of,
                occurred_at=terminal_values.occurred_at,
                predecessor_record_hash=terminal_values.predecessor_record_hash,
                lifecycle=terminal_values.lifecycle,
                eligible_for_trading=terminal_values.eligible_for_trading,
                record_hash=terminal_values.record_hash,
            )
            session.add(terminal_row)
            session.flush()
    except ResearchAgentRunAuditError:
        if session.in_transaction():
            session.rollback()
        raise
    except IntegrityError as exc:
        if session.in_transaction():
            session.rollback()
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_WRITE_CONFLICT") from exc
    if terminal_row is None:
        raise ResearchAgentRunAuditError("RESEARCH_AGENT_RUN_AUDIT_WRITE_CONFLICT")
    return terminal_row


def read_research_agent_run_audit_trail(
    session: Session,
    *,
    run_id: str,
) -> tuple[
    tuple[ResearchAgentRunAuditEventRecord, ...],
    tuple[ResearchAgentRunTraceEntryRecord, ...],
] | None:
    """Read a run's hash-only trail after fully replaying its event and trace chain."""

    normalized_run_id = _research_agent_audit_identifier(run_id, field_name="run_id")
    event_records, trace_records = _load_research_agent_run_audit_rows(
        session,
        run_id=normalized_run_id,
        lock_events=False,
    )
    _validate_research_agent_run_audit_rows(
        run_id=normalized_run_id,
        event_records=event_records,
        trace_records=trace_records,
    )
    if not event_records:
        return None
    return event_records, trace_records


def prepare_order_submission(
    session: Session,
    order: OrderRequest,
    *,
    broker: str,
    account: str,
    before_durable_intent: Callable[[OrderRequest], None] | None = None,
    submission_guard: Callable[[OrderRequest], None] | None = None,
) -> tuple[OrderRecord, bool]:
    """在触达券商前持久化完整订单意图。

    数据库唯一约束是并发下的最终仲裁者；先查询只用于减少正常路径上的异常
    开销，不能替代唯一约束。
    """

    normalized_broker = str(broker or "").strip().lower()
    normalized_account = str(account or "").strip()
    plan_id = str(order.plan_id or "").strip()
    if not normalized_broker or not normalized_account:
        raise ValueError("持久化订单意图前必须明确 broker 和 account。")
    if not plan_id:
        raise ValueError("持久化订单意图前必须提供 plan_id。")

    idempotency_key = build_order_idempotency_key(
        broker=normalized_broker,
        account=normalized_account,
        plan_id=plan_id,
        attempt_no=order.attempt_no,
    )
    request_fingerprint = build_order_request_fingerprint(
        account=normalized_account,
        strategy_id=order.strategy_id,
        symbol=order.symbol,
        side=order.side,
        qty=order.qty,
        order_type=order.order_type,
        limit_price=order.limit_price,
        plan_id=plan_id,
        attempt_no=order.attempt_no,
        instrument_id=order.instrument_id,
        exchange_id=order.exchange_id,
        ctp_offset=order.ctp_offset,
        volume_multiple=order.volume_multiple,
        margin_rate=order.margin_rate,
        currency=order.currency,
        execution_policy_fingerprint=order.execution_policy_fingerprint,
    )
    identity_conditions = (
        OrderRecord.broker == normalized_broker,
        OrderRecord.account == normalized_account,
        OrderRecord.idempotency_key == idempotency_key,
    )
    existing = session.scalar(select(OrderRecord).where(*identity_conditions))
    if existing is not None:
        if existing.request_fingerprint != request_fingerprint:
            raise RuntimeError(
                "IDEMPOTENCY_CONFLICT: 同一订单幂等键对应不同券商载荷，"
                "已禁止提交。"
            )
        return existing, False

    planned_trade_value = order.planned_trade_value
    if planned_trade_value is None:
        reference_price = order.reference_price or order.limit_price
        if reference_price is not None:
            planned_trade_value = abs(float(order.qty)) * float(reference_price)

    now = _database_utc_now(session)
    row = OrderRecord(
        profile_id=order.profile_id,
        strategy_id=order.strategy_id,
        symbol=order.symbol,
        side=order.side,
        qty=order.qty,
        target_weight=order.target_weight,
        order_type=order.order_type,
        limit_price=order.limit_price,
        order_semantic=order.order_semantic,
        reason=order.reason,
        broker=normalized_broker,
        account=normalized_account,
        instrument_id=order.instrument_id,
        exchange_id=order.exchange_id,
        ctp_offset=order.ctp_offset,
        volume_multiple=order.volume_multiple,
        margin_rate=order.margin_rate,
        required_margin=order.required_margin,
        currency=order.currency,
        reference_price=order.reference_price,
        reference_price_source=order.reference_price_source,
        planned_trade_value=planned_trade_value,
        execution_planner_id=order.execution_planner_id,
        run_id=order.run_id,
        batch_id=order.batch_id,
        plan_id=plan_id,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        execution_policy_fingerprint=order.execution_policy_fingerprint,
        attempt_no=int(order.attempt_no),
        order_ref=build_order_ref(plan_id, order.attempt_no),
        broker_order_id=None,
        status="Prepared",
        prepared_at=now,
        submitted_at=None,
        updated_at=now,
    )
    # These hooks run only for a newly created intent.  An idempotent replay
    # returns above without staging a duplicate execution plan or consuming the
    # same provenance commitment again.  Both hooks run before the intent is
    # added and the following commit makes the plan, consumption, and intent
    # one atomic durable boundary.
    if before_durable_intent is not None:
        before_durable_intent(order)
    if submission_guard is not None:
        submission_guard(order)
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        existing = session.scalar(select(OrderRecord).where(*identity_conditions))
        if existing is None:
            raise exc
        if existing.request_fingerprint != request_fingerprint:
            raise RuntimeError(
                "IDEMPOTENCY_CONFLICT: 并发请求复用了不同券商载荷。"
            ) from exc
        return existing, False
    session.refresh(row)
    return row, True


def claim_order_submission(
    session: Session,
    *,
    order_id: int,
    submission_owner: str,
    lease_resource_key: str | None = None,
    lease_owner_token: str | None = None,
    lease_fencing_token: int | None = None,
) -> bool:
    """原子认领一条 Prepared 意图，并在券商调用前提交 Submitting 状态。"""

    owner = str(submission_owner or "").strip()
    if not owner:
        raise ValueError("认领订单提交前必须提供 submission_owner。")
    now = _database_utc_now(session)
    lease_values = (
        lease_resource_key,
        lease_owner_token,
        lease_fencing_token,
    )
    if any(value is not None for value in lease_values) and not all(
        value is not None for value in lease_values
    ):
        raise ValueError("订单提交的租约身份字段必须同时提供。")
    lease_condition = None
    if lease_resource_key is not None:
        if lease_owner_token is None or lease_fencing_token is None:
            raise ValueError("订单提交缺少完整租约身份。")
        lease_fence = int(lease_fencing_token)
        lease_condition = (
            select(ExecutionLeaseRecord.resource_key)
            .where(
                ExecutionLeaseRecord.resource_key == str(lease_resource_key),
                ExecutionLeaseRecord.owner_token == str(lease_owner_token),
                ExecutionLeaseRecord.fencing_token == lease_fence,
                ExecutionLeaseRecord.expires_at > now,
            )
            .exists()
        )
    conditions = [
        OrderRecord.id == int(order_id),
        OrderRecord.status == "Prepared",
        OrderRecord.broker_order_id.is_(None),
        OrderRecord.submission_owner.is_(None),
    ]
    if lease_condition is not None:
        conditions.append(lease_condition)
    result = session.execute(
        update(OrderRecord)
        .where(*conditions)
        .values(
            status="Submitting",
            submission_owner=owner,
            lease_fencing_token=lease_fencing_token,
            submission_started_at=now,
            last_submission_error=None,
            updated_at=now,
        )
    )
    session.commit()
    return _affected_rows(result) == 1


def complete_order_submission(
    session: Session,
    *,
    order_id: int,
    submission_owner: str,
    result: OrderResult,
) -> None:
    """保存券商确认；仅当前提交所有者可以完成状态转换。"""

    acknowledged_at = utc_now()
    submitted_at = ensure_utc(result.submitted_at) if result.submitted_at else acknowledged_at
    update_result = session.execute(
        update(OrderRecord)
        .where(
            OrderRecord.id == int(order_id),
            OrderRecord.status == "Submitting",
            OrderRecord.submission_owner == str(submission_owner),
        )
        .values(
            broker_order_id=str(result.broker_order_id or "").strip() or None,
            client_id=result.client_id,
            perm_id=result.perm_id,
            status=str(result.status or "SubmissionUnknown"),
            submission_owner=None,
            last_submission_error=None,
            submitted_at=submitted_at,
            broker_acknowledged_at=acknowledged_at,
            updated_at=acknowledged_at,
        )
    )
    if _affected_rows(update_result) != 1:
        session.rollback()
        raise RuntimeError("订单提交结果持久化失败：提交所有权已丢失。")
    session.commit()


def mark_order_submission_unknown(
    session: Session,
    *,
    order_id: int,
    submission_owner: str,
    error: str,
) -> None:
    """将券商调用异常持久化为禁止自动重发的不确定状态。"""

    now = utc_now()
    result = session.execute(
        update(OrderRecord)
        .where(
            OrderRecord.id == int(order_id),
            OrderRecord.status == "Submitting",
            OrderRecord.submission_owner == str(submission_owner),
        )
        .values(
            status="SubmissionUnknown",
            submission_owner=None,
            last_submission_error=str(error)[:4000],
            updated_at=now,
        )
    )
    if _affected_rows(result) != 1:
        session.rollback()
        raise RuntimeError("订单不确定状态持久化失败：提交所有权已丢失。")
    session.commit()


def _optional_broker_int(
    value: object,
    *,
    field_name: str,
    positive: bool = False,
) -> int | None:
    """规范化券商身份整数；异常值不能静默参与订单关联。"""

    if value is None or str(value).strip() == "":
        return None
    try:
        normalized = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"BROKER_ORDER_IDENTITY_INVALID: {field_name}={value!r}"
        ) from exc
    if positive and normalized <= 0:
        return None
    return normalized


def _optional_broker_string(value: object, *, field_name: str) -> str | None:
    """规范化 CTP 合约身份字符串；空值不参与自动关联。"""

    if value is None or not str(value).strip():
        return None
    normalized = str(value).strip()
    if len(normalized) > 32:
        raise RuntimeError(
            f"BROKER_ORDER_IDENTITY_INVALID: {field_name}={value!r}"
        )
    return normalized


def _find_order_for_broker_row(
    session: Session,
    *,
    broker_order_id: str | None,
    order_ref: str | None,
    perm_id: int | None,
    client_id: int | None,
    broker: str | None,
    account: str | None,
) -> OrderRecord | None:
    """按最强可用身份关联券商订单，并拒绝含糊的弱身份匹配。"""

    scope_conditions = []
    if broker:
        scope_conditions.append(func.lower(OrderRecord.broker) == broker)
    if account:
        scope_conditions.append(OrderRecord.account == account)

    strong_identities = (
        (OrderRecord.order_ref == order_ref) if order_ref else None,
        (OrderRecord.perm_id == perm_id) if perm_id is not None else None,
    )
    for identity_condition in strong_identities:
        if identity_condition is None:
            continue
        order = session.scalar(
            select(OrderRecord)
            .where(*scope_conditions, identity_condition)
            .order_by(OrderRecord.id.desc())
            .limit(1)
        )
        if order is not None:
            return order

    if not broker_order_id:
        return None
    fallback_conditions = [
        *scope_conditions,
        OrderRecord.broker_order_id == broker_order_id,
    ]
    if client_id is not None:
        fallback_conditions.append(
            or_(
                OrderRecord.client_id == client_id,
                OrderRecord.client_id.is_(None),
            )
        )
    if order_ref:
        # 精确 orderRef 查找已经失败；只允许兼容没有 orderRef 的历史记录，
        # 不能把券商订单关联到另一个非空 orderRef。
        fallback_conditions.append(OrderRecord.order_ref.is_(None))
    candidates = list(
        session.scalars(
            select(OrderRecord)
            .where(*fallback_conditions)
            .order_by(OrderRecord.id.desc())
            .limit(2)
        )
    )
    if len(candidates) > 1:
        raise RuntimeError(
            "BROKER_ORDER_IDENTITY_AMBIGUOUS: broker/account/orderId "
            "对应多条本地订单，已停止自动恢复。"
        )
    return candidates[0] if candidates else None


def _assert_broker_order_identity(
    order: OrderRecord,
    *,
    broker_order_id: str | None,
    order_ref: str | None,
    client_id: int | None,
    perm_id: int | None,
    instrument_id: str | None,
    exchange_id: str | None,
    symbol: str | None,
    side: str | None = None,
    qty: object | None = None,
    order_type: str | None = None,
    limit_price: object | None = None,
) -> None:
    """确认恢复行与持久化订单是同一券商订单和同一合约。"""

    comparisons = (
        ("broker_order_id", order.broker_order_id, broker_order_id),
        ("order_ref", order.order_ref, order_ref),
        ("client_id", order.client_id, client_id),
        ("perm_id", order.perm_id, perm_id),
        ("instrument_id", order.instrument_id, instrument_id),
        ("exchange_id", order.exchange_id, exchange_id),
    )
    for field_name, persisted, observed in comparisons:
        if persisted is None or observed is None:
            continue
        if str(persisted) != str(observed):
            raise RuntimeError(
                "BROKER_ORDER_IDENTITY_MISMATCH: "
                f"order_id={order.id}，field={field_name}，"
                f"persisted={persisted}，observed={observed}。"
            )
    normalized_symbol = str(symbol or "").strip().upper()
    if (
        normalized_symbol
        and order.symbol
        and str(order.symbol).strip().upper() != normalized_symbol
    ):
        raise RuntimeError(
            "BROKER_ORDER_IDENTITY_MISMATCH: "
            f"order_id={order.id}，field=symbol，"
            f"persisted={order.symbol}，observed={normalized_symbol}。"
        )
    normalized_side = str(side or "").strip().upper()
    if (
        normalized_side
        and order.side
        and str(order.side).strip().upper() != normalized_side
    ):
        raise RuntimeError(
            "BROKER_ORDER_IDENTITY_MISMATCH: "
            f"order_id={order.id}，field=side，"
            f"persisted={order.side}，observed={normalized_side}。"
        )
    normalized_order_type = str(order_type or "").strip().upper()
    if (
        normalized_order_type
        and order.order_type
        and str(order.order_type).strip().upper() != normalized_order_type
    ):
        raise RuntimeError(
            "BROKER_ORDER_IDENTITY_MISMATCH: "
            f"order_id={order.id}，field=order_type，"
            f"persisted={order.order_type}，observed={normalized_order_type}。"
        )
    if qty is not None:
        observed_qty = float(str(qty).strip())
        if (
            not math.isfinite(observed_qty)
            or abs(abs(float(order.qty)) - abs(observed_qty)) > 1e-8
        ):
            raise RuntimeError(
                "BROKER_ORDER_IDENTITY_MISMATCH: "
                f"order_id={order.id}，field=qty，"
                f"persisted={order.qty}，observed={qty}。"
            )
    if limit_price is not None and order.limit_price is not None:
        observed_limit = float(str(limit_price).strip())
        if (
            not math.isfinite(observed_limit)
            or abs(float(order.limit_price) - observed_limit) > 1e-8
        ):
            raise RuntimeError(
                "BROKER_ORDER_IDENTITY_MISMATCH: "
                f"order_id={order.id}，field=limit_price，"
                f"persisted={order.limit_price}，observed={limit_price}。"
            )


def update_order_statuses(
    session: Session,
    broker_rows: Sequence[dict],
    *,
    broker: str | None = None,
    account: str | None = None,
    commit: bool = True,
) -> int:
    """按券商返回的未完成订单 / 状态信息更新本地订单状态。"""

    normalized_broker = str(broker or "").strip().lower() or None
    default_account = str(account or "").strip() or None
    updated = 0
    for row in broker_rows:
        broker_order_id = (
            str(row.get("broker_order_id") or "").strip() or None
        )
        row_account = str(row.get("account") or default_account or "").strip() or None
        order_ref = str(row.get("order_ref") or "").strip() or None
        client_id = _optional_broker_int(
            row.get("client_id"),
            field_name="client_id",
        )
        perm_id = _optional_broker_int(
            row.get("perm_id"),
            field_name="perm_id",
            positive=True,
        )
        instrument_id = _optional_broker_string(
            row.get("instrument_id"),
            field_name="instrument_id",
        )
        exchange_id = _optional_broker_string(
            row.get("exchange_id"),
            field_name="exchange_id",
        )
        if not broker_order_id and not order_ref and perm_id is None:
            continue
        order = _find_order_for_broker_row(
            session,
            broker_order_id=broker_order_id,
            order_ref=order_ref,
            perm_id=perm_id,
            client_id=client_id,
            broker=normalized_broker,
            account=row_account,
        )
        if order is None:
            continue
        _assert_broker_order_identity(
            order,
            broker_order_id=broker_order_id,
            order_ref=order_ref,
            client_id=client_id,
            perm_id=perm_id,
            instrument_id=instrument_id,
            exchange_id=exchange_id,
            symbol=str(row.get("symbol") or "").strip() or None,
            side=str(row.get("side") or "").strip() or None,
            qty=row.get("qty"),
            order_type=str(row.get("order_type") or "").strip() or None,
            limit_price=row.get("limit_price"),
        )
        new_status = str(row.get("status") or order.status)
        current_is_final = is_final_order_status(order.status)
        new_is_final = is_final_order_status(new_status)
        progress: dict[str, float | None] = {}
        for field_name in ("filled_qty", "remaining_qty", "avg_fill_price"):
            raw_value = row.get(field_name)
            if raw_value is None:
                progress[field_name] = None
                continue
            numeric_value = float(str(raw_value).strip())
            if not math.isfinite(numeric_value) or numeric_value < 0:
                raise RuntimeError(
                    "BROKER_ORDER_PROGRESS_INVALID: "
                    f"field={field_name}，value={raw_value!r}。"
                )
            progress[field_name] = numeric_value

        observed_filled = progress["filled_qty"]
        observed_remaining = progress["remaining_qty"]
        total_qty = abs(float(order.qty))
        if (
            observed_filled is not None
            and observed_filled > total_qty + 1e-8
        ) or (
            observed_remaining is not None
            and observed_remaining > total_qty + 1e-8
        ):
            raise RuntimeError(
                "BROKER_ORDER_PROGRESS_INVALID: 累计成交或剩余数量超过订单"
                f"总量，order_id={order.id}。"
            )
        if (
            str(new_status).strip().lower() == "filled"
            and (
                observed_filled is None
                or observed_remaining is None
                or abs(observed_filled - total_qty) > 1e-8
                or observed_remaining > 1e-8
            )
        ):
            raise RuntimeError(
                "BROKER_FILLED_PROGRESS_INCONSISTENT: Filled 终态与订单数量"
                f"不一致，order_id={order.id}。"
            )

        changed = False
        if not order.broker_order_id and broker_order_id:
            order.broker_order_id = broker_order_id
            order.broker_acknowledged_at = utc_now()
            changed = True
        identity_updates: dict[str, object | None] = {
            "client_id": client_id,
            "perm_id": perm_id,
            "instrument_id": instrument_id,
            "exchange_id": exchange_id,
        }
        for field_name, value in identity_updates.items():
            if value is None or getattr(order, field_name) == value:
                continue
            setattr(order, field_name, value)
            changed = True

        if observed_filled is not None and (
            order.filled_qty is None
            or observed_filled >= float(order.filled_qty) - 1e-8
        ):
            if order.filled_qty != observed_filled:
                order.filled_qty = observed_filled
                changed = True
        if observed_remaining is not None and (
            order.remaining_qty is None
            or observed_remaining <= float(order.remaining_qty) + 1e-8
        ):
            if order.remaining_qty != observed_remaining:
                order.remaining_qty = observed_remaining
                changed = True
        observed_avg_price = progress["avg_fill_price"]
        if observed_avg_price is not None and (
            order.filled_qty is None
            or observed_filled is None
            or observed_filled >= float(order.filled_qty) - 1e-8
        ):
            if order.avg_fill_price != observed_avg_price:
                order.avg_fill_price = observed_avg_price
                changed = True
        if order.status != new_status and not current_is_final:
            order.status = new_status
            changed = True
        elif order.status != new_status and current_is_final and not new_is_final:
            # 晚到的 working 回报不能把已确认终态降级。
            pass
        if changed:
            order.submission_owner = None
            order.last_submission_error = None
            order.updated_at = utc_now()
            updated += 1
    if commit:
        session.commit()
    else:
        session.flush()
    return updated


def update_single_order_status(
    session: Session,
    broker_row: dict,
    *,
    broker: str | None = None,
    account: str | None = None,
) -> int:
    """更新一条券商订单状态，供轮询执行器即时持久化终态。"""

    return update_order_statuses(
        session,
        [broker_row],
        broker=broker,
        account=account,
    )


def try_acquire_execution_lease(
    session: Session,
    *,
    resource_key: str,
    owner_token: str,
    ttl_seconds: int,
    now: datetime | None = None,
) -> int | None:
    """原子获取跨进程租约；未过期租约只能由当前 owner 续持。"""

    resource = str(resource_key or "").strip()
    owner = str(owner_token or "").strip()
    ttl = int(ttl_seconds)
    if not resource or not owner:
        raise ValueError("获取执行租约前必须提供 resource_key 和 owner_token。")
    if ttl < 1:
        raise ValueError("执行租约 ttl_seconds 必须大于零。")
    lease_now = (
        ensure_utc(now)
        if now is not None
        else _database_utc_now(session)
    )
    expires_at = lease_now + timedelta(seconds=ttl)

    existing = session.get(ExecutionLeaseRecord, resource)
    if existing is None:
        session.add(
            ExecutionLeaseRecord(
                resource_key=resource,
                owner_token=owner,
                fencing_token=1,
                acquired_at=lease_now,
                heartbeat_at=lease_now,
                expires_at=expires_at,
            )
        )
        try:
            session.commit()
            return 1
        except IntegrityError:
            session.rollback()

    same_owner_result = session.execute(
        update(ExecutionLeaseRecord)
        .where(
            ExecutionLeaseRecord.resource_key == resource,
            ExecutionLeaseRecord.owner_token == owner,
            ExecutionLeaseRecord.expires_at > lease_now,
        )
        .values(
            heartbeat_at=lease_now,
            expires_at=expires_at,
        )
    )
    session.commit()
    if _affected_rows(same_owner_result) == 1:
        fencing_token = session.scalar(
            select(ExecutionLeaseRecord.fencing_token).where(
                ExecutionLeaseRecord.resource_key == resource,
                ExecutionLeaseRecord.owner_token == owner,
            )
        )
        return int(fencing_token) if fencing_token is not None else None

    result = session.execute(
        update(ExecutionLeaseRecord)
        .where(
            ExecutionLeaseRecord.resource_key == resource,
            ExecutionLeaseRecord.expires_at <= lease_now,
        )
        .values(
            owner_token=owner,
            fencing_token=ExecutionLeaseRecord.fencing_token + 1,
            acquired_at=lease_now,
            heartbeat_at=lease_now,
            expires_at=expires_at,
        )
    )
    session.commit()
    if _affected_rows(result) != 1:
        return None
    fencing_token = session.scalar(
        select(ExecutionLeaseRecord.fencing_token).where(
            ExecutionLeaseRecord.resource_key == resource,
            ExecutionLeaseRecord.owner_token == owner,
        )
    )
    return int(fencing_token) if fencing_token is not None else None


def renew_execution_lease(
    session: Session,
    *,
    resource_key: str,
    owner_token: str,
    fencing_token: int,
    ttl_seconds: int,
    now: datetime | None = None,
) -> bool:
    """仅租约当前持有者可在未过期时续约。"""

    lease_now = (
        ensure_utc(now)
        if now is not None
        else _database_utc_now(session)
    )
    expires_at = lease_now + timedelta(seconds=int(ttl_seconds))
    result = session.execute(
        update(ExecutionLeaseRecord)
        .where(
            ExecutionLeaseRecord.resource_key == str(resource_key),
            ExecutionLeaseRecord.owner_token == str(owner_token),
            ExecutionLeaseRecord.fencing_token == int(fencing_token),
            ExecutionLeaseRecord.expires_at > lease_now,
        )
        .values(heartbeat_at=lease_now, expires_at=expires_at)
    )
    session.commit()
    return _affected_rows(result) == 1


def release_execution_lease(
    session: Session,
    *,
    resource_key: str,
    owner_token: str,
    fencing_token: int,
) -> bool:
    """释放租约；非持有者不能删除其他进程的租约。"""

    result = session.execute(
        delete(ExecutionLeaseRecord).where(
            ExecutionLeaseRecord.resource_key == str(resource_key),
            ExecutionLeaseRecord.owner_token == str(owner_token),
            ExecutionLeaseRecord.fencing_token == int(fencing_token),
        )
    )
    session.commit()
    return _affected_rows(result) == 1


def list_execution_recovery_blockers(
    session: Session,
    *,
    broker: str,
    account: str,
    ignore_in_flight_order_ref: str | None = None,
) -> list[str]:
    """列出必须先完成券商恢复、不得开启新提交的本地状态。"""

    normalized_broker = str(broker or "").strip().lower()
    normalized_account = str(account or "").strip()
    uncertain_orders = list(
        session.scalars(
            select(OrderRecord).where(
                func.lower(OrderRecord.broker) == normalized_broker,
                OrderRecord.account == normalized_account,
                func.lower(OrderRecord.status).in_(
                    ("submitting", "submissionunknown")
                ),
            )
        )
    )
    pending_cancels = list(
        session.scalars(
            select(CancelRecord).where(
                func.lower(CancelRecord.broker) == normalized_broker,
                CancelRecord.account == normalized_account,
                func.lower(CancelRecord.status).in_(
                    (
                        "cancelprepared",
                        "pendingcancel",
                        "cancelrequestfailed",
                    )
                ),
            )
        )
    )
    ignored_order_ref = str(ignore_in_flight_order_ref or "").strip()
    blockers = [
        (
            f"订单恢复未完成：order_id={row.id}，"
            f"order_ref={row.order_ref or 'N/A'}，status={row.status}"
        )
        for row in uncertain_orders
        if not ignored_order_ref
        or str(row.order_ref or "").strip() != ignored_order_ref
    ]
    blockers.extend(
        (
            f"撤单终态未确认：cancel_id={row.id}，"
            f"broker_order_id={row.broker_order_id or 'N/A'}"
        )
        for row in pending_cancels
    )
    return blockers


def _matches_observed_order_identity(
    order: OrderRecord,
    *,
    order_ref: str | None,
    perm_id: int | None,
    client_id: int | None,
    allow_client_id: bool = True,
) -> bool | None:
    """按强身份优先级判断订单；``None`` 表示只能使用旧数据弱匹配。"""

    if order_ref and order.order_ref:
        return str(order.order_ref) == order_ref
    if perm_id is not None and order.perm_id is not None:
        return int(order.perm_id) == perm_id
    if (
        allow_client_id
        and client_id is not None
        and order.client_id is not None
    ):
        return int(order.client_id) == client_id
    return None


def update_pending_cancel_statuses(
    session: Session,
    broker_rows: Sequence[dict],
    *,
    broker: str,
    account: str | None = None,
    commit: bool = True,
) -> int:
    """用券商确认的订单终态恢复对应的待确认撤单记录。"""

    normalized_broker = str(broker or "").strip().lower()
    default_account = str(account or "").strip() or None
    if not normalized_broker:
        raise ValueError("更新撤单终态前必须提供券商标识。")

    updated = 0
    for row in broker_rows:
        broker_order_id = (
            str(row.get("broker_order_id") or "").strip() or None
        )
        new_status = str(row.get("status") or "").strip()
        row_account = str(row.get("account") or default_account or "").strip()
        if (
            not row_account
            or not is_final_order_status(new_status)
        ):
            continue

        client_id = _optional_broker_int(
            row.get("client_id"),
            field_name="client_id",
        )
        perm_id = _optional_broker_int(
            row.get("perm_id"),
            field_name="perm_id",
            positive=True,
        )
        instrument_id = _optional_broker_string(
            row.get("instrument_id"),
            field_name="instrument_id",
        )
        exchange_id = _optional_broker_string(
            row.get("exchange_id"),
            field_name="exchange_id",
        )
        order_ref = str(row.get("order_ref") or "").strip() or None
        symbol = str(row.get("symbol") or "").strip() or None
        if not broker_order_id and not order_ref and perm_id is None:
            continue
        cancel_conditions = [
            func.lower(CancelRecord.broker) == normalized_broker,
            CancelRecord.account == row_account,
            func.lower(CancelRecord.status).in_(
                (
                    "cancelprepared",
                    "pendingcancel",
                    "cancelrequestfailed",
                )
            ),
        ]
        if broker_order_id:
            cancel_conditions.append(
                CancelRecord.broker_order_id == broker_order_id
            )
        cancel_rows = list(
            session.scalars(
                select(CancelRecord).where(*cancel_conditions)
            )
        )
        definite_matches: list[tuple[CancelRecord, OrderRecord]] = []
        legacy_matches: list[tuple[CancelRecord, OrderRecord]] = []
        for cancel_row in cancel_rows:
            if cancel_row.order_id is None:
                raise RuntimeError(
                    "CANCEL_ORDER_IDENTITY_MISSING: 撤单记录没有本地 order_id，"
                    "已停止自动恢复。"
                )
            order = session.get(OrderRecord, cancel_row.order_id)
            if order is None:
                raise RuntimeError(
                    "CANCEL_ORDER_IDENTITY_MISSING: 撤单记录关联的本地订单不存在，"
                    "已停止自动恢复。"
                )
            identity_match = _matches_observed_order_identity(
                order,
                order_ref=order_ref,
                perm_id=perm_id,
                client_id=client_id,
                allow_client_id=broker_order_id is not None,
            )
            if identity_match is True:
                definite_matches.append((cancel_row, order))
            elif identity_match is None:
                legacy_matches.append((cancel_row, order))

        if len(definite_matches) > 1:
            raise RuntimeError(
                "CANCEL_ORDER_IDENTITY_AMBIGUOUS: 券商终态对应多条强身份撤单记录，"
                "已停止自动恢复。"
            )
        # completedOrder 没有原始 orderId/clientId 时，只能依赖 orderRef/permId
        # 的明确命中；不能因为账户里“恰好只有一条”旧撤单就按交易语义猜测。
        target_matches = (
            definite_matches
            if definite_matches
            else (legacy_matches if broker_order_id else [])
        )
        if len(target_matches) > 1:
            raise RuntimeError(
                "CANCEL_ORDER_IDENTITY_AMBIGUOUS: 旧撤单记录缺少足够券商身份，"
                "已停止自动恢复。"
            )
        for cancel_row, order in target_matches:
            _assert_broker_order_identity(
                order,
                broker_order_id=broker_order_id,
                order_ref=order_ref,
                client_id=client_id,
                perm_id=perm_id,
                instrument_id=instrument_id,
                exchange_id=exchange_id,
                symbol=symbol,
                side=str(row.get("side") or "").strip() or None,
                qty=row.get("qty"),
                order_type=str(row.get("order_type") or "").strip() or None,
                limit_price=row.get("limit_price"),
            )
            cancel_row.status = new_status
            updated += 1

    if commit:
        session.commit()
    else:
        session.flush()
    return updated


def latest_account_snapshot(
    session: Session,
    *,
    broker: str | None = None,
    account: str | None = None,
    profile_id: str | None = None,
) -> AccountSnapshotRecord | None:
    """按明确作用域读取最近账户权益快照。"""

    stmt = select(AccountSnapshotRecord)
    if broker is not None:
        stmt = stmt.where(AccountSnapshotRecord.broker == broker.strip().lower())
    stmt = _account_snapshot_scope_clause(
        stmt,
        profile_id=profile_id,
        account=account,
    )
    return session.scalar(
        stmt.order_by(
            AccountSnapshotRecord.asof.desc(),
            AccountSnapshotRecord.id.desc(),
        ).limit(1)
    )


def list_latest_positions(
    session: Session,
    *,
    broker: str | None = None,
    account: str | None = None,
    profile_id: str | None = None,
) -> list[PositionSnapshotRecord]:
    """读取最近一次持仓快照。

    按 broker/account/profile_id 选择最新批次头，再读取整批明细。批次头
    position_count=0 时明确返回空列表；不存在批次头时同样返回空列表。
    """

    batch_stmt = select(PositionSnapshotBatchRecord)
    if broker is not None:
        batch_stmt = batch_stmt.where(
            PositionSnapshotBatchRecord.broker == broker.strip().lower()
        )
    if account is not None:
        batch_stmt = batch_stmt.where(PositionSnapshotBatchRecord.account == account)
    if profile_id is not None:
        batch_stmt = batch_stmt.where(PositionSnapshotBatchRecord.profile_id == profile_id)
    latest_batch = session.scalar(
        batch_stmt.order_by(
            PositionSnapshotBatchRecord.asof.desc(),
            PositionSnapshotBatchRecord.created_at.desc(),
        ).limit(1)
    )
    if latest_batch is None or latest_batch.position_count == 0:
        return []

    return list(
        session.scalars(
            select(PositionSnapshotRecord)
            .where(
                PositionSnapshotRecord.snapshot_batch_id
                == latest_batch.snapshot_batch_id
            )
            .order_by(PositionSnapshotRecord.symbol.asc(), PositionSnapshotRecord.id.asc())
        )
    )


def write_sync_log(
    session: Session,
    broker: str,
    sync_type: str,
    status: str,
    detail: str | None = None,
    *,
    commit: bool = True,
) -> None:
    """写入券商同步日志。"""

    session.add(BrokerSyncLog(broker=broker, sync_type=sync_type, status=status, detail=detail))
    if commit:
        session.commit()
    else:
        session.flush()


def list_recent_orders(session: Session, limit: int = 50) -> list[OrderRecord]:
    """读取最近订单记录。"""

    return list(
        session.scalars(
            select(OrderRecord).order_by(OrderRecord.submitted_at.desc()).limit(limit)
        )
    )


def list_recent_fills(session: Session, limit: int = 50) -> list[FillRecord]:
    """读取最近成交记录。"""

    return list(
        session.scalars(
            select(FillRecord).order_by(FillRecord.filled_at.desc()).limit(limit)
        )
    )


def list_recent_trade_attributions(
    session: Session,
    limit: int = 50,
    *,
    profile_id: str | None = None,
    account: str | None = None,
) -> list[TradeAttributionRecord]:
    """读取最近成交归因记录。"""

    stmt = select(TradeAttributionRecord)
    stmt = _trade_attribution_scope_clause(
        stmt,
        profile_id=profile_id,
        account=account,
    )
    return list(
        session.scalars(
            stmt.order_by(TradeAttributionRecord.attributed_at.desc()).limit(limit)
        )
    )


def list_recent_account_attributions(
    session: Session,
    limit: int = 50,
    *,
    profile_id: str | None = None,
    account: str | None = None,
) -> list[AccountAttributionRecord]:
    """读取最近账户区间收益归因。"""

    stmt = select(AccountAttributionRecord)
    if profile_id is not None:
        stmt = stmt.where(AccountAttributionRecord.profile_id == profile_id)
    if account is not None:
        stmt = stmt.where(AccountAttributionRecord.account == account)
    return list(
        session.scalars(
            stmt.order_by(AccountAttributionRecord.end_asof.desc(), AccountAttributionRecord.id.desc())
            .limit(limit)
        )
    )


def replace_anomaly_events_for_account_attribution(
    session: Session,
    *,
    account_attribution_id: int,
    profile_id: str | None,
    account: str | None,
    run_id: str | None,
    report_type: str,
    report_path: str | None,
    detected_at: datetime | None,
    alert_items: Sequence[dict[str, object]],
) -> dict[str, int]:
    """用当前最新异常项替换同一归因区间下的事件记录。"""

    deleted = _affected_rows(
        session.execute(
            delete(AnomalyEventRecord).where(
                AnomalyEventRecord.account_attribution_id
                == account_attribution_id,
                AnomalyEventRecord.report_type == report_type,
            )
        )
    )

    created = 0
    for item in alert_items:
        alert_code = _optional_text(item.get("code"))
        alert_tag = _optional_text(item.get("tag"))
        summary = _optional_text(item.get("message"))
        if alert_code is None or alert_tag is None or summary is None:
            continue
        session.add(
            AnomalyEventRecord(
                account_attribution_id=account_attribution_id,
                profile_id=profile_id,
                account=account,
                run_id=run_id,
                report_type=report_type,
                alert_code=alert_code,
                alert_tag=alert_tag,
                severity=_optional_text(item.get("severity")) or "warning",
                summary=summary,
                details_json=_serialize_json(item),
                report_path=report_path,
                detected_at=ensure_utc(detected_at),
            )
        )
        created += 1

    session.commit()
    return {"deleted": int(deleted), "created": created}


def list_recent_anomaly_events(
    session: Session,
    limit: int = 50,
    *,
    profile_id: str | None = None,
    account: str | None = None,
    alert_tag: str | None = None,
) -> list[AnomalyEventRecord]:
    """读取最近异常事件。"""

    stmt = select(AnomalyEventRecord)
    if profile_id is not None:
        stmt = stmt.where(AnomalyEventRecord.profile_id == profile_id)
    if account is not None:
        stmt = stmt.where(AnomalyEventRecord.account == account)
    if alert_tag is not None:
        stmt = stmt.where(AnomalyEventRecord.alert_tag == alert_tag)
    return list(
        session.scalars(
            stmt.order_by(AnomalyEventRecord.detected_at.desc(), AnomalyEventRecord.id.desc())
            .limit(limit)
        )
    )


def count_anomaly_events(
    session: Session,
    *,
    profile_id: str | None = None,
    account: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> int:
    """统计指定时间窗口内的异常事件数。"""

    stmt = select(func.count(AnomalyEventRecord.id))
    if profile_id is not None:
        stmt = stmt.where(AnomalyEventRecord.profile_id == profile_id)
    if account is not None:
        stmt = stmt.where(AnomalyEventRecord.account == account)
    if start_at is not None:
        stmt = stmt.where(AnomalyEventRecord.detected_at >= ensure_utc(start_at))
    if end_at is not None:
        stmt = stmt.where(AnomalyEventRecord.detected_at < ensure_utc(end_at))
    return int(session.scalar(stmt) or 0)


def save_runtime_risk_record(
    session: Session,
    *,
    profile_id: str,
    broker: str,
    account: str | None,
    can_submit: bool,
    blocking_failure_count: int,
    warning_count: int,
    checks: Sequence[dict[str, object]],
    checked_at: datetime,
) -> RuntimeRiskRecord:
    """保存盘中风控结论，供监控和每笔订单提交门禁复用。"""

    row = RuntimeRiskRecord(
        profile_id=str(profile_id).strip(),
        broker=str(broker).strip().lower(),
        account=_optional_text(account),
        can_submit=bool(can_submit),
        blocking_failure_count=int(blocking_failure_count),
        warning_count=int(warning_count),
        checks_json=_serialize_json(list(checks)) or "[]",
        checked_at=ensure_utc(checked_at),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def latest_runtime_risk_record(
    session: Session,
    *,
    profile_id: str,
    broker: str,
    account: str | None,
) -> RuntimeRiskRecord | None:
    """读取指定画像和账户最近一次盘中风控结论。"""

    stmt = select(RuntimeRiskRecord).where(
        RuntimeRiskRecord.profile_id == str(profile_id).strip(),
        RuntimeRiskRecord.broker == str(broker).strip().lower(),
    )
    normalized_account = _optional_text(account)
    if normalized_account is None:
        stmt = stmt.where(RuntimeRiskRecord.account.is_(None))
    else:
        stmt = stmt.where(RuntimeRiskRecord.account == normalized_account)
    return session.scalar(
        stmt.order_by(
            RuntimeRiskRecord.checked_at.desc(),
            RuntimeRiskRecord.id.desc(),
        ).limit(1)
    )


def latest_reconciliation_safety_state(
    session: Session,
    *,
    profile_id: str | None,
    broker: str,
    account: str | None,
) -> ReconciliationSafetyStateRecord | None:
    """读取账户最新的、不可被盘中风控结果覆盖的对账安全状态。"""

    normalized_account = _optional_text(account)
    stmt = select(ReconciliationSafetyStateRecord).where(
        ReconciliationSafetyStateRecord.profile_id
        == (str(profile_id).strip() or _UNSCOPED_RECONCILIATION_PROFILE),
        ReconciliationSafetyStateRecord.broker == str(broker).strip().lower(),
    )
    if normalized_account is None:
        stmt = stmt.where(ReconciliationSafetyStateRecord.account.is_(None))
    else:
        stmt = stmt.where(ReconciliationSafetyStateRecord.account == normalized_account)
    return session.scalar(
        stmt.order_by(
            ReconciliationSafetyStateRecord.occurred_at.desc(),
            ReconciliationSafetyStateRecord.id.desc(),
        ).limit(1)
    )


def acquire_reconciliation_safety_fence(
    session: Session,
    *,
    profile_id: str | None,
    broker: str,
    account: str | None,
) -> int:
    """Serialize safety-state reads and writes for one broker account.

    The fence is transaction-scoped deliberately: a candidate holds it from
    its final persisted-NORMAL check through the locked broker mutation and
    durable outcome commit.  Reconciliation and HALT transitions use the same
    key, so a safety transition cannot race between that check and mutation.
    """

    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("RECONCILIATION_SAFETY_FENCE_POSTGRES_REQUIRED")
    normalized_profile = str(profile_id or "").strip() or _UNSCOPED_RECONCILIATION_PROFILE
    normalized_broker = str(broker or "").strip().lower()
    normalized_account = _optional_text(account) or "__unscoped__"
    if not normalized_broker:
        raise ValueError("RECONCILIATION_SAFETY_FENCE_BROKER_REQUIRED")
    material = "\x1f".join(
        (
            "northstar.reconciliation-safety-fence.v1",
            normalized_profile,
            normalized_broker,
            normalized_account,
        )
    )
    fence_key = int.from_bytes(
        sha256(material.encode("utf-8")).digest()[:8],
        byteorder="big",
        signed=True,
    )
    session.execute(select(func.pg_advisory_xact_lock(fence_key)))
    return fence_key


def save_reconciliation_safety_state(
    session: Session,
    *,
    profile_id: str | None,
    broker: str,
    account: str | None,
    state: str,
    reason: str,
    evidence: dict[str, object],
    predecessor_hash: str | None,
    state_hash: str,
    recovery_approver_id: str | None,
    occurred_at: datetime,
    commit: bool = True,
) -> ReconciliationSafetyStateRecord:
    """追加一条对账安全状态；调用方负责执行领域状态机转换。"""

    normalized_state = str(state).strip().upper()
    if not normalized_state or not str(reason).strip() or not str(state_hash).strip():
        raise ValueError("对账安全状态必须包含 state、reason 和 state_hash")
    row = ReconciliationSafetyStateRecord(
        profile_id=str(profile_id).strip() or _UNSCOPED_RECONCILIATION_PROFILE,
        broker=str(broker).strip().lower(),
        account=_optional_text(account),
        state=normalized_state,
        reason=str(reason).strip(),
        evidence_json=_serialize_json(evidence) or "{}",
        predecessor_hash=_optional_text(predecessor_hash),
        state_hash=str(state_hash).strip(),
        recovery_approver_id=_optional_text(recovery_approver_id),
        occurred_at=ensure_utc(occurred_at),
    )
    session.add(row)
    if commit:
        session.commit()
    else:
        session.flush()
    return row


def assert_broker_order_rows_explained(
    session: Session,
    broker_rows: Sequence[dict],
    *,
    broker: str,
    account: str | None,
) -> None:
    """拒绝无法归属到内部订单账本的券商订单，避免静默接受账户外风险。"""

    normalized_broker = str(broker).strip().lower()
    expected_account = _optional_text(account)
    for row in broker_rows:
        broker_order_id = _optional_text(row.get("broker_order_id"))
        order_ref = _optional_text(row.get("order_ref"))
        raw_perm_id = row.get("perm_id")
        perm_id = (
            _optional_broker_int(raw_perm_id, field_name="perm_id", positive=True)
            if raw_perm_id is not None
            else None
        )
        row_account = _optional_text(row.get("account")) or expected_account
        if expected_account is not None and row_account != expected_account:
            raise RuntimeError(
                "BROKER_ORDER_ACCOUNT_MISMATCH: 券商订单账户不属于本次对账账户。"
            )
        if not broker_order_id and not order_ref and perm_id is None:
            raise RuntimeError(
                "BROKER_ORDER_IDENTITY_REQUIRED: 券商订单缺少可审计身份，无法对账。"
            )
        identity_conditions = []
        if broker_order_id:
            identity_conditions.append(OrderRecord.broker_order_id == broker_order_id)
        if order_ref:
            identity_conditions.append(OrderRecord.order_ref == order_ref)
        if perm_id is not None:
            identity_conditions.append(OrderRecord.perm_id == perm_id)
        stmt = select(OrderRecord.id).where(
            OrderRecord.broker == normalized_broker,
            or_(*identity_conditions),
        )
        if row_account is None:
            stmt = stmt.where(OrderRecord.account.is_(None))
        else:
            stmt = stmt.where(OrderRecord.account == row_account)
        if session.scalar(stmt.limit(1)) is None:
            raise RuntimeError(
                "BROKER_ORDER_UNEXPLAINED: 券商订单未在内部订单账本中找到。"
            )


def assert_broker_fills_explained(
    session: Session,
    fills: Sequence[FillSnapshot],
    *,
    broker: str,
    account: str | None,
) -> None:
    """拒绝不能归属到内部订单的券商成交，避免外部成交静默写入账本。"""

    normalized_broker = str(broker).strip().lower()
    expected_account = _optional_text(account)
    for item in fills:
        item_account = _optional_text(item.account) or expected_account
        if expected_account is not None and item_account != expected_account:
            raise RuntimeError(
                "BROKER_FILL_ACCOUNT_MISMATCH: 券商成交账户不属于本次对账账户。"
            )
        broker_order_id = _optional_text(item.broker_order_id)
        order_ref = _optional_text(item.order_ref)
        perm_id = item.perm_id
        client_id = item.client_id
        if not broker_order_id and not order_ref and perm_id is None:
            raise RuntimeError(
                "BROKER_FILL_IDENTITY_REQUIRED: 券商成交缺少可审计订单身份。"
            )
        identity_conditions = []
        if broker_order_id:
            identity_conditions.append(OrderRecord.broker_order_id == broker_order_id)
        if order_ref:
            identity_conditions.append(OrderRecord.order_ref == order_ref)
        if perm_id is not None:
            identity_conditions.append(OrderRecord.perm_id == int(perm_id))
        if client_id is not None and broker_order_id:
            identity_conditions.append(
                (OrderRecord.broker_order_id == broker_order_id)
                & (OrderRecord.client_id == int(client_id))
            )
        stmt = select(OrderRecord.id).where(
            OrderRecord.broker == normalized_broker,
            or_(*identity_conditions),
        )
        if item_account is None:
            stmt = stmt.where(OrderRecord.account.is_(None))
        else:
            stmt = stmt.where(OrderRecord.account == item_account)
        if session.scalar(stmt.limit(1)) is None:
            raise RuntimeError(
                "BROKER_FILL_UNEXPLAINED: 券商成交未在内部订单账本中找到。"
            )


def save_run_health_record(
    session: Session,
    *,
    run_id: str | None,
    profile_id: str | None,
    mode: str,
    broker: str,
    account: str | None,
    preflight_can_trade: bool,
    blocking_failure_count: int = 0,
    warning_count: int = 0,
    target_symbol_count: int = 0,
    target_weight_sum: float | None = None,
    execution_plan_count: int = 0,
    planned_trade_value: float | None = None,
    plan_consistency_issue_count: int = 0,
    open_order_count: int = 0,
    partial_fill_count: int = 0,
    fills_seen_count: int = 0,
    execution_shortfall: float | None = None,
    execution_shortfall_bps: float | None = None,
    residual_pnl: float | None = None,
    anomaly_count_trailing_7d: int = 0,
    anomaly_count_prev_7d: int = 0,
    anomaly_trend: str | None = None,
    details: dict[str, object] | None = None,
) -> RunHealthRecord:
    """保存一条 soak / shadow 运行健康记录。"""

    row = RunHealthRecord(
        run_id=run_id,
        profile_id=profile_id,
        mode=mode,
        broker=broker,
        account=account,
        preflight_can_trade=bool(preflight_can_trade),
        blocking_failure_count=int(blocking_failure_count),
        warning_count=int(warning_count),
        target_symbol_count=int(target_symbol_count),
        target_weight_sum=_optional_float(target_weight_sum),
        execution_plan_count=int(execution_plan_count),
        planned_trade_value=_optional_float(planned_trade_value),
        plan_consistency_issue_count=int(plan_consistency_issue_count),
        open_order_count=int(open_order_count),
        partial_fill_count=int(partial_fill_count),
        fills_seen_count=int(fills_seen_count),
        execution_shortfall=_optional_float(execution_shortfall),
        execution_shortfall_bps=_optional_float(execution_shortfall_bps),
        residual_pnl=_optional_float(residual_pnl),
        anomaly_count_trailing_7d=int(anomaly_count_trailing_7d),
        anomaly_count_prev_7d=int(anomaly_count_prev_7d),
        anomaly_trend=_optional_text(anomaly_trend),
        details_json=_serialize_json(details or {}),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_run_health_records(
    session: Session,
    limit: int = 50,
    *,
    profile_id: str | None = None,
    account: str | None = None,
    mode: str | None = None,
    since: datetime | None = None,
) -> list[RunHealthRecord]:
    """读取最近运行健康记录。"""

    stmt = select(RunHealthRecord)
    if profile_id is not None:
        stmt = stmt.where(RunHealthRecord.profile_id == profile_id)
    if account is not None:
        stmt = stmt.where(RunHealthRecord.account == account)
    if mode is not None:
        stmt = stmt.where(RunHealthRecord.mode == mode)
    if since is not None:
        stmt = stmt.where(RunHealthRecord.created_at >= ensure_utc(since))
    return list(
        session.scalars(
            stmt.order_by(RunHealthRecord.created_at.desc(), RunHealthRecord.id.desc()).limit(limit)
        )
    )


def aggregate_position_market_value(
    session: Session,
    *,
    broker: str | None = None,
    account: str | None = None,
    profile_id: str | None = None,
) -> float:
    """估算最近一次真实持仓的总市值。"""

    rows = list_latest_positions(
        session,
        broker=broker,
        account=account,
        profile_id=profile_id,
    )
    total = 0.0
    for row in rows:
        total += float(row.market_value or 0.0)
    return total
