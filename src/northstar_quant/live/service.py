"""实盘主服务。"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import timedelta
from uuid import uuid4

import polars as pl

from northstar_quant.common.enums import StrategyOutputType
from northstar_quant.common.time import utc_now
from northstar_quant.config.settings import get_settings
from northstar_quant.config.trading_profile import ensure_production_profile, load_trading_profile
from northstar_quant.data.storage import load_profile_market_data, load_profile_signal_data
from northstar_quant.db.repositories import (
    count_anomaly_events,
    list_recent_anomaly_events,
    list_recent_account_attributions,
    list_run_health_records,
    list_recent_trade_attributions,
    save_run_health_record,
    save_execution_plan_records,
    save_order_result,
    save_strategy_run_snapshot,
)
from northstar_quant.db.session import SessionLocal
from northstar_quant.execution.ibkr_adapter import IBKRBrokerAdapter
from northstar_quant.execution.limit_chase_executor import LimitChaseExecutor
from northstar_quant.execution.limit_executor import build_limit_order
from northstar_quant.execution.models import BrokerStateSnapshot, OrderRequest
from northstar_quant.execution.pricing import (
    build_execution_reference_price_map,
    normalize_symbols,
)
from northstar_quant.execution.paper_broker import PaperBrokerAdapter
from northstar_quant.execution.reconciliation import analyze_position_drift, reconcile_broker_state
from northstar_quant.execution.registry import build_execution_plan, resolve_execution_planner
from northstar_quant.execution.router import OrderRouter
from northstar_quant.live.ibkr_service import IBKRService
from northstar_quant.live.order_management import cancel_stale_orders
from northstar_quant.live.preflight import build_preflight_result
from northstar_quant.logging_.logger import get_logger
from northstar_quant.monitoring.alerts import send_alert
from northstar_quant.reporting.report_builder import latest_live_account_attribution_summary
from northstar_quant.risk.models import OrderRiskContext
from northstar_quant.strategies.pipeline import (
    build_profile_risk_limits,
    run_profile_strategy_pipeline,
)

logger = get_logger(__name__)


def _pick_broker(service: IBKRService | None = None):
    settings = get_settings()
    if settings.broker == "ibkr":
        return IBKRBrokerAdapter(service=service)
    return PaperBrokerAdapter()


def _live_execution_guard_messages(broker_name: str) -> list[str]:
    settings = get_settings()
    normalized_broker = broker_name.strip().lower()
    messages: list[str] = []

    if settings.kill_switch_enabled:
        messages.append("KILL_SWITCH_ENABLED: 交易 kill switch 已开启，本次不下单。")

    if normalized_broker != "paper" and not settings.live_trading_enabled:
        messages.append(
            "LIVE_TRADING_DISABLED: 真实券商下单开关未开启；"
            "需要显式设置 NORTHSTAR_LIVE_TRADING_ENABLED=true。"
        )

    return messages


def _latest_valuation_price_map(market_df: pl.DataFrame) -> dict[str, float]:
    return {
        str(row["symbol"]).strip().upper(): float(row["close"])
        for row in market_df.group_by("symbol").tail(1).select(["symbol", "close"]).to_dicts()
    }


def _collect_execution_symbols(
    output: pl.DataFrame,
    state,
) -> list[str]:
    output_symbols = output["symbol"].to_list() if "symbol" in output.columns else []
    position_symbols = [item.symbol for item in state.positions]
    open_order_symbols = [str(row.get("symbol") or "") for row in state.open_orders]
    return normalize_symbols(output_symbols + position_symbols + open_order_symbols)


def _resolve_execution_reference_prices(
    broker,
    symbols: list[str],
    valuation_prices: dict[str, float],
) -> tuple[dict[str, float], dict[str, str]]:
    fallback_prices = {
        symbol: valuation_prices[symbol]
        for symbol in symbols
        if symbol in valuation_prices
    }
    broker_quotes = broker.get_market_quotes(symbols)
    return build_execution_reference_price_map(broker_quotes, fallback_prices)


def _empty_drift_result(output_type: StrategyOutputType) -> dict:
    return {
        "summary": {
            "total_abs_weight_diff": 0.0,
            "max_abs_weight_diff": 0.0,
            "supported": output_type == StrategyOutputType.TARGET_WEIGHT,
            "output_type": output_type.value,
        },
        "details": [],
    }


def _preflight_blocked_messages(preflight: dict) -> list[str]:
    messages = ["PRECHECK_BLOCKED: 实盘 preflight 未通过，本次只同步不下单。"]
    messages.extend(str(message) for message in preflight.get("blocking_messages", []))
    messages.extend(str(message) for message in preflight.get("warning_messages", []))
    return messages


def _build_preflight_alert_message(preflight: dict) -> str:
    lines = [
        "Northstar Quant 已阻止本次执行。",
        f"画像：{preflight.get('profile_id')}",
        "状态：只同步，不下单。",
    ]
    lines.extend(f"- {message}" for message in preflight.get("blocking_messages", []))
    warning_messages = [
        str(message)
        for message in preflight.get("warning_messages", [])
        if str(message).strip()
    ]
    if warning_messages:
        lines.append("附加关注：")
        lines.extend(f"- {message}" for message in warning_messages)
    return "\n".join(lines)


def run_live_preflight(profile_id: str | None = None) -> dict:
    """执行一次实盘 preflight，但不真正下单。"""

    profile = ensure_production_profile(
        load_trading_profile(profile_id),
        context="live.preflight",
    )
    raw_market_df = load_profile_market_data(profile)
    signal_market_df = load_profile_signal_data(profile)
    valuation_prices = _latest_valuation_price_map(raw_market_df)
    pipeline = run_profile_strategy_pipeline(signal_market_df, profile, latest_only=True)
    service = IBKRService() if get_settings().broker == "ibkr" else None
    broker = _pick_broker(service)
    broker.connect()
    try:
        state = broker.sync_state()
        execution_symbols = _collect_execution_symbols(pipeline.frame, state)
        execution_reference_prices, execution_price_sources = _resolve_execution_reference_prices(
            broker,
            execution_symbols,
            valuation_prices,
        )
        account = getattr(broker, "account", None) or get_settings().ibkr_account
        preflight = build_preflight_result(
            profile=profile,
            raw_market_df=raw_market_df,
            signal_market_df=signal_market_df,
            output_frame=pipeline.frame,
            output_time_column=pipeline.time_column,
            broker_state=state,
            execution_symbols=execution_symbols,
            execution_reference_prices=execution_reference_prices,
            execution_price_sources=execution_price_sources,
            equity=_extract_equity(state.account_values),
            live_account_attribution=latest_live_account_attribution_summary(
                profile_id=profile.profile_id,
                account=account,
            ),
        )
        return preflight.to_dict()
    finally:
        broker.disconnect()


def _partial_fill_count(open_orders: list[dict]) -> int:
    count = 0
    for row in open_orders:
        filled_qty = float(row.get("filled_qty", 0.0) or 0.0)
        remaining_qty = float(row.get("remaining_qty", 0.0) or 0.0)
        if filled_qty > 1e-8 and remaining_qty > 1e-8:
            count += 1
    return count


def _target_summary(output_frame: pl.DataFrame, output_type: StrategyOutputType) -> tuple[int, float | None]:
    if output_type != StrategyOutputType.TARGET_WEIGHT or output_frame.is_empty():
        return output_frame.height, None
    target_rows = [
        row
        for row in output_frame.to_dicts()
        if abs(float(row.get("target_weight", 0.0) or 0.0)) > 1e-8
    ]
    return len(target_rows), sum(float(row.get("target_weight", 0.0) or 0.0) for row in target_rows)


def _plan_consistency_issue_count(plans) -> int:
    issues = 0
    for plan in plans:
        delta_qty = float(plan.target_qty or 0.0) - float(plan.current_qty or 0.0)
        qty = float(plan.qty or 0.0)
        if abs(abs(delta_qty) - abs(qty)) > 1e-6:
            issues += 1
            continue
        if delta_qty > 1e-8 and str(plan.side).upper() != "BUY":
            issues += 1
            continue
        if delta_qty < -1e-8 and str(plan.side).upper() != "SELL":
            issues += 1
            continue
        expected_trade_value = abs(delta_qty) * float(plan.execution_reference_price or plan.latest_price or 0.0)
        if expected_trade_value > 0 and abs(expected_trade_value - float(plan.estimated_trade_value or 0.0)) > 1e-4:
            issues += 1
    return issues


def _anomaly_trend(current_count: int, previous_count: int) -> str:
    if current_count < previous_count:
        return "down"
    if current_count > previous_count:
        return "up"
    return "flat"


def _execution_shortfall_bps(summary: dict | None) -> float | None:
    if not summary:
        return None
    traded_notional = float(summary.get("traded_notional") or 0.0)
    execution_shortfall = float(summary.get("execution_shortfall") or 0.0)
    if traded_notional <= 1e-8:
        return None
    return execution_shortfall / traded_notional * 10000.0


def _record_run_health(
    session,
    *,
    run_id: str,
    profile_id: str,
    mode: str,
    broker_name: str,
    account: str | None,
    preflight: dict,
    broker_state,
    pipeline,
    plans,
    live_account_attribution: dict | None,
    extra_details: dict | None = None,
) -> dict:
    target_symbol_count, target_weight_sum = _target_summary(
        pipeline.frame,
        pipeline.output_type,
    )
    planned_trade_value = sum(float(plan.estimated_trade_value or 0.0) for plan in plans)
    current_window_end = utc_now()
    current_window_start = current_window_end - timedelta(days=7)
    previous_window_start = current_window_start - timedelta(days=7)
    anomaly_count_trailing_7d = count_anomaly_events(
        session,
        profile_id=profile_id,
        account=account,
        start_at=current_window_start,
        end_at=current_window_end,
    )
    anomaly_count_prev_7d = count_anomaly_events(
        session,
        profile_id=profile_id,
        account=account,
        start_at=previous_window_start,
        end_at=current_window_start,
    )
    row = save_run_health_record(
        session,
        run_id=run_id,
        profile_id=profile_id,
        mode=mode,
        broker=broker_name,
        account=account,
        preflight_can_trade=bool(preflight.get("can_trade")),
        blocking_failure_count=int(preflight.get("blocking_failure_count", 0) or 0),
        warning_count=int(preflight.get("warning_count", 0) or 0),
        target_symbol_count=target_symbol_count,
        target_weight_sum=target_weight_sum,
        execution_plan_count=len(plans),
        planned_trade_value=planned_trade_value,
        plan_consistency_issue_count=_plan_consistency_issue_count(plans),
        open_order_count=len(broker_state.open_orders),
        partial_fill_count=_partial_fill_count(broker_state.open_orders),
        fills_seen_count=len(broker_state.fills),
        execution_shortfall=(
            float(live_account_attribution.get("execution_shortfall") or 0.0)
            if live_account_attribution
            else None
        ),
        execution_shortfall_bps=_execution_shortfall_bps(live_account_attribution),
        residual_pnl=(
            float(live_account_attribution.get("residual_pnl") or 0.0)
            if live_account_attribution
            else None
        ),
        anomaly_count_trailing_7d=anomaly_count_trailing_7d,
        anomaly_count_prev_7d=anomaly_count_prev_7d,
        anomaly_trend=_anomaly_trend(anomaly_count_trailing_7d, anomaly_count_prev_7d),
        details={
            "preflight": preflight,
            "extra": extra_details or {},
        },
    )
    return {
        "id": row.id,
        "mode": row.mode,
        "preflight_can_trade": row.preflight_can_trade,
        "execution_plan_count": row.execution_plan_count,
        "plan_consistency_issue_count": row.plan_consistency_issue_count,
        "open_order_count": row.open_order_count,
        "partial_fill_count": row.partial_fill_count,
        "execution_shortfall_bps": row.execution_shortfall_bps,
        "residual_pnl": row.residual_pnl,
        "anomaly_count_trailing_7d": row.anomaly_count_trailing_7d,
        "anomaly_count_prev_7d": row.anomaly_count_prev_7d,
        "anomaly_trend": row.anomaly_trend,
        "created_at": row.created_at.isoformat(),
    }


def run_shadow_once(profile_id: str | None = None) -> dict:
    """执行一次 shadow run：同步、建计划、落账，但不真正下单。"""

    profile = ensure_production_profile(
        load_trading_profile(profile_id),
        context="live.shadow-run",
    )
    shadow_logger = logger.bind(command="live.shadow-run", profile=profile.profile_id)
    shadow_logger.info("开始执行 shadow run")
    raw_market_df = load_profile_market_data(profile)
    signal_market_df = load_profile_signal_data(profile)
    valuation_prices = _latest_valuation_price_map(raw_market_df)

    service = IBKRService() if get_settings().broker == "ibkr" else None
    broker = _pick_broker(service)
    broker.connect()
    try:
        pipeline = run_profile_strategy_pipeline(signal_market_df, profile, latest_only=True)
        run_id = f"shadow-run-{uuid4().hex}"
        account = getattr(broker, "account", None) or get_settings().ibkr_account
        with SessionLocal() as session:
            save_strategy_run_snapshot(
                session,
                run_id=run_id,
                profile_id=profile.profile_id,
                pipeline_strategy_id=pipeline.strategy_id,
                output_type=pipeline.output_type,
                time_column=pipeline.time_column,
                output_frame=pipeline.frame,
                selected_strategy_ids=[item.strategy_id for item in profile.enabled_strategies],
                strategy_params={
                    item.strategy_id: dict(item.params)
                    for item in profile.enabled_strategies
                },
                risk_limits=dict(profile.risk),
                market_data_frame=raw_market_df,
                signal_data_frame=signal_market_df,
            )
            state = broker.sync_state()
            sync_result = reconcile_broker_state(
                session,
                broker,
                snapshot=state,
                run_id=run_id,
                profile_id=profile.profile_id,
            )
            execution_symbols = _collect_execution_symbols(pipeline.frame, state)
            execution_reference_prices, execution_price_sources = _resolve_execution_reference_prices(
                broker,
                execution_symbols,
                valuation_prices,
            )
            live_account_attribution = latest_live_account_attribution_summary(
                profile_id=profile.profile_id,
                account=account,
            )
            preflight = build_preflight_result(
                profile=profile,
                raw_market_df=raw_market_df,
                signal_market_df=signal_market_df,
                output_frame=pipeline.frame,
                output_time_column=pipeline.time_column,
                broker_state=state,
                execution_symbols=execution_symbols,
                execution_reference_prices=execution_reference_prices,
                execution_price_sources=execution_price_sources,
                equity=_extract_equity(state.account_values),
                live_account_attribution=live_account_attribution,
            ).to_dict()
            plans = []
            planned_order_count = 0
            if preflight["can_trade"]:
                planner = resolve_execution_planner(profile, pipeline.output_type)
                plans = build_execution_plan(
                    profile,
                    pipeline.frame,
                    pipeline.output_type,
                    state,
                    execution_reference_prices,
                    equity=_extract_equity(state.account_values),
                )
                batch_id = f"shadow-batch-{uuid4().hex[:12]}"
                for idx, plan in enumerate(plans, start=1):
                    plan.plan_id = f"{batch_id}-{idx:04d}-{plan.symbol.lower()}"
                planned_order_count = save_execution_plan_records(
                    session,
                    plans,
                    run_id=run_id,
                    batch_id=batch_id,
                    profile_id=profile.profile_id,
                    execution_planner_id=planner.planner_id,
                )
            run_health = _record_run_health(
                session,
                run_id=run_id,
                profile_id=profile.profile_id,
                mode="shadow_run",
                broker_name=broker.get_name(),
                account=account,
                preflight=preflight,
                broker_state=state,
                pipeline=pipeline,
                plans=plans,
                live_account_attribution=live_account_attribution,
                extra_details={
                    "sync_result": sync_result,
                    "execution_price_sources": execution_price_sources,
                    "planned_order_count": planned_order_count,
                },
            )
            if not preflight["can_trade"]:
                send_alert(_build_preflight_alert_message(preflight), level="warning")
            result = {
                "run_id": run_id,
                "profile_id": profile.profile_id,
                "mode": "shadow_run",
                "broker": broker.get_name(),
                "preflight": preflight,
                "sync_result": sync_result,
                "plan_count": len(plans),
                "planned_order_count": planned_order_count,
                "planned_trade_value": sum(float(plan.estimated_trade_value or 0.0) for plan in plans),
                "run_health": run_health,
            }
            shadow_logger.bind(
                run_id=run_id,
                plan_count=len(plans),
                preflight_can_trade=preflight["can_trade"],
            ).info("shadow run 完成")
            return result
    finally:
        broker.disconnect()


def run_live_once(profile_id: str | None = None) -> list[str]:
    """运行一次完整实盘主流程。"""

    profile = ensure_production_profile(
        load_trading_profile(profile_id),
        context="live.run",
    )
    run_logger = logger.bind(command="live.run", profile=profile.profile_id)
    run_logger.info("开始执行一次实盘主流程")
    settings = get_settings()
    guard_messages = _live_execution_guard_messages(settings.broker)
    if guard_messages:
        run_logger.warning("实盘执行被安全开关拦截：%s", " | ".join(guard_messages))
        send_alert(
            "Northstar Quant 实盘执行被安全开关拦截。\n" + "\n".join(guard_messages),
            level="warning",
        )
        return guard_messages

    raw_market_df = load_profile_market_data(profile)
    signal_market_df = load_profile_signal_data(profile)
    valuation_prices = _latest_valuation_price_map(raw_market_df)

    service = IBKRService() if settings.broker == "ibkr" else None
    broker = _pick_broker(service)
    broker.connect()
    try:
        pipeline = run_profile_strategy_pipeline(signal_market_df, profile, latest_only=True)
        limits = build_profile_risk_limits(profile)
        run_id = f"live-run-{uuid4().hex}"
        account = getattr(broker, "account", None) or get_settings().ibkr_account
        with SessionLocal() as session:
            save_strategy_run_snapshot(
                session,
                run_id=run_id,
                profile_id=profile.profile_id,
                pipeline_strategy_id=pipeline.strategy_id,
                output_type=pipeline.output_type,
                time_column=pipeline.time_column,
                output_frame=pipeline.frame,
                selected_strategy_ids=[item.strategy_id for item in profile.enabled_strategies],
                strategy_params={
                    item.strategy_id: dict(item.params)
                    for item in profile.enabled_strategies
                },
                risk_limits=dict(profile.risk),
                market_data_frame=raw_market_df,
                signal_data_frame=signal_market_df,
            )
            state = broker.sync_state()
            sync_result = reconcile_broker_state(
                session,
                broker,
                snapshot=state,
                run_id=run_id,
                profile_id=profile.profile_id,
            )
            execution_symbols = _collect_execution_symbols(pipeline.frame, state)
            execution_reference_prices, execution_price_sources = _resolve_execution_reference_prices(
                broker,
                execution_symbols,
                valuation_prices,
            )
            live_account_attribution = latest_live_account_attribution_summary(
                profile_id=profile.profile_id,
                account=account,
            )
            preflight = build_preflight_result(
                profile=profile,
                raw_market_df=raw_market_df,
                signal_market_df=signal_market_df,
                output_frame=pipeline.frame,
                output_time_column=pipeline.time_column,
                broker_state=state,
                execution_symbols=execution_symbols,
                execution_reference_prices=execution_reference_prices,
                execution_price_sources=execution_price_sources,
                equity=_extract_equity(state.account_values),
                live_account_attribution=live_account_attribution,
            ).to_dict()
            missing_execution_prices = [
                symbol
                for symbol in execution_symbols
                if symbol not in execution_reference_prices
            ]
            run_logger.bind(
                preflight_can_trade=preflight["can_trade"],
                preflight_blocking_failures=preflight["blocking_failure_count"],
                preflight_warning_count=preflight["warning_count"],
            ).info("实盘 preflight 已完成")
            if not preflight["can_trade"]:
                blocked_messages = _preflight_blocked_messages(preflight)
                _record_run_health(
                    session,
                    run_id=run_id,
                    profile_id=profile.profile_id,
                    mode="paper_soak" if broker.get_name() == "paper" else "live_run",
                    broker_name=broker.get_name(),
                    account=account,
                    preflight=preflight,
                    broker_state=state,
                    pipeline=pipeline,
                    plans=[],
                    live_account_attribution=live_account_attribution,
                    extra_details={
                        "sync_result": sync_result,
                        "blocked": True,
                    },
                )
                send_alert(_build_preflight_alert_message(preflight), level="warning")
                run_logger.warning(
                    "实盘 preflight 未通过，blocking_failures=%s，warnings=%s",
                    preflight["blocking_failure_count"],
                    preflight["warning_count"],
                )
                return blocked_messages
            drift = (
                analyze_position_drift(session, pipeline.frame, valuation_prices)
                if pipeline.output_type == StrategyOutputType.TARGET_WEIGHT
                else _empty_drift_result(pipeline.output_type)
            )
            planner = resolve_execution_planner(profile, pipeline.output_type)
            plans = build_execution_plan(
                profile,
                pipeline.frame,
                pipeline.output_type,
                state,
                execution_reference_prices,
                equity=_extract_equity(state.account_values),
            )
            batch_id = f"order-batch-{uuid4().hex[:12]}"
            for idx, plan in enumerate(plans, start=1):
                plan.plan_id = f"{batch_id}-{idx:04d}-{plan.symbol.lower()}"
            planned_order_count = save_execution_plan_records(
                session,
                plans,
                run_id=run_id,
                batch_id=batch_id,
                profile_id=profile.profile_id,
                execution_planner_id=planner.planner_id,
            )
            order_risk_context = _build_order_risk_context(state)
            router = OrderRouter(broker, limits, risk_context=order_risk_context)
            run_logger.info(
                "实盘前检查完成，持仓同步=%s，成交同步=%s，执行计划数=%s，计划快照=%s，执行价来源=%s",
                sync_result["positions_synced"],
                sync_result["fills_synced"],
                len(plans),
                planned_order_count,
                {
                    source: sum(1 for value in execution_price_sources.values() if value == source)
                    for source in sorted(set(execution_price_sources.values()))
                },
            )
            if missing_execution_prices:
                run_logger.warning(
                    "以下标的缺少执行参考价，将不会进入执行计划：%s",
                    ", ".join(missing_execution_prices),
                )
            run_logger.bind(
                execution_planner=planner.planner_id,
                output_type=pipeline.output_type.value,
            ).info("执行计划器已选定，planner_id=%s", planner.planner_id)

            messages: list[str] = []
            chase_executor = (
                LimitChaseExecutor(broker, limits, risk_context=order_risk_context)
                if get_settings().broker == "ibkr"
                else None
            )
            for idx, plan in enumerate(plans, start=1):
                plan_id = plan.plan_id or f"{batch_id}-{idx:04d}-{plan.symbol.lower()}"
                base_order = OrderRequest(
                    strategy_id=plan.strategy_id,
                    symbol=plan.symbol,
                    side=plan.side,
                    qty=round(plan.qty, 6),
                    profile_id=profile.profile_id,
                    target_weight=plan.target_weight,
                    order_type=plan.order_type,
                    limit_price=plan.limit_price,
                    order_semantic=plan.order_semantic,
                    account=account,
                    reason=plan.reason,
                    reference_price=plan.execution_reference_price or plan.latest_price,
                    reference_price_source=execution_price_sources.get(plan.symbol),
                    planned_trade_value=plan.estimated_trade_value,
                    run_id=run_id,
                    batch_id=batch_id,
                    plan_id=plan_id,
                    execution_planner_id=planner.planner_id,
                )
                execution_reference_price = float(
                    plan.execution_reference_price or plan.latest_price or 0.0
                )
                if (
                    get_settings().broker == "ibkr"
                    and chase_executor is not None
                    and base_order.order_type.upper() == "MKT"
                ):
                    chase_result = chase_executor.execute(
                        base_order,
                        reference_price=execution_reference_price,
                    )
                    final_order = chase_result.final_order
                    result = chase_result.final_result
                    save_order_result(
                        session=session,
                        order=final_order,
                        result=result,
                    )
                    messages.append(
                        f"{result.message} | 最终模式={chase_result.final_mode} | 尝试次数={len(chase_result.attempts)}"
                    )
                    run_logger.bind(
                        strategy=final_order.strategy_id,
                        symbol=final_order.symbol,
                        order_semantic=final_order.order_semantic,
                        run_id=run_id,
                        batch_id=batch_id,
                        plan_id=plan_id,
                    ).info(
                        "订单执行完成，symbol=%s，side=%s，status=%s，mode=%s，attempts=%s",
                        final_order.symbol,
                        final_order.side,
                        result.status,
                        chase_result.final_mode,
                        len(chase_result.attempts),
                    )
                else:
                    order = (
                        build_limit_order(base_order, reference_price=execution_reference_price)
                        if get_settings().broker == "ibkr" and base_order.order_type.upper() == "MKT"
                        else base_order
                    )
                    result = router.route(order)
                    save_order_result(
                        session=session,
                        order=order,
                        result=result,
                    )
                    messages.append(result.message)
                    run_logger.bind(
                        strategy=order.strategy_id,
                        symbol=order.symbol,
                        order_semantic=order.order_semantic,
                        run_id=run_id,
                        batch_id=batch_id,
                        plan_id=plan_id,
                    ).info(
                        "订单执行完成，symbol=%s，side=%s，status=%s",
                        order.symbol,
                        order.side,
                        result.status,
                    )

            if messages:
                alert_lines = [
                    "Northstar Quant 已完成本次执行。",
                    f"订单数：{len(messages)}",
                    f"输出类型：{pipeline.output_type.value}",
                    f"同步结果：{sync_result['positions_synced']} 持仓 / {sync_result['fills_synced']} 成交",
                ]
                if pipeline.output_type == StrategyOutputType.TARGET_WEIGHT:
                    alert_lines.append(
                        f"持仓偏离总量：{drift['summary']['total_abs_weight_diff']:.4f}"
                    )
                send_alert("\n".join(alert_lines + messages[:10]), level="info")

            drift_total = float(drift["summary"].get("total_abs_weight_diff", 0.0))
            run_health = _record_run_health(
                session,
                run_id=run_id,
                profile_id=profile.profile_id,
                mode="paper_soak" if broker.get_name() == "paper" else "live_run",
                broker_name=broker.get_name(),
                account=account,
                preflight=preflight,
                broker_state=state,
                pipeline=pipeline,
                plans=plans,
                live_account_attribution=live_account_attribution,
                extra_details={
                    "sync_result": sync_result,
                    "drift_summary": drift["summary"],
                    "message_count": len(messages),
                },
            )
            run_logger.info(
                "实盘主流程结束，订单数=%s，持仓偏离总量=%.4f，run_health_id=%s",
                len(messages),
                drift_total,
                run_health["id"],
            )
            return messages
    finally:
        broker.disconnect()
        run_logger.info("实盘主流程连接已关闭")


def sync_broker_once() -> dict:
    """单独执行一次券商状态同步与对账。"""

    sync_logger = logger.bind(command="live.sync")
    sync_logger.info("开始执行券商状态同步")
    service = IBKRService() if get_settings().broker == "ibkr" else None
    broker = _pick_broker(service)
    broker.connect()
    try:
        with SessionLocal() as session:
            result = reconcile_broker_state(session, broker)
        sync_logger.info(
            "券商状态同步完成，持仓同步=%s，成交同步=%s",
            result["positions_synced"],
            result["fills_synced"],
        )
        return result
    finally:
        broker.disconnect()


def preview_rebalance(profile_id: str | None = None) -> list[dict]:
    """只预览执行计划，不真正下单。"""

    profile = ensure_production_profile(
        load_trading_profile(profile_id),
        context="live.preview-rebalance",
    )
    preview_logger = logger.bind(command="live.preview-rebalance", profile=profile.profile_id)
    preview_logger.info("开始预览执行计划")
    raw_market_df = load_profile_market_data(profile)
    signal_market_df = load_profile_signal_data(profile)
    valuation_prices = _latest_valuation_price_map(raw_market_df)
    pipeline = run_profile_strategy_pipeline(signal_market_df, profile, latest_only=True)
    service = IBKRService() if get_settings().broker == "ibkr" else None
    broker = _pick_broker(service)
    broker.connect()
    try:
        state = broker.sync_state()
        execution_symbols = _collect_execution_symbols(pipeline.frame, state)
        execution_reference_prices, execution_price_sources = _resolve_execution_reference_prices(
            broker,
            execution_symbols,
            valuation_prices,
        )
        missing_execution_prices = [
            symbol
            for symbol in execution_symbols
            if symbol not in execution_reference_prices
        ]
        planner = resolve_execution_planner(profile, pipeline.output_type)
        plans = build_execution_plan(
            profile,
            pipeline.frame,
            pipeline.output_type,
            state,
            execution_reference_prices,
            equity=_extract_equity(state.account_values),
        )
        preview_logger.bind(
            execution_planner=planner.planner_id,
            output_type=pipeline.output_type.value,
            execution_price_sources=execution_price_sources,
        ).info("执行预览完成，计划数=%s", len(plans))
        if missing_execution_prices:
            preview_logger.warning(
                "以下标的缺少执行参考价，将不会进入执行计划：%s",
                ", ".join(missing_execution_prices),
            )
        return [asdict(plan) for plan in plans]
    finally:
        broker.disconnect()


def _extract_equity(account_values: dict) -> float | None:
    """从券商账户摘要中提取账户权益。"""

    for key in ("NetLiquidation", "EquityWithLoanValue", "AvailableFunds"):
        value = account_values.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except Exception:
            continue
    return None


def _extract_available_cash(account_values: dict) -> float | None:
    """从券商账户摘要中提取可用资金。"""

    for key in ("AvailableFunds", "CashBalance", "TotalCashValue", "BuyingPower"):
        value = account_values.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except Exception:
            continue
    return None


def _build_order_risk_context(state: BrokerStateSnapshot) -> OrderRiskContext:
    """把券商状态转成订单路由期间的动态风控上下文。"""

    return OrderRiskContext(
        available_cash=_extract_available_cash(state.account_values),
        position_qty_by_symbol={
            str(item.symbol).strip().upper(): float(item.qty)
            for item in state.positions
        },
    )


def poll_orders_and_fills_once() -> dict:
    """执行一次订单状态轮询与成交回写。"""

    poll_logger = logger.bind(command="live.poll")
    poll_logger.info("开始轮询订单状态与成交")
    service = IBKRService() if get_settings().broker == "ibkr" else None
    broker = _pick_broker(service)
    broker.connect()
    try:
        with SessionLocal() as session:
            result = reconcile_broker_state(session, broker)
        poll_logger.info(
            "订单状态轮询完成，持仓同步=%s，成交同步=%s",
            result["positions_synced"],
            result["fills_synced"],
        )
        return result
    finally:
        broker.disconnect()


def analyze_live_position_drift(profile_id: str | None = None) -> dict:
    """分析当前目标组合与最新真实持仓之间的偏离。"""

    profile = ensure_production_profile(
        load_trading_profile(profile_id),
        context="live.drift",
    )
    drift_logger = logger.bind(command="live.drift", profile=profile.profile_id)
    drift_logger.info("开始分析目标组合与真实持仓偏离")
    raw_market_df = load_profile_market_data(profile)
    signal_market_df = load_profile_signal_data(profile)
    valuation_prices = _latest_valuation_price_map(raw_market_df)
    pipeline = run_profile_strategy_pipeline(signal_market_df, profile, latest_only=True)
    if pipeline.output_type != StrategyOutputType.TARGET_WEIGHT:
        result = _empty_drift_result(pipeline.output_type)
        drift_logger.info("当前画像输出类型=%s，跳过持仓偏离分析", pipeline.output_type.value)
        return result

    with SessionLocal() as session:
        result = analyze_position_drift(session, pipeline.frame, valuation_prices)
    drift_logger.info(
        "持仓偏离分析完成，总偏离=%.4f，最大偏离=%.4f",
        result["summary"]["total_abs_weight_diff"],
        result["summary"]["max_abs_weight_diff"],
    )
    return result


def cancel_stale_orders_once() -> dict:
    """执行一次超时订单撤单。"""

    cancel_logger = logger.bind(command="live.cancel-stale")
    cancel_logger.info("开始执行超时订单撤单")
    service = IBKRService() if get_settings().broker == "ibkr" else None
    broker = _pick_broker(service)
    broker.connect()
    try:
        with SessionLocal() as session:
            result = cancel_stale_orders(session, broker)
        if result["canceled_order_ids"]:
            send_alert(
                f"已撤销超时订单：{', '.join(result['canceled_order_ids'])}",
                level="warning",
            )
        cancel_logger.info("超时订单撤单完成，撤单数=%s", len(result["canceled_order_ids"]))
        return result
    finally:
        broker.disconnect()


def recent_trade_attributions(
    *,
    limit: int = 20,
    profile_id: str | None = None,
    account: str | None = None,
) -> list[dict]:
    """读取最近成交归因，返回可直接序列化的结果。"""

    with SessionLocal() as session:
        rows = list_recent_trade_attributions(
            session,
            limit=limit,
            profile_id=profile_id,
            account=account,
        )
    return [
        {
            "attributed_at": row.attributed_at.isoformat(),
            "profile_id": row.profile_id,
            "account": row.account,
            "run_id": row.run_id,
            "batch_id": row.batch_id,
            "plan_id": row.plan_id,
            "strategy_id": row.strategy_id,
            "execution_planner_id": row.execution_planner_id,
            "symbol": row.symbol,
            "side": row.side,
            "qty": row.qty,
            "fill_price": row.fill_price,
            "reference_price": row.reference_price,
            "reference_price_source": row.reference_price_source,
            "actual_notional": row.actual_notional,
            "reference_notional": row.reference_notional,
            "implementation_shortfall": row.implementation_shortfall,
            "implementation_shortfall_bps": row.implementation_shortfall_bps,
            "order_semantic": row.order_semantic,
            "reason": row.reason,
        }
        for row in rows
    ]


def recent_account_attributions(
    *,
    limit: int = 20,
    profile_id: str | None = None,
    account: str | None = None,
) -> list[dict]:
    """读取最近账户区间归因，返回可直接序列化的结果。"""

    with SessionLocal() as session:
        rows = list_recent_account_attributions(
            session,
            limit=limit,
            profile_id=profile_id,
            account=account,
        )
    return [
        {
            "start_asof": row.start_asof.isoformat(),
            "end_asof": row.end_asof.isoformat(),
            "profile_id": row.profile_id,
            "account": row.account,
            "run_id": row.run_id,
            "starting_equity": row.starting_equity,
            "ending_equity": row.ending_equity,
            "equity_change": row.equity_change,
            "starting_cash": row.starting_cash,
            "ending_cash": row.ending_cash,
            "cash_change": row.cash_change,
            "price_pnl": row.price_pnl,
            "rebalance_pnl": row.rebalance_pnl,
            "execution_shortfall": row.execution_shortfall,
            "dividend_cash_flow": row.dividend_cash_flow,
            "interest_cash_flow": row.interest_cash_flow,
            "fee_cash_flow": row.fee_cash_flow,
            "tax_cash_flow": row.tax_cash_flow,
            "funding_cash_flow": row.funding_cash_flow,
            "corporate_action_cash_flow": row.corporate_action_cash_flow,
            "other_non_trade_cash_flow": row.other_non_trade_cash_flow,
            "total_non_trade_cash_flow": row.total_non_trade_cash_flow,
            "traded_notional": row.traded_notional,
            "fill_count": row.fill_count,
            "residual_pnl": row.residual_pnl,
        }
        for row in rows
    ]


def recent_anomaly_events(
    *,
    limit: int = 20,
    profile_id: str | None = None,
    account: str | None = None,
    alert_tag: str | None = None,
) -> list[dict]:
    """读取最近异常事件，返回可直接序列化的结果。"""

    with SessionLocal() as session:
        rows = list_recent_anomaly_events(
            session,
            limit=limit,
            profile_id=profile_id,
            account=account,
            alert_tag=alert_tag,
        )
    return [
        {
            "detected_at": row.detected_at.isoformat(),
            "profile_id": row.profile_id,
            "account": row.account,
            "run_id": row.run_id,
            "report_type": row.report_type,
            "alert_code": row.alert_code,
            "alert_tag": row.alert_tag,
            "severity": row.severity,
            "summary": row.summary,
            "report_path": row.report_path,
        }
        for row in rows
    ]


def recent_run_health(
    *,
    limit: int = 20,
    profile_id: str | None = None,
    account: str | None = None,
    mode: str | None = None,
) -> list[dict]:
    """读取最近 soak / shadow 运行健康记录。"""

    with SessionLocal() as session:
        rows = list_run_health_records(
            session,
            limit=limit,
            profile_id=profile_id,
            account=account,
            mode=mode,
        )
    return [
        {
            "created_at": row.created_at.isoformat(),
            "run_id": row.run_id,
            "profile_id": row.profile_id,
            "mode": row.mode,
            "broker": row.broker,
            "account": row.account,
            "preflight_can_trade": row.preflight_can_trade,
            "blocking_failure_count": row.blocking_failure_count,
            "warning_count": row.warning_count,
            "target_symbol_count": row.target_symbol_count,
            "target_weight_sum": row.target_weight_sum,
            "execution_plan_count": row.execution_plan_count,
            "planned_trade_value": row.planned_trade_value,
            "plan_consistency_issue_count": row.plan_consistency_issue_count,
            "open_order_count": row.open_order_count,
            "partial_fill_count": row.partial_fill_count,
            "fills_seen_count": row.fills_seen_count,
            "execution_shortfall": row.execution_shortfall,
            "execution_shortfall_bps": row.execution_shortfall_bps,
            "residual_pnl": row.residual_pnl,
            "anomaly_count_trailing_7d": row.anomaly_count_trailing_7d,
            "anomaly_count_prev_7d": row.anomaly_count_prev_7d,
            "anomaly_trend": row.anomaly_trend,
            "details": json.loads(row.details_json) if row.details_json else {},
        }
        for row in rows
    ]


def soak_summary(
    *,
    days: int = 28,
    limit: int = 20,
    profile_id: str | None = None,
    account: str | None = None,
    mode: str | None = None,
) -> dict:
    """汇总最近一段时间的 soak / shadow 运行稳定性。"""

    since = utc_now() - timedelta(days=max(int(days), 1))
    with SessionLocal() as session:
        rows = list_run_health_records(
            session,
            limit=1000,
            profile_id=profile_id,
            account=account,
            mode=mode,
            since=since,
        )
        latest_rows = list_run_health_records(
            session,
            limit=limit,
            profile_id=profile_id,
            account=account,
            mode=mode,
        )
        now = utc_now()
        current_window_start = now - timedelta(days=7)
        previous_window_start = current_window_start - timedelta(days=7)
        anomaly_recent_7d = count_anomaly_events(
            session,
            profile_id=profile_id,
            account=account,
            start_at=current_window_start,
            end_at=now,
        )
        anomaly_prev_7d = count_anomaly_events(
            session,
            profile_id=profile_id,
            account=account,
            start_at=previous_window_start,
            end_at=current_window_start,
        )

    abs_shortfall_bps = [
        abs(float(row.execution_shortfall_bps))
        for row in rows
        if row.execution_shortfall_bps is not None
    ]
    abs_residuals = [
        abs(float(row.residual_pnl))
        for row in rows
        if row.residual_pnl is not None
    ]
    run_count = len(rows)
    return {
        "profile_id": profile_id,
        "account": account,
        "mode": mode or "all",
        "days": int(days),
        "run_count": run_count,
        "preflight_pass_count": sum(1 for row in rows if row.preflight_can_trade),
        "blocked_run_count": sum(1 for row in rows if not row.preflight_can_trade),
        "plan_consistency_issue_run_count": sum(
            1 for row in rows if int(row.plan_consistency_issue_count or 0) > 0
        ),
        "open_order_run_count": sum(1 for row in rows if int(row.open_order_count or 0) > 0),
        "partial_fill_run_count": sum(1 for row in rows if int(row.partial_fill_count or 0) > 0),
        "avg_abs_execution_shortfall_bps": (
            sum(abs_shortfall_bps) / len(abs_shortfall_bps) if abs_shortfall_bps else None
        ),
        "avg_abs_residual_pnl": (
            sum(abs_residuals) / len(abs_residuals) if abs_residuals else None
        ),
        "anomaly_events_recent_7d": anomaly_recent_7d,
        "anomaly_events_prev_7d": anomaly_prev_7d,
        "anomaly_trend": _anomaly_trend(anomaly_recent_7d, anomaly_prev_7d),
        "latest_runs": [
            {
                "created_at": row.created_at.isoformat(),
                "run_id": row.run_id,
                "mode": row.mode,
                "preflight_can_trade": row.preflight_can_trade,
                "execution_plan_count": row.execution_plan_count,
                "plan_consistency_issue_count": row.plan_consistency_issue_count,
                "open_order_count": row.open_order_count,
                "partial_fill_count": row.partial_fill_count,
                "execution_shortfall_bps": row.execution_shortfall_bps,
                "residual_pnl": row.residual_pnl,
                "anomaly_trend": row.anomaly_trend,
            }
            for row in latest_rows
        ],
    }
