"""实盘主服务。"""

from __future__ import annotations

from dataclasses import asdict

import polars as pl

from northstar_quant.common.enums import StrategyOutputType
from northstar_quant.common.types import StrategyOutputBundle
from northstar_quant.config.settings import get_settings
from northstar_quant.config.trading_profile import TradingProfile, load_trading_profile
from northstar_quant.data.storage import load_profile_market_data, load_profile_signal_data
from northstar_quant.db.repositories import save_order_result
from northstar_quant.db.session import SessionLocal
from northstar_quant.execution.ibkr_adapter import IBKRBrokerAdapter
from northstar_quant.execution.limit_chase_executor import LimitChaseExecutor
from northstar_quant.execution.limit_executor import build_limit_order
from northstar_quant.execution.models import OrderRequest
from northstar_quant.execution.paper_broker import PaperBrokerAdapter
from northstar_quant.execution.reconciliation import analyze_position_drift, reconcile_broker_state
from northstar_quant.execution.registry import build_execution_plan, resolve_execution_planner
from northstar_quant.execution.router import OrderRouter
from northstar_quant.live.ibkr_service import IBKRService
from northstar_quant.live.order_management import cancel_stale_orders
from northstar_quant.logging_.logger import get_logger
from northstar_quant.monitoring.alerts import send_alert
from northstar_quant.portfolio.allocator import normalize_weights
from northstar_quant.portfolio.multi_strategy import (
    combine_strategy_execution_intents,
    combine_strategy_targets,
)
from northstar_quant.risk.global_risk import enforce_global_risk
from northstar_quant.risk.models import RiskLimits
from northstar_quant.risk.strategy_risk import enforce_strategy_risk
from northstar_quant.strategies.registry import build_profile_strategies

logger = get_logger(__name__)


def _pick_broker(service: IBKRService | None = None):
    settings = get_settings()
    if settings.broker == "ibkr":
        return IBKRBrokerAdapter(service=service)
    return PaperBrokerAdapter()


def _build_risk_limits(profile: TradingProfile) -> RiskLimits:
    supported_fields = set(RiskLimits.__dataclass_fields__)
    risk_overrides = {
        key: value
        for key, value in profile.risk.items()
        if key in supported_fields
    }
    return RiskLimits(**risk_overrides)


def _latest_close_map(market_df: pl.DataFrame) -> dict[str, float]:
    return {
        row["symbol"]: float(row["close"])
        for row in market_df.group_by("symbol").tail(1).select(["symbol", "close"]).to_dicts()
    }


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


def _run_strategy_pipeline(market_df: pl.DataFrame, profile: TradingProfile) -> StrategyOutputBundle:
    """运行交易画像中的启用策略，并得到最终组合输出。"""

    strategies = build_profile_strategies(profile)
    if not strategies:
        raise ValueError(f"交易画像 {profile.profile_id} 未配置任何启用中的策略。")

    output_types = {strategy.output_type for strategy, _ in strategies}
    if len(output_types) != 1:
        output_text = ", ".join(sorted(item.value for item in output_types))
        raise ValueError(
            f"交易画像 {profile.profile_id} 同时包含多种策略输出类型：{output_text}。"
            "当前版本暂不支持同一画像混用 target_weight 和 execution_intent。"
        )

    output_type = next(iter(output_types))
    time_column = strategies[0][0].time_column
    latest_frames: list[pl.DataFrame] = []
    weights: list[float] = []

    for strategy, capital_weight in strategies:
        output = strategy.build_output_bundle(market_df).frame
        latest_output = strategy.latest_output(output)
        if latest_output.is_empty():
            continue
        if "strategy_id" not in latest_output.columns:
            latest_output = latest_output.with_columns(pl.lit(strategy.strategy_id).alias("strategy_id"))
        latest_frames.append(latest_output)
        weights.append(capital_weight)

    if output_type == StrategyOutputType.TARGET_WEIGHT:
        combined = combine_strategy_targets(latest_frames, weights)
        limits = _build_risk_limits(profile)
        combined = enforce_strategy_risk(combined, limits)
        combined = enforce_global_risk(combined, limits)
        combined = normalize_weights(combined)
    else:
        combined = combine_strategy_execution_intents(
            latest_frames,
            weights,
            time_column=time_column,
        )

    logger.bind(
        command="strategy.pipeline",
        profile=profile.profile_id,
        output_type=output_type.value,
    ).info("策略流水线执行完成，输出记录数=%s", combined.height)
    return StrategyOutputBundle(
        strategy_id="portfolio",
        output_type=output_type,
        time_column=time_column,
        frame=combined,
    )


def run_live_once(profile_id: str | None = None) -> list[str]:
    """运行一次完整实盘主流程。"""

    profile = load_trading_profile(profile_id)
    run_logger = logger.bind(command="live.run", profile=profile.profile_id)
    run_logger.info("开始执行一次实盘主流程")
    raw_market_df = load_profile_market_data(profile)
    signal_market_df = load_profile_signal_data(profile)
    latest_prices = _latest_close_map(raw_market_df)

    service = IBKRService() if get_settings().broker == "ibkr" else None
    broker = _pick_broker(service)
    broker.connect()
    try:
        pipeline = _run_strategy_pipeline(signal_market_df, profile)
        limits = _build_risk_limits(profile)
        with SessionLocal() as session:
            sync_result = reconcile_broker_state(session, broker)
            state = broker.sync_state()
            drift = (
                analyze_position_drift(session, pipeline.frame, latest_prices)
                if pipeline.output_type == StrategyOutputType.TARGET_WEIGHT
                else _empty_drift_result(pipeline.output_type)
            )
            planner = resolve_execution_planner(profile, pipeline.output_type)
            plans = build_execution_plan(
                profile,
                pipeline.frame,
                pipeline.output_type,
                state.positions,
                latest_prices,
                equity=_extract_equity(state.account_values),
            )
            router = OrderRouter(broker, limits)
            run_logger.info(
                "实盘前检查完成，持仓同步=%s，成交同步=%s，执行计划数=%s",
                sync_result["positions_synced"],
                sync_result["fills_synced"],
                len(plans),
            )
            run_logger.bind(
                execution_planner=planner.planner_id,
                output_type=pipeline.output_type.value,
            ).info("执行计划器已选定，planner_id=%s", planner.planner_id)

            messages: list[str] = []
            chase_executor = LimitChaseExecutor(broker, limits) if get_settings().broker == "ibkr" else None
            for plan in plans:
                base_order = OrderRequest(
                    strategy_id=plan.strategy_id,
                    symbol=plan.symbol,
                    side=plan.side,
                    qty=round(plan.qty, 6),
                    target_weight=plan.target_weight,
                    order_type=plan.order_type,
                    limit_price=plan.limit_price,
                    order_semantic=plan.order_semantic,
                    account=get_settings().ibkr_account,
                    reason=plan.reason,
                )
                if (
                    get_settings().broker == "ibkr"
                    and chase_executor is not None
                    and base_order.order_type.upper() == "MKT"
                ):
                    chase_result = chase_executor.execute(
                        base_order,
                        reference_price=float(plan.latest_price or 0.0),
                    )
                    result = chase_result.final_result
                    save_order_result(
                        session=session,
                        strategy_id=base_order.strategy_id,
                        symbol=base_order.symbol,
                        side=base_order.side,
                        qty=base_order.qty,
                        target_weight=base_order.target_weight,
                        order_semantic=base_order.order_semantic,
                        broker_order_id=result.broker_order_id,
                        status=result.status,
                    )
                    messages.append(
                        f"{result.message} | 最终模式={chase_result.final_mode} | 尝试次数={len(chase_result.attempts)}"
                    )
                    run_logger.bind(
                        strategy=base_order.strategy_id,
                        symbol=base_order.symbol,
                        order_semantic=base_order.order_semantic,
                    ).info(
                        "订单执行完成，symbol=%s，side=%s，status=%s，mode=%s，attempts=%s",
                        base_order.symbol,
                        base_order.side,
                        result.status,
                        chase_result.final_mode,
                        len(chase_result.attempts),
                    )
                else:
                    order = (
                        build_limit_order(base_order, reference_price=float(plan.latest_price or 0.0))
                        if get_settings().broker == "ibkr" and base_order.order_type.upper() == "MKT"
                        else base_order
                    )
                    result = router.route(order)
                    save_order_result(
                        session=session,
                        strategy_id=order.strategy_id,
                        symbol=order.symbol,
                        side=order.side,
                        qty=order.qty,
                        target_weight=order.target_weight,
                        order_semantic=order.order_semantic,
                        broker_order_id=result.broker_order_id,
                        status=result.status,
                    )
                    messages.append(result.message)
                    run_logger.bind(
                        strategy=order.strategy_id,
                        symbol=order.symbol,
                        order_semantic=order.order_semantic,
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
            run_logger.info(
                "实盘主流程结束，订单数=%s，持仓偏离总量=%.4f",
                len(messages),
                drift_total,
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

    profile = load_trading_profile(profile_id)
    preview_logger = logger.bind(command="live.preview-rebalance", profile=profile.profile_id)
    preview_logger.info("开始预览执行计划")
    raw_market_df = load_profile_market_data(profile)
    signal_market_df = load_profile_signal_data(profile)
    latest_prices = _latest_close_map(raw_market_df)
    pipeline = _run_strategy_pipeline(signal_market_df, profile)
    service = IBKRService() if get_settings().broker == "ibkr" else None
    broker = _pick_broker(service)
    broker.connect()
    try:
        state = broker.sync_state()
        planner = resolve_execution_planner(profile, pipeline.output_type)
        plans = build_execution_plan(
            profile,
            pipeline.frame,
            pipeline.output_type,
            state.positions,
            latest_prices,
            equity=_extract_equity(state.account_values),
        )
        preview_logger.bind(
            execution_planner=planner.planner_id,
            output_type=pipeline.output_type.value,
        ).info("执行预览完成，计划数=%s", len(plans))
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

    profile = load_trading_profile(profile_id)
    drift_logger = logger.bind(command="live.drift", profile=profile.profile_id)
    drift_logger.info("开始分析目标组合与真实持仓偏离")
    raw_market_df = load_profile_market_data(profile)
    signal_market_df = load_profile_signal_data(profile)
    latest_prices = _latest_close_map(raw_market_df)
    pipeline = _run_strategy_pipeline(signal_market_df, profile)
    if pipeline.output_type != StrategyOutputType.TARGET_WEIGHT:
        result = _empty_drift_result(pipeline.output_type)
        drift_logger.info("当前画像输出类型=%s，跳过持仓偏离分析", pipeline.output_type.value)
        return result

    with SessionLocal() as session:
        result = analyze_position_drift(session, pipeline.frame, latest_prices)
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
