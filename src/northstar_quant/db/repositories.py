"""数据库写入辅助函数。"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from uuid import uuid4

import polars as pl
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from northstar_quant.common.time import ensure_utc, utc_now
from northstar_quant.db.models import (
    AccountAttributionRecord,
    AccountSnapshotRecord,
    AnomalyEventRecord,
    BrokerSyncLog,
    CancelRecord,
    ExecutionPlanRecord,
    FillRecord,
    OrderRecord,
    PositionSnapshotRecord,
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


def save_position_snapshots(session: Session, snapshots: list[PositionSnapshot]) -> int:
    """批量保存真实持仓快照。

    每次保存都视为一次完整的“持仓批次”：
    - 同一批共享一个 snapshot_batch_id
    - 同一批共享一个 asof

    这样即便上游误传了逐行不同的时间戳，库里仍能保留稳定的批次边界。
    """

    if not snapshots:
        return 0

    asof_values = [ensure_utc(item.asof) for item in snapshots if item.asof is not None]
    batch_asof = max(asof_values) if asof_values else utc_now()
    batch_id = next(
        (item.snapshot_batch_id for item in snapshots if item.snapshot_batch_id),
        f"position-batch-{uuid4().hex}",
    )

    count = 0
    for item in snapshots:
        session.add(
            PositionSnapshotRecord(
                account=item.account,
                symbol=item.symbol,
                qty=item.qty,
                avg_cost=item.avg_cost,
                market_price=item.market_price,
                market_value=item.market_value,
                asof=batch_asof,
                snapshot_batch_id=batch_id,
            )
        )
        count += 1
    session.commit()
    return count


def _serialize_json(payload: object | None) -> str | None:
    if payload is None:
        return None
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


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
    (
        "dividend",
        (
            "dividend",
            "dividends",
            "distribution",
            "distributions",
            "capital gain distribution",
            "capital gains distribution",
        ),
    ),
    ("interest", ("interest",)),
    ("tax", ("withholding tax", "withholding", "tax", "levy")),
    ("fee", ("commission", "commissions", "fee", "fees", "charge", "charges", "regulatory fee")),
    (
        "corporate_action",
        (
            "corporate action",
            "corp action",
            "cash in lieu",
            "cash merger",
            "reorg",
            "reorganization",
            "spin off",
            "spinoff",
            "stock split",
            "reverse split",
            "split",
            "merger",
            "tender",
            "redemption",
            "exchange offer",
            "liquidation",
        ),
    ),
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
        "dividend": 0.0,
        "interest": 0.0,
        "fee": 0.0,
        "tax": 0.0,
        "funding": 0.0,
        "corporate_action": 0.0,
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
            )
        )
        count += 1

    session.commit()
    return count


def save_fill_snapshots(session: Session, fills: list[FillSnapshot]) -> int:
    """批量保存成交快照。

    这里按 broker_order_id + symbol + qty + price + filled_at 做最基础的去重，
    避免轮询同步时把同一笔成交重复写入。
    """

    count = 0
    for item in fills:
        exists = session.scalar(
            select(FillRecord.id).where(
                FillRecord.broker_order_id == item.broker_order_id,
                FillRecord.symbol == item.symbol,
                FillRecord.qty == item.qty,
                FillRecord.price == item.price,
                FillRecord.filled_at == item.filled_at,
            )
        )
        if exists:
            continue

        order_row = session.scalar(
            select(OrderRecord)
            .where(OrderRecord.broker_order_id == item.broker_order_id)
            .order_by(OrderRecord.submitted_at.desc(), OrderRecord.id.desc())
            .limit(1)
        )
        fill_row = FillRecord(
            order_id=order_row.id if order_row is not None else None,
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
        count += 1
    session.commit()
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

    session.commit()
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
) -> AccountSnapshotRecord:
    """保存账户账本快照。"""

    account_values = snapshot.account_values or {}
    account = _optional_text(account_values.get("Account"))
    batch_id = next(
        (item.snapshot_batch_id for item in snapshot.positions if item.snapshot_batch_id),
        None,
    )
    if account is None:
        account = next((item.account for item in snapshot.positions if item.account), None)

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
    session.commit()
    session.refresh(row)
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
        dividend_cash_flow=non_trade_components["dividend"],
        interest_cash_flow=non_trade_components["interest"],
        fee_cash_flow=non_trade_components["fee"],
        tax_cash_flow=non_trade_components["tax"],
        funding_cash_flow=non_trade_components["funding"],
        corporate_action_cash_flow=non_trade_components["corporate_action"],
        other_non_trade_cash_flow=non_trade_components["other"],
        total_non_trade_cash_flow=total_non_trade_cash_flow,
        traded_notional=traded_notional,
        fill_count=len(interval_trades),
        residual_pnl=residual_pnl,
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


def save_order_result(
    session: Session,
    order: OrderRequest,
    result: OrderResult,
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
        account=order.account,
        reference_price=order.reference_price,
        reference_price_source=order.reference_price_source,
        planned_trade_value=planned_trade_value,
        execution_planner_id=order.execution_planner_id,
        run_id=order.run_id,
        batch_id=order.batch_id,
        plan_id=order.plan_id,
        broker_order_id=result.broker_order_id,
        status=result.status,
        submitted_at=ensure_utc(result.submitted_at),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_order_statuses(session: Session, broker_rows: Sequence[dict]) -> int:
    """按券商返回的未完成订单 / 状态信息更新本地订单状态。"""

    updated = 0
    for row in broker_rows:
        broker_order_id = str(row.get('broker_order_id') or '')
        if not broker_order_id:
            continue
        order = session.scalar(
            select(OrderRecord).where(OrderRecord.broker_order_id == broker_order_id)
        )
        if order is None:
            continue
        new_status = str(row.get('status') or order.status)
        if order.status != new_status:
            order.status = new_status
            updated += 1
    session.commit()
    return updated


def list_latest_positions(session: Session) -> list[PositionSnapshotRecord]:
    """读取最近一次持仓快照。

    新版本优先按 snapshot_batch_id 读取最新一整批持仓，
    避免“逐行 asof 略有差异，结果只读到最后一条”的问题。

    对历史旧数据，如果还没有 snapshot_batch_id，则回退到旧的 asof 逻辑。
    """

    latest_batch_id = session.scalar(
        select(PositionSnapshotRecord.snapshot_batch_id)
        .where(PositionSnapshotRecord.snapshot_batch_id.is_not(None))
        .order_by(PositionSnapshotRecord.asof.desc(), PositionSnapshotRecord.id.desc())
        .limit(1)
    )
    if latest_batch_id is not None:
        return list(
            session.scalars(
                select(PositionSnapshotRecord)
                .where(PositionSnapshotRecord.snapshot_batch_id == latest_batch_id)
                .order_by(PositionSnapshotRecord.symbol.asc(), PositionSnapshotRecord.id.asc())
            )
        )

    latest_asof = session.scalar(
        select(PositionSnapshotRecord.asof)
        .order_by(PositionSnapshotRecord.asof.desc(), PositionSnapshotRecord.id.desc())
        .limit(1)
    )
    if latest_asof is None:
        return []
    return list(
        session.scalars(
            select(PositionSnapshotRecord)
            .where(PositionSnapshotRecord.asof == latest_asof)
            .order_by(PositionSnapshotRecord.symbol.asc(), PositionSnapshotRecord.id.asc())
        )
    )


def write_sync_log(
    session: Session,
    broker: str,
    sync_type: str,
    status: str,
    detail: str | None = None,
) -> None:
    """写入券商同步日志。"""

    session.add(BrokerSyncLog(broker=broker, sync_type=sync_type, status=status, detail=detail))
    session.commit()


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

    deleted = session.execute(
        delete(AnomalyEventRecord).where(
            AnomalyEventRecord.account_attribution_id == account_attribution_id,
            AnomalyEventRecord.report_type == report_type,
        )
    ).rowcount or 0

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


def aggregate_position_market_value(session: Session) -> float:
    """估算最近一次真实持仓的总市值。"""

    rows = list_latest_positions(session)
    total = 0.0
    for row in rows:
        total += float(row.market_value or 0.0)
    return total
