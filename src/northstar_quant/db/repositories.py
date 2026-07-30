"""数据库写入辅助函数。"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4

import polars as pl
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from northstar_quant.common.order_identity import (
    build_order_idempotency_key,
    build_order_ref,
    build_order_request_fingerprint,
)
from northstar_quant.common.order_status import (
    FINAL_ORDER_STATUSES,
    is_filled_order_status,
    is_final_order_status,
)
from northstar_quant.common.time import ensure_utc, utc_now
from northstar_quant.db.models import (
    AccountAttributionRecord,
    AccountSnapshotRecord,
    AnomalyEventRecord,
    BrokerSyncLog,
    CancelRecord,
    ExecutionLeaseRecord,
    ExecutionPlanRecord,
    FillRecord,
    OrderRecord,
    PositionSnapshotBatchRecord,
    PositionSnapshotRecord,
    RuntimeRiskRecord,
    RunHealthRecord,
    StrategyRunRecord,
    StrategySnapshotRecord,
    TradeAttributionRecord,
    WorkingOrderSnapshotRecord,
)
from northstar_quant.execution.models import (
    BrokerStateSnapshot,
    FillSnapshot,
    OrderRequest,
    OrderResult,
    PositionSnapshot,
    RebalanceOrderPlan,
)


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
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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

    session.commit()
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
    gross_exposure = (
        gross_position_value / net_liquidation
        if net_liquidation not in (None, 0.0)
        else None
    )
    net_exposure = (
        net_position_value / net_liquidation
        if net_liquidation not in (None, 0.0)
        else None
    )

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
        session.refresh(row)
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

    end_price_by_symbol = {
        symbol: _position_price(row)
        for symbol, row in end_positions.items()
        if _position_price(row) is not None
    }
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

    row = AccountAttributionRecord(
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
    session.add(row)
    if commit:
        session.commit()
        session.refresh(row)
    else:
        session.flush()
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


def prepare_order_submission(
    session: Session,
    order: OrderRequest,
    *,
    broker: str,
    account: str,
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
    blockers = [
        (
            f"订单恢复未完成：order_id={row.id}，"
            f"order_ref={row.order_ref or 'N/A'}，status={row.status}"
        )
        for row in uncertain_orders
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

    stmt = select(AccountAttributionRecord).where(True)
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
