"""实盘主服务。"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import timedelta
from uuid import uuid4

import polars as pl

from northstar_quant.common.enums import AssetType, StrategyOutputType
from northstar_quant.common.order_identity import (
    build_execution_batch_id,
    build_execution_plan_id,
)
from northstar_quant.common.time import ensure_utc, utc_now
from northstar_quant.config.settings import get_settings, load_settings
from northstar_quant.config.ctp_contract_mapping import load_ctp_contract_registry
from northstar_quant.config.trading_profile import (
    ensure_broker_profile,
    load_trading_profile,
)
from northstar_quant.data.downloader import read_profile_manifest
from northstar_quant.data.storage import load_profile_market_data, load_profile_signal_data
from northstar_quant.db.repositories import (
    count_anomaly_events,
    list_execution_recovery_blockers,
    list_recent_anomaly_events,
    list_recent_account_attributions,
    list_run_health_records,
    list_recent_trade_attributions,
    latest_runtime_risk_record,
    release_execution_lease,
    save_runtime_risk_record,
    save_run_health_record,
    save_execution_plan_records,
    save_strategy_run_snapshot,
    try_acquire_execution_lease,
)
from northstar_quant.db.session import SessionLocal
from northstar_quant.execution.models import (
    BrokerStateSnapshot,
    FuturesExecutionRule,
    MarketQuoteSnapshot,
    OrderRequest,
)
from northstar_quant.execution.ctp_sim_broker import CtpSimBrokerAdapter
from northstar_quant.execution.pricing import (
    build_execution_reference_price_map,
    normalize_symbols,
)
from northstar_quant.execution.paper_broker import PaperBrokerAdapter
from northstar_quant.execution.registry import build_execution_plan, resolve_execution_planner
from northstar_quant.execution.router import OrderRouter
from northstar_quant.live.order_management import cancel_stale_orders
from northstar_quant.live.durable_submission import (
    DurableBrokerAdapter,
    SubmissionLease,
)
from northstar_quant.live.preflight import build_preflight_result
from northstar_quant.live.reconciliation import analyze_position_drift, reconcile_broker_state
from northstar_quant.live.runtime_risk import (
    RuntimeRiskAssessment,
    assess_runtime_risk,
    runtime_risk_symbols,
)
from northstar_quant.live.target_service import (
    generate_daily_targets_once,
    load_latest_daily_targets,
)
from northstar_quant.live.trading_calendar import is_trading_session
from northstar_quant.logging_.logger import get_logger
from northstar_quant.monitoring.alerts import AlertLevel, send_alert
from northstar_quant.monitoring.run_health import (
    anomaly_trend,
    soak_summary as _soak_summary,
)
from northstar_quant.reporting.report_builder import latest_live_account_attribution_summary
from northstar_quant.risk.models import OrderRiskContext, SymbolTradeState
from northstar_quant.risk.pretrade import reserve_open_orders_in_context
from northstar_quant.strategies.pipeline import (
    build_profile_risk_limits,
    run_profile_strategy_pipeline,
)

logger = get_logger(__name__)


def _load_broker_profile(profile_id: str | None, *, context: str):
    settings = load_settings()
    return ensure_broker_profile(
        load_trading_profile(profile_id),
        broker=settings.broker,
        context=context,
    )


def _load_data_manifest(profile) -> dict | None:
    """读取数据来源清单；失败时返回空，由真实券商 preflight 阻断。"""

    try:
        return read_profile_manifest(profile.profile_id)
    except (OSError, TypeError, ValueError) as exc:
        logger.bind(profile=profile.profile_id).warning(
            "读取数据 manifest 失败，将按缺少来源清单处理: %s",
            exc,
        )
        return None


def _pipeline_output_asof(pipeline) -> str:
    """提取稳定策略输出周期；缺失时禁止生成随机幂等身份。"""

    time_column = str(pipeline.time_column or "").strip()
    if (
        not time_column
        or pipeline.frame.is_empty()
        or time_column not in pipeline.frame.columns
    ):
        raise RuntimeError(
            "EXECUTION_OUTPUT_ASOF_REQUIRED: 策略输出缺少稳定时间列，"
            "无法生成可重启的订单幂等身份。"
        )
    value = pipeline.frame.get_column(time_column).max()
    if value is None:
        raise RuntimeError(
            "EXECUTION_OUTPUT_ASOF_REQUIRED: 策略输出时间为空，禁止提交订单。"
        )
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat() if callable(isoformat) else value)


def _pick_broker():
    settings = get_settings()
    if settings.broker == "paper":
        return PaperBrokerAdapter()
    if settings.broker == "ctp_sim":
        return CtpSimBrokerAdapter()
    if settings.broker == "ctp":
        raise NotImplementedError(
            "CTP_EXECUTION_ADAPTER_REQUIRED: 已配置 CTP 合约映射，"
            "但尚未实现 CTP 连接、报单和回报状态机。"
        )
    raise ValueError(f"不支持的券商模式：{settings.broker}")


def _live_execution_guard_messages(broker_name: str, *, settings=None) -> list[str]:
    settings = settings or get_settings()
    normalized_broker = broker_name.strip().lower()
    messages: list[str] = []

    if settings.kill_switch_enabled:
        messages.append("KILL_SWITCH_ENABLED: 交易 kill switch 已开启，本次不下单。")

    if normalized_broker != str(settings.broker).strip().lower():
        messages.append(
            "BROKER_CONFIG_CHANGED: 当前已连接券商与最新配置不一致，本次停止下单。"
        )

    if normalized_broker == "ctp" and not settings.live_trading_enabled:
        messages.append(
            "LIVE_TRADING_DISABLED: 真实券商下单开关未开启；"
            "需要显式设置 NORTHSTAR_LIVE_TRADING_ENABLED=true。"
        )
        return messages

    return messages


def _assert_live_submission_allowed(broker_name: str, _order: OrderRequest) -> None:
    """在每次券商提交前重新读取安全开关并执行最后一道门禁。"""

    settings = load_settings()
    messages = _live_execution_guard_messages(
        broker_name,
        settings=settings,
    )
    if messages:
        raise PermissionError(" | ".join(messages))

    normalized_broker = broker_name.strip().lower()
    if normalized_broker == "paper":
        return

    profile = load_trading_profile(_order.profile_id)
    account = str(_order.account or "").strip()
    if not account:
        raise PermissionError(
            "RUNTIME_RISK_ACCOUNT_REQUIRED: 订单缺少账户，无法读取盘中风控结论。"
        )
    with SessionLocal() as session:
        runtime_risk = latest_runtime_risk_record(
            session,
            profile_id=profile.profile_id,
            broker=normalized_broker,
            account=account,
        )
    if runtime_risk is None:
        raise PermissionError(
            "RUNTIME_RISK_REQUIRED: 缺少盘中实时风控结论，禁止提交真实订单。"
        )
    risk_age_seconds = (
        utc_now() - ensure_utc(runtime_risk.checked_at)
    ).total_seconds()
    max_risk_age = settings.runtime_risk_gate_max_age_seconds
    if risk_age_seconds < -5 or risk_age_seconds > max_risk_age:
        raise PermissionError(
            "RUNTIME_RISK_STALE: 盘中实时风控结论已过期或时间异常，"
            f"age_seconds={risk_age_seconds:.1f}，max_age_seconds={max_risk_age}。"
        )
    if not runtime_risk.can_submit:
        raise PermissionError(
            "RUNTIME_RISK_BLOCKED: 最新盘中实时风控结论禁止提交订单。"
        )

    try:
        trading_day = is_trading_session(
            calendar=profile.calendar,
            timezone=profile.timezone,
            require_calendar=True,
        )
    except Exception as exc:
        raise PermissionError(
            "TRADING_CALENDAR_UNAVAILABLE: 无法确认目标市场交易日，本次停止下单。"
        ) from exc
    if not trading_day:
        raise PermissionError(
            "NON_TRADING_DAY: 当前不是目标画像交易日，本次停止下单。"
        )


def _send_alert_best_effort(
    message: str,
    *,
    level: AlertLevel = "info",
) -> None:
    """告警失败不能覆盖已经持久化的交易结果。"""

    try:
        send_alert(message, level=level)
    except Exception:
        logger.bind(command="live.alert", level=level).exception(
            "告警发送失败；交易与运行健康记录已保留。"
        )


def _route_order_batch_fail_closed(
    router: OrderRouter,
    orders: list[OrderRequest],
    *,
    run_logger=logger,
) -> tuple[list[str], str | None]:
    """顺序路由一个订单批次；任一失败立即停止剩余订单并返回可审计原因。"""

    messages: list[str] = []
    for index, order in enumerate(orders, start=1):
        try:
            result = router.route(order)
        except Exception as exc:
            remaining = len(orders) - index
            detail = str(exc).strip() or type(exc).__name__
            halted_reason = (
                "EXECUTION_BATCH_HALTED: "
                f"第 {index} 笔 {order.symbol} 路由失败：{detail}；"
                f"已停止剩余 {remaining} 笔订单。"
            )
            messages.append(halted_reason)
            run_logger.bind(
                strategy=order.strategy_id,
                symbol=order.symbol,
                order_semantic=order.order_semantic,
                run_id=order.run_id,
                batch_id=order.batch_id,
                plan_id=order.plan_id,
            ).exception(halted_reason)
            return messages, halted_reason

        messages.append(result.message)
        run_logger.bind(
            strategy=order.strategy_id,
            symbol=order.symbol,
            order_semantic=order.order_semantic,
            run_id=order.run_id,
            batch_id=order.batch_id,
            plan_id=order.plan_id,
        ).info(
            "订单执行完成，symbol=%s，side=%s，status=%s",
            order.symbol,
            order.side,
            result.status,
        )
    return messages, None


def _latest_valuation_price_map(market_df: pl.DataFrame) -> dict[str, float]:
    time_column = "timestamp" if "timestamp" in market_df.columns else "date"
    latest_rows = (
        market_df.sort(["symbol", time_column])
        .group_by("symbol", maintain_order=True)
        .tail(1)
        .select(["symbol", "close"])
    )
    return {
        str(row["symbol"]).strip().upper(): float(row["close"])
        for row in latest_rows.to_dicts()
    }


def _optional_float(row: dict, *keys: str) -> float | None:
    for key in keys:
        if key not in row:
            continue
        value = row.get(key)
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _optional_suspended_flag(row: dict) -> bool | None:
    for key in ("is_suspended", "suspended", "is_halted", "halted"):
        if key not in row:
            continue
        value = row.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"true", "1", "yes", "y", "suspended", "halted", "停牌"}:
            return True
        if text in {"false", "0", "no", "n", "trading", "active", "正常"}:
            return False

    status = str(row.get("trade_status") or row.get("trading_status") or "").strip().lower()
    if status in {"suspended", "halted", "停牌"}:
        return True
    if status in {"trading", "active", "normal", "正常"}:
        return False
    return None


def _latest_trade_state_by_symbol(market_df: pl.DataFrame) -> dict[str, SymbolTradeState]:
    """从可选行情字段中提取停牌和涨跌停状态。"""

    if market_df.is_empty() or "symbol" not in market_df.columns:
        return {}

    sort_columns = [column for column in ("timestamp", "date") if column in market_df.columns]
    latest_frame = market_df.sort(sort_columns) if sort_columns else market_df
    rows = latest_frame.group_by("symbol").tail(1).to_dicts()
    state_by_symbol: dict[str, SymbolTradeState] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue

        is_suspended = _optional_suspended_flag(row)
        limit_up_price = _optional_float(
            row,
            "limit_up_price",
            "limit_up",
            "upper_limit",
            "up_limit",
        )
        limit_down_price = _optional_float(
            row,
            "limit_down_price",
            "limit_down",
            "lower_limit",
            "down_limit",
        )
        if is_suspended is None and limit_up_price is None and limit_down_price is None:
            continue

        state_by_symbol[symbol] = SymbolTradeState(
            is_suspended=bool(is_suspended),
            limit_up_price=limit_up_price,
            limit_down_price=limit_down_price,
        )
    return state_by_symbol


def _collect_execution_symbols(
    profile,
    output: pl.DataFrame,
    state,
    *,
    broker_name: str,
) -> list[str]:
    output_symbols = output["symbol"].to_list() if "symbol" in output.columns else []
    if profile.asset_type == AssetType.FUTURES:
        futures = profile.futures
        if futures is None:
            raise ValueError("期货画像缺少 futures 配置。")
        registry = load_ctp_contract_registry(
            futures.ctp_contract_mapping_path,
            expected_broker=broker_name,
        )
        output_symbols = [
            (
                registry.resolve_continuous(str(symbol)).require_trading_enabled().data_symbol
                if str(symbol).strip().upper().endswith("_CONT")
                else registry.resolve_data_symbol(str(symbol)).data_symbol
            )
            for symbol in output_symbols
        ]
    position_symbols = [item.symbol for item in state.positions]
    open_order_symbols = [str(row.get("symbol") or "") for row in state.open_orders]
    return normalize_symbols(output_symbols + position_symbols + open_order_symbols)


def _latest_futures_execution_rules(
    market_df: pl.DataFrame,
    profile,
    *,
    broker_name: str,
) -> dict[str, FuturesExecutionRule]:
    """提取映射内具体合约的最新保证金率和限仓快照。"""

    if profile.asset_type != AssetType.FUTURES:
        return {}
    futures = profile.futures
    if futures is None:
        raise ValueError("期货画像缺少 futures 配置。")
    required_columns = {"symbol", "margin_rate"}
    if not required_columns.issubset(market_df.columns):
        missing = ", ".join(sorted(required_columns - set(market_df.columns)))
        raise ValueError(f"FUTURES_DYNAMIC_RULE_REQUIRED: 行情缺少字段 {missing}。")
    registry = load_ctp_contract_registry(
        futures.ctp_contract_mapping_path,
        expected_broker=broker_name,
    )
    time_columns = [
        column for column in ("timestamp", "date") if column in market_df.columns
    ]
    latest = market_df.sort(time_columns) if time_columns else market_df
    rules: dict[str, FuturesExecutionRule] = {}
    for row in latest.group_by("symbol").tail(1).to_dicts():
        symbol = str(row.get("symbol") or "").strip().upper()
        try:
            mapping = registry.resolve_data_symbol(symbol)
        except ValueError:
            continue
        max_lots = row.get("max_position_lots")
        rules[mapping.data_symbol] = FuturesExecutionRule(
            margin_rate=float(row["margin_rate"]),
            max_position_lots=int(max_lots) if max_lots is not None else None,
        )
    return rules


def _resolve_execution_reference_prices(
    broker,
    symbols: list[str],
    valuation_prices: dict[str, float],
) -> tuple[dict[str, float], dict[str, str], list[MarketQuoteSnapshot]]:
    fallback_prices = {
        symbol: valuation_prices[symbol]
        for symbol in symbols
        if symbol in valuation_prices
    }
    seed_quotes = getattr(broker, "seed_market_quotes", None)
    if callable(seed_quotes):
        seed_quotes(fallback_prices, asof=utc_now())
    broker_quotes = broker.get_market_quotes(symbols)
    prices, sources = build_execution_reference_price_map(
        broker_quotes,
        fallback_prices,
    )
    return prices, sources, broker_quotes


def _save_runtime_risk_assessment(
    assessment: RuntimeRiskAssessment,
) -> dict[str, object]:
    payload = assessment.to_dict()
    with SessionLocal() as session:
        save_runtime_risk_record(
            session,
            profile_id=assessment.profile_id,
            broker=assessment.broker,
            account=assessment.account,
            can_submit=assessment.can_submit,
            blocking_failure_count=len(assessment.blocking_checks),
            warning_count=len(assessment.warning_checks),
            checks=[asdict(check) for check in assessment.checks],
            checked_at=assessment.checked_at,
        )
    return payload


def _assess_and_save_runtime_risk(
    *,
    profile,
    broker,
    state: BrokerStateSnapshot,
    quotes: list[MarketQuoteSnapshot],
    target_symbols: list[str],
    account: str | None,
) -> dict[str, object]:
    assessment = assess_runtime_risk(
        profile_id=profile.profile_id,
        broker=broker.get_name(),
        account=account,
        state=state,
        quotes=quotes,
        required_symbols=runtime_risk_symbols(state, target_symbols),
        settings=load_settings(),
    )
    return _save_runtime_risk_assessment(assessment)


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

    profile = _load_broker_profile(profile_id, context="live.preflight")
    raw_market_df = load_profile_market_data(profile)
    signal_market_df = load_profile_signal_data(profile)
    valuation_prices = _latest_valuation_price_map(raw_market_df)
    target_snapshot = load_latest_daily_targets(profile)
    pipeline = target_snapshot.bundle
    broker = _pick_broker()
    broker.connect()
    try:
        state = broker.sync_state()
        execution_symbols = _collect_execution_symbols(
            profile,
            pipeline.frame,
            state,
            broker_name=broker.get_name(),
        )
        (
            execution_reference_prices,
            execution_price_sources,
            broker_quotes,
        ) = _resolve_execution_reference_prices(
            broker,
            execution_symbols,
            valuation_prices,
        )
        account = str(
            state.account
            or broker.get_account()
            or getattr(broker, "account", None)
            or ""
        ).strip() or None
        runtime_risk = _assess_and_save_runtime_risk(
            profile=profile,
            broker=broker,
            state=state,
            quotes=broker_quotes,
            target_symbols=execution_symbols,
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
            available_cash=_extract_available_cash(state.account_values),
            live_account_attribution=latest_live_account_attribution_summary(
                profile_id=profile.profile_id,
                account=account,
            ),
            broker_name=broker.get_name(),
            expected_account=account,
            data_manifest=_load_data_manifest(profile),
            runtime_risk_assessment=runtime_risk,
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
        anomaly_trend=anomaly_trend(anomaly_count_trailing_7d, anomaly_count_prev_7d),
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

    profile = _load_broker_profile(profile_id, context="live.shadow-run")
    shadow_logger = logger.bind(command="live.shadow-run", profile=profile.profile_id)
    shadow_logger.info("开始执行 shadow run")
    raw_market_df = load_profile_market_data(profile)
    signal_market_df = load_profile_signal_data(profile)
    valuation_prices = _latest_valuation_price_map(raw_market_df)

    broker = _pick_broker()
    broker.connect()
    try:
        pipeline = run_profile_strategy_pipeline(signal_market_df, profile, latest_only=True)
        run_id = f"shadow-run-{uuid4().hex}"
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
            account = str(
                state.account
                or broker.get_account()
                or getattr(broker, "account", None)
                or ""
            ).strip() or None
            sync_result = reconcile_broker_state(
                session,
                broker,
                snapshot=state,
                run_id=run_id,
                profile_id=profile.profile_id,
            )
            execution_symbols = _collect_execution_symbols(
                profile,
                pipeline.frame,
                state,
                broker_name=broker.get_name(),
            )
            (
                execution_reference_prices,
                execution_price_sources,
                broker_quotes,
            ) = _resolve_execution_reference_prices(
                broker,
                execution_symbols,
                valuation_prices,
            )
            runtime_risk = _assess_and_save_runtime_risk(
                profile=profile,
                broker=broker,
                state=state,
                quotes=broker_quotes,
                target_symbols=execution_symbols,
                account=account,
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
                available_cash=_extract_available_cash(state.account_values),
                live_account_attribution=live_account_attribution,
                broker_name=broker.get_name(),
                expected_account=account,
                data_manifest=_load_data_manifest(profile),
                runtime_risk_assessment=runtime_risk,
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
                    broker_name=broker.get_name(),
                    futures_rules=_latest_futures_execution_rules(
                        raw_market_df,
                        profile,
                        broker_name=broker.get_name(),
                    ),
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


def execute_latest_targets_once(profile_id: str | None = None) -> list[str]:
    """读取已经冻结的日频目标，并执行一次盘中再平衡。"""

    profile = _load_broker_profile(profile_id, context="live.execute")
    run_logger = logger.bind(command="live.execute", profile=profile.profile_id)
    run_logger.info("开始执行已冻结的日频目标")
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
    target_snapshot = load_latest_daily_targets(profile)
    pipeline = target_snapshot.bundle
    limits = build_profile_risk_limits(profile)
    run_id = f"live-run-{uuid4().hex}"

    broker = _pick_broker()
    account_getter = getattr(broker, "get_account", None)
    account = str(
        (account_getter() if callable(account_getter) else None)
        or getattr(broker, "account", None)

        or ""
    ).strip()
    if not account:
        message = (
            "EXECUTION_ACCOUNT_REQUIRED: 无法确定执行账户，"
            "本次不会连接券商或提交订单。"
        )
        run_logger.error(message)
        send_alert(message, level="warning")
        return [message]

    lease_resource_key = (
        f"live-submit:{broker.get_name().strip().lower()}:{account}"
    )
    with SessionLocal() as lease_session:
        fencing_token = try_acquire_execution_lease(
            lease_session,
            resource_key=lease_resource_key,
            owner_token=run_id,
            ttl_seconds=settings.execution_lease_ttl_seconds,
        )
    if fencing_token is None:
        message = (
            "EXECUTION_LEASE_BUSY: 同一券商账户已有其他进程持有执行租约，"
            "本次不会连接券商或使用可能过期的账户快照。"
        )
        run_logger.warning(message)
        send_alert(message, level="warning")
        return [message]
    lease = SubmissionLease(
        resource_key=lease_resource_key,
        owner_token=run_id,
        fencing_token=fencing_token,
        ttl_seconds=settings.execution_lease_ttl_seconds,
    )

    try:
        broker.connect()
        with SessionLocal() as session:
            state = broker.sync_state()
            sync_result = reconcile_broker_state(
                session,
                broker,
                snapshot=state,
                run_id=run_id,
                profile_id=profile.profile_id,
            )
            execution_symbols = _collect_execution_symbols(
                profile,
                pipeline.frame,
                state,
                broker_name=broker.get_name(),
            )
            (
                execution_reference_prices,
                execution_price_sources,
                broker_quotes,
            ) = _resolve_execution_reference_prices(
                broker,
                execution_symbols,
                valuation_prices,
            )
            runtime_risk = _assess_and_save_runtime_risk(
                profile=profile,
                broker=broker,
                state=state,
                quotes=broker_quotes,
                target_symbols=execution_symbols,
                account=account,
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
                available_cash=_extract_available_cash(state.account_values),
                live_account_attribution=live_account_attribution,
                broker_name=broker.get_name(),
                expected_account=account,
                data_manifest=_load_data_manifest(profile),
                runtime_risk_assessment=runtime_risk,
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
                        "strategy_run_id": target_snapshot.run_id,
                    },
                )
                send_alert(_build_preflight_alert_message(preflight), level="warning")
                run_logger.warning(
                    "实盘 preflight 未通过，blocking_failures=%s，warnings=%s",
                    preflight["blocking_failure_count"],
                    preflight["warning_count"],
                )
                return blocked_messages
            recovery_blockers = list_execution_recovery_blockers(
                session,
                broker=broker.get_name(),
                account=account,
            )
            if recovery_blockers:
                recovery_messages = [
                    "EXECUTION_RECOVERY_REQUIRED: 存在未恢复的提交或撤单状态，"
                    "本次只完成对账，不会提交新订单。",
                    *recovery_blockers,
                ]
                run_logger.error(" | ".join(recovery_messages))
                send_alert("\n".join(recovery_messages), level="warning")
                return recovery_messages
            drift = (
                analyze_position_drift(
                    session,
                    pipeline.frame,
                    valuation_prices,
                    broker=broker.get_name(),
                    account=account,
                    profile_id=profile.profile_id,
                    equity=_extract_equity(state.account_values),
                )
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
                broker_name=broker.get_name(),
                futures_rules=_latest_futures_execution_rules(
                    raw_market_df,
                    profile,
                    broker_name=broker.get_name(),
                ),
            )
            batch_id = build_execution_batch_id(
                broker=broker.get_name(),
                account=account,
                profile_id=profile.profile_id,
                strategy_id=pipeline.strategy_id,
                output_asof=_pipeline_output_asof(pipeline),
            )
            plan_ids: set[str] = set()
            for plan in plans:
                plan.plan_id = build_execution_plan_id(
                    batch_id=batch_id,
                    strategy_id=plan.strategy_id,
                    symbol=plan.symbol,
                    side=plan.side,
                    order_semantic=plan.order_semantic,
                    ctp_offset=plan.ctp_offset,
                )
                if plan.plan_id in plan_ids:
                    raise RuntimeError(
                        "EXECUTION_PLAN_ID_CONFLICT: 同一执行周期出现重复计划身份，"
                        "已禁止下单。"
                    )
                plan_ids.add(plan.plan_id)
            planned_order_count = save_execution_plan_records(
                session,
                plans,
                run_id=run_id,
                batch_id=batch_id,
                profile_id=profile.profile_id,
                execution_planner_id=planner.planner_id,
            )
            order_risk_context = _build_order_risk_context(
                state,
                execution_reference_prices,
                _latest_trade_state_by_symbol(raw_market_df),
            )
            def submission_guard(order: OrderRequest) -> None:
                _assert_live_submission_allowed(broker.get_name(), order)

            durable_broker = DurableBrokerAdapter(
                broker,
                session,
                lease=lease,
            )
            router = OrderRouter(
                durable_broker,
                limits,
                risk_context=order_risk_context,
                submission_guard=submission_guard,
            )
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

            orders: list[OrderRequest] = []
            for idx, plan in enumerate(plans, start=1):
                plan_id = plan.plan_id or f"{batch_id}-{idx:04d}-{plan.symbol.lower()}"
                orders.append(
                    OrderRequest(
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
                        instrument_id=plan.instrument_id,
                        exchange_id=plan.exchange_id,
                        ctp_offset=plan.ctp_offset,
                        volume_multiple=plan.volume_multiple,
                        margin_rate=plan.margin_rate,
                        required_margin=plan.required_margin,
                        currency=profile.currency,
                    )
                )
            messages, batch_halted_reason = _route_order_batch_fail_closed(
                router,
                orders,
                run_logger=run_logger,
            )

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
                    "batch_halted_reason": batch_halted_reason,
                    "strategy_run_id": target_snapshot.run_id,
                },
            )
            if messages:
                alert_lines = [
                    (
                        "Northstar Quant 已停止本次执行批次。"
                        if batch_halted_reason
                        else "Northstar Quant 已完成本次执行。"
                    ),
                    f"订单数：{len(messages)}",
                    f"输出类型：{pipeline.output_type.value}",
                    f"同步结果：{sync_result['positions_synced']} 持仓 / {sync_result['fills_synced']} 成交",
                ]
                if batch_halted_reason:
                    alert_lines.append(batch_halted_reason)
                if pipeline.output_type == StrategyOutputType.TARGET_WEIGHT:
                    alert_lines.append(
                        f"持仓偏离总量：{drift['summary']['total_abs_weight_diff']:.4f}"
                    )
                _send_alert_best_effort(
                    "\n".join(alert_lines + messages[:10]),
                    level="warning" if batch_halted_reason else "info",
                )
            run_logger.info(
                "实盘主流程结束，订单数=%s，持仓偏离总量=%.4f，run_health_id=%s",
                len(messages),
                drift_total,
                run_health["id"],
            )
            return messages
    finally:
        try:
            broker.disconnect()
        finally:
            if lease is not None:
                try:
                    with SessionLocal() as lease_session:
                        released = release_execution_lease(
                            lease_session,
                            resource_key=lease.resource_key,
                            owner_token=lease.owner_token,
                            fencing_token=lease.fencing_token,
                        )
                    if not released:
                        run_logger.error(
                            "执行租约释放失败或已被接管，resource=%s，fencing=%s",
                            lease.resource_key,
                            lease.fencing_token,
                        )
                except Exception:
                    run_logger.exception(
                        "执行租约释放异常；租约将在 TTL 到期后自动失效"
                    )
            run_logger.info("实盘主流程连接已关闭")


def run_live_once(profile_id: str | None = None) -> list[str]:
    """手动串行执行“冻结日频目标 → 盘中执行”完整链路。"""

    target_snapshot = generate_daily_targets_once(profile_id)
    logger.bind(
        command="live.run",
        profile=target_snapshot.profile_id,
        strategy_run_id=target_snapshot.run_id,
    ).info("日频目标已经就绪，继续进入执行层")
    return execute_latest_targets_once(profile_id)


def sync_broker_once() -> dict:
    """单独执行一次券商状态同步与对账。"""

    sync_logger = logger.bind(command="live.sync")
    sync_logger.info("开始执行券商状态同步")
    broker = _pick_broker()
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


def run_runtime_risk_monitor_once(profile_id: str | None = None) -> dict[str, object]:
    """独立轮询一次账户、持仓、保证金和实时行情风险。"""

    profile = _load_broker_profile(profile_id, context="live.risk-check")
    risk_logger = logger.bind(
        command="live.risk-check",
        profile=profile.profile_id,
    )
    broker = _pick_broker()
    broker.connect()
    try:
        state = broker.sync_state()
        raw_market_df = load_profile_market_data(profile)
        valuation_prices = _latest_valuation_price_map(raw_market_df)
        account = str(
            state.account
            or broker.get_account()
            or getattr(broker, "account", None)
            or ""
        ).strip() or None
        try:
            target_snapshot = load_latest_daily_targets(
                profile,
                require_fresh=False,
            )
            target_frame = target_snapshot.bundle.frame
        except RuntimeError:
            target_frame = pl.DataFrame(
                schema={"symbol": pl.String, "target_weight": pl.Float64}
            )
        required_symbols = _collect_execution_symbols(
            profile,
            target_frame,
            state,
            broker_name=broker.get_name(),
        )
        _, _, quotes = _resolve_execution_reference_prices(
            broker,
            required_symbols,
            valuation_prices,
        )
        assessment = assess_runtime_risk(
            profile_id=profile.profile_id,
            broker=broker.get_name(),
            account=account,
            state=state,
            quotes=quotes,
            required_symbols=required_symbols,
            settings=load_settings(),
        )
        with SessionLocal() as session:
            previous = latest_runtime_risk_record(
                session,
                profile_id=profile.profile_id,
                broker=broker.get_name(),
                account=account,
            )
        payload = _save_runtime_risk_assessment(assessment)

        if not assessment.can_submit and (
            previous is None or previous.can_submit
        ):
            _send_alert_best_effort(
                "Northstar Quant 盘中实时风控已阻断新订单。\n"
                + "\n".join(
                    f"- {check.message}"
                    for check in assessment.blocking_checks
                ),
                level="warning",
            )
        elif assessment.can_submit and previous is not None and not previous.can_submit:
            _send_alert_best_effort(
                "Northstar Quant 盘中实时风控已经恢复通过。",
                level="info",
            )

        risk_logger.bind(
            can_submit=assessment.can_submit,
            blocking_failure_count=len(assessment.blocking_checks),
            warning_count=len(assessment.warning_checks),
        ).info("盘中实时风控检查完成")
        return payload
    finally:
        broker.disconnect()


def preview_rebalance(profile_id: str | None = None) -> list[dict]:
    """只预览执行计划，不真正下单。"""

    profile = _load_broker_profile(profile_id, context="live.preview-rebalance")
    preview_logger = logger.bind(command="live.preview-rebalance", profile=profile.profile_id)
    preview_logger.info("开始预览执行计划")
    raw_market_df = load_profile_market_data(profile)
    valuation_prices = _latest_valuation_price_map(raw_market_df)
    pipeline = load_latest_daily_targets(profile).bundle
    broker = _pick_broker()
    broker.connect()
    try:
        state = broker.sync_state()
        execution_symbols = _collect_execution_symbols(
            profile,
            pipeline.frame,
            state,
            broker_name=broker.get_name(),
        )
        (
            execution_reference_prices,
            execution_price_sources,
            _broker_quotes,
        ) = _resolve_execution_reference_prices(
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
            broker_name=broker.get_name(),
            futures_rules=_latest_futures_execution_rules(
                raw_market_df,
                profile,
                broker_name=broker.get_name(),
            ),
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
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed) and parsed > 0:
            return parsed
    return None


def _extract_available_cash(account_values: dict) -> float | None:
    """从券商账户摘要中提取可用资金。"""

    for key in ("AvailableFunds", "CashBalance", "TotalCashValue", "BuyingPower"):
        value = account_values.get(key)
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed) and parsed >= 0:
            return parsed
    return None


def _build_order_risk_context(
    state: BrokerStateSnapshot,
    reference_prices: dict[str, float] | None = None,
    trade_state_by_symbol: dict[str, SymbolTradeState] | None = None,
) -> OrderRiskContext:
    """把券商状态转成订单路由期间的动态风控上下文。"""

    context = OrderRiskContext(
        available_cash=_extract_available_cash(state.account_values),
        position_qty_by_symbol={
            str(item.symbol).strip().upper(): float(item.qty)
            for item in state.positions
        },
        sellable_qty_by_symbol={
            str(item.symbol).strip().upper(): float(item.sellable_qty)
            for item in state.positions
            if item.sellable_qty is not None
        },
        trade_state_by_symbol=trade_state_by_symbol or {},
    )
    reserve_open_orders_in_context(context, state.open_orders, reference_prices)
    return context


def poll_orders_and_fills_once() -> dict:
    """执行一次订单状态轮询与成交回写。"""

    poll_logger = logger.bind(command="live.poll")
    poll_logger.info("开始轮询订单状态与成交")
    broker = _pick_broker()
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

    profile = _load_broker_profile(profile_id, context="live.drift")
    drift_logger = logger.bind(command="live.drift", profile=profile.profile_id)
    drift_logger.info("开始分析目标组合与真实持仓偏离")
    raw_market_df = load_profile_market_data(profile)
    valuation_prices = _latest_valuation_price_map(raw_market_df)
    pipeline = load_latest_daily_targets(profile).bundle
    if pipeline.output_type != StrategyOutputType.TARGET_WEIGHT:
        result = _empty_drift_result(pipeline.output_type)
        drift_logger.info("当前画像输出类型=%s，跳过持仓偏离分析", pipeline.output_type.value)
        return result

    with SessionLocal() as session:
        settings = get_settings()
        account = (
            settings.paper_account
            if settings.broker == "paper"
            else settings.ctp_sim_account
            if settings.broker == "ctp_sim"
            else None
        )
        result = analyze_position_drift(
            session,
            pipeline.frame,
            valuation_prices,
            broker=settings.broker,
            account=account,
            profile_id=profile.profile_id,
        )
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
    settings = get_settings()
    broker = _pick_broker()
    account = str(broker.get_account() or "").strip()
    if not account:
        raise RuntimeError("撤单前无法确定券商账户，已停止操作。")
    owner_token = f"cancel-run-{uuid4().hex}"
    resource_key = (
        f"live-submit:{broker.get_name().strip().lower()}:{account}"
    )
    with SessionLocal() as lease_session:
        fencing_token = try_acquire_execution_lease(
            lease_session,
            resource_key=resource_key,
            owner_token=owner_token,
            ttl_seconds=settings.execution_lease_ttl_seconds,
        )
    if fencing_token is None:
        message = "EXECUTION_LEASE_BUSY: 账户正在执行其他券商写操作，本次不撤单。"
        cancel_logger.warning(message)
        return {
            "broker": broker.get_name(),
            "account": account,
            "stale_order_count": 0,
            "cancel_requested_order_ids": [],
            "canceled_order_ids": [],
            "cancel_record_count": 0,
            "cancel_batch_id": None,
            "blocked_reason": message,
        }
    lease = SubmissionLease(
        resource_key=resource_key,
        owner_token=owner_token,
        fencing_token=fencing_token,
        ttl_seconds=settings.execution_lease_ttl_seconds,
    )
    try:
        broker.connect()
        cancel_batch_id = f"cancel-batch-{uuid4().hex[:12]}"
        with SessionLocal() as session:
            leased_broker = DurableBrokerAdapter(
                broker,
                session,
                lease=lease,
                cancel_reason="stale_order_timeout",
                cancel_batch_id=cancel_batch_id,
            )
            result = cancel_stale_orders(
                session,
                leased_broker,
                cancel_batch_id=cancel_batch_id,
            )
        if result["canceled_order_ids"]:
            send_alert(
                f"已撤销超时订单：{', '.join(result['canceled_order_ids'])}",
                level="warning",
            )
        cancel_logger.info("超时订单撤单完成，撤单数=%s", len(result["canceled_order_ids"]))
        return result
    finally:
        try:
            broker.disconnect()
        finally:
            with SessionLocal() as lease_session:
                released = release_execution_lease(
                    lease_session,
                    resource_key=lease.resource_key,
                    owner_token=lease.owner_token,
                    fencing_token=lease.fencing_token,
                )
            if not released:
                cancel_logger.error("撤单执行租约释放失败或已被接管")


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
            "interest_cash_flow": row.interest_cash_flow,
            "fee_cash_flow": row.fee_cash_flow,
            "tax_cash_flow": row.tax_cash_flow,
            "funding_cash_flow": row.funding_cash_flow,
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
    """兼容原查询入口，并把 live 层注入的会话与时钟传给监控服务。"""

    return _soak_summary(
        days=days,
        limit=limit,
        profile_id=profile_id,
        account=account,
        mode=mode,
        session_factory=SessionLocal,
        now=utc_now(),
    )
