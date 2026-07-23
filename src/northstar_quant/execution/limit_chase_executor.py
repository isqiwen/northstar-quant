"""限价单追价执行器。

每一轮追价都必须先确认上一笔订单已经成交或撤销。任何未知状态、撤单失败、
撤单未确认都会立即停止后续提交，避免旧单与新单重叠成交。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from northstar_quant.common.order_status import (
    is_cancelled_order_status,
    is_filled_order_status,
    is_final_order_status,
    is_rejected_order_status,
    is_working_order_status,
)
from northstar_quant.config.settings import get_settings
from northstar_quant.execution.broker_base import BrokerAdapter
from northstar_quant.execution.limit_executor import build_limit_order
from northstar_quant.execution.models import OrderRequest, OrderResult
from northstar_quant.execution.pricing import build_execution_reference_price_map
from northstar_quant.execution.router import OrderRouter
from northstar_quant.logging_.logger import get_logger
from northstar_quant.risk.models import OrderRiskContext, RiskLimits
from northstar_quant.risk.pretrade import release_order_context

logger = get_logger(__name__)


@dataclass(slots=True)
class ChaseExecutionResult:
    """追价执行结果。"""

    final_order: OrderRequest
    final_result: OrderResult
    attempts: list[dict]
    final_mode: str


class LimitChaseExecutor:
    """在明确订单终态约束下执行多轮限价追价。"""

    def __init__(
        self,
        broker: BrokerAdapter,
        limits: RiskLimits | None = None,
        risk_context: OrderRiskContext | None = None,
        submission_guard: Callable[[OrderRequest], None] | None = None,
    ) -> None:
        self.broker = broker
        self.limits = limits or RiskLimits()
        self.risk_context = risk_context
        self.router = OrderRouter(
            broker,
            self.limits,
            risk_context=risk_context,
            submission_guard=submission_guard,
        )
        self.settings = get_settings()

    def execute(
        self,
        base_order: OrderRequest,
        reference_price: float,
    ) -> ChaseExecutionResult:
        """执行多轮限价追价；撤单未确认时 fail-closed。"""

        attempts: list[dict] = []
        max_steps = max(1, int(self.settings.limit_chase_max_steps))
        remaining_qty = float(base_order.qty)
        last_submitted_order = base_order
        last_result: OrderResult | None = None
        chase_logger = logger.bind(
            command="execution.limit-chase",
            strategy=base_order.strategy_id,
            symbol=base_order.symbol,
        )
        chase_logger.info("开始执行限价追价，max_steps=%s", max_steps)

        for step in range(max_steps):
            attempt_order = replace(
                self._resize_order(base_order, remaining_qty),
                attempt_no=step + 1,
            )
            refreshed_reference_price = self._resolve_reference_price(
                base_order.symbol,
                fallback_price=reference_price,
            )
            limit_order = build_limit_order(
                attempt_order,
                reference_price=refreshed_reference_price,
                step=step,
            )
            last_submitted_order = limit_order
            result = self.router.route(limit_order)
            last_result = result
            attempts.append(
                {
                    "step": step + 1,
                    "mode": "LMT",
                    "qty": float(limit_order.qty),
                    "broker_order_id": result.broker_order_id,
                    "status": result.status,
                    "limit_price": limit_order.limit_price,
                    "reference_price": refreshed_reference_price,
                }
            )
            chase_logger.info(
                "已提交追价订单，step=%s，broker_order_id=%s，limit_price=%s，reference_price=%s",
                step + 1,
                result.broker_order_id,
                limit_order.limit_price,
                refreshed_reference_price,
            )

            terminal = self._wait_for_terminal_or_timeout(
                result.broker_order_id,
                expected_qty=remaining_qty,
            )
            if terminal is None:
                cancel_requested = self.broker.cancel_order(result.broker_order_id)
                attempts[-1]["cancel_requested"] = cancel_requested
                attempts[-1]["status_after_wait"] = "timeout"
                chase_logger.warning(
                    "追价等待超时，step=%s，broker_order_id=%s，cancel_requested=%s",
                    step + 1,
                    result.broker_order_id,
                    cancel_requested,
                )
                if not cancel_requested:
                    return self._uncertain_result(
                        order=limit_order,
                        submitted_result=result,
                        attempts=attempts,
                        status="cancel_request_failed",
                        message="追价订单撤单请求失败，订单状态不确定，已停止后续下单。",
                    )

                terminal = self._wait_for_terminal_or_timeout(
                    result.broker_order_id,
                    expected_qty=remaining_qty,
                )
                if terminal is None:
                    return self._uncertain_result(
                        order=limit_order,
                        submitted_result=result,
                        attempts=attempts,
                        status="cancel_unconfirmed",
                        message="追价订单撤单未确认，订单状态不确定，已停止后续下单。",
                    )

            status = terminal.get("status")
            filled_qty = self._filled_qty(
                terminal,
                default=remaining_qty if is_filled_order_status(status) else 0.0,
            )
            filled_qty = min(max(filled_qty, 0.0), remaining_qty)
            attempts[-1]["status_after_wait"] = status
            attempts[-1]["filled_qty"] = filled_qty

            if is_filled_order_status(status):
                final = OrderResult(
                    accepted=True,
                    broker_order_id=result.broker_order_id,
                    status=str(status),
                    message=(
                        f"限价追价执行完成：{base_order.symbol} "
                        f"{base_order.side} {base_order.qty}"
                    ),
                    submitted_at=result.submitted_at,
                )
                return ChaseExecutionResult(
                    final_order=limit_order,
                    final_result=final,
                    attempts=attempts,
                    final_mode="limit_filled",
                )

            if is_cancelled_order_status(status):
                unfilled_qty = max(remaining_qty - filled_qty, 0.0)
                if unfilled_qty > 1e-8:
                    # 原订单已为全量预留；只释放确认未成交的剩余部分。
                    release_order_context(
                        self.risk_context,
                        self._resize_order(limit_order, unfilled_qty),
                    )
                remaining_qty = unfilled_qty
                if remaining_qty <= 1e-8:
                    final = OrderResult(
                        accepted=True,
                        broker_order_id=result.broker_order_id,
                        status="Filled",
                        message=(
                            "限价订单在撤单确认前已全部成交："
                            f"{base_order.symbol} {base_order.side} {base_order.qty}"
                        ),
                        submitted_at=result.submitted_at,
                    )
                    return ChaseExecutionResult(
                        final_order=limit_order,
                        final_result=final,
                        attempts=attempts,
                        final_mode="filled_before_cancel",
                    )
                continue

            if is_rejected_order_status(status):
                unfilled_qty = max(remaining_qty - filled_qty, 0.0)
                if unfilled_qty > 1e-8:
                    release_order_context(
                        self.risk_context,
                        self._resize_order(limit_order, unfilled_qty),
                    )
                final = OrderResult(
                    accepted=False,
                    broker_order_id=result.broker_order_id,
                    status=str(status),
                    message=(
                        "限价追价订单被券商拒绝或失效："
                        f"{base_order.symbol} {base_order.side} {remaining_qty}"
                    ),
                    submitted_at=result.submitted_at,
                )
                return ChaseExecutionResult(
                    final_order=limit_order,
                    final_result=final,
                    attempts=attempts,
                    final_mode="rejected",
                )

            return self._uncertain_result(
                order=limit_order,
                submitted_result=result,
                attempts=attempts,
                status="unknown_terminal",
                message=(
                    f"追价订单返回无法确认的状态 {status!r}，"
                    "已停止后续下单并等待对账。"
                ),
            )

        # 只有前面所有限价订单均已确认撤销，才可能进入兜底分支。
        if self.settings.limit_chase_fallback_mode.lower() == "market":
            remaining_order = self._resize_order(base_order, remaining_qty)
            market_order = replace(
                remaining_order,
                order_type="MKT",
                limit_price=None,
                reason=f"{base_order.reason}_limit_fallback_market",
                attempt_no=max_steps + 1,
            )
            result = self.router.route(market_order)
            attempts.append(
                {
                    "step": max_steps + 1,
                    "mode": "MKT",
                    "qty": float(market_order.qty),
                    "broker_order_id": result.broker_order_id,
                    "status": result.status,
                }
            )
            chase_logger.warning("限价追价转市价单兜底执行，status=%s", result.status)
            return ChaseExecutionResult(
                final_order=market_order,
                final_result=result,
                attempts=attempts,
                final_mode="fallback_market",
            )

        final = OrderResult(
            accepted=False,
            broker_order_id=last_result.broker_order_id if last_result is not None else "",
            status="cancelled_after_chase",
            message=(
                "限价追价达到最大轮数，剩余数量已确认撤单："
                f"{base_order.symbol} {base_order.side} {remaining_qty}"
            ),
            submitted_at=last_result.submitted_at if last_result is not None else None,
        )
        chase_logger.warning("限价追价达到最大轮数，最终撤单")
        return ChaseExecutionResult(
            final_order=last_submitted_order,
            final_result=final,
            attempts=attempts,
            final_mode="cancel_after_chase",
        )

    def _wait_for_terminal_or_timeout(
        self,
        broker_order_id: str,
        *,
        expected_qty: float,
    ) -> dict | None:
        """等待订单进入明确终态；无法确认时返回 None。"""

        wait_logger = logger.bind(
            command="execution.limit-chase.wait",
            broker_order_id=broker_order_id,
        )
        deadline = (
            time.time()
            + max(1, int(self.settings.limit_chase_per_step_timeout_seconds))
        )
        sleep_seconds = max(0.2, float(self.settings.limit_chase_sleep_seconds))

        while time.time() < deadline:
            status_row = self.broker.get_order_status(broker_order_id)
            if status_row is not None:
                status = status_row.get("status")
                if is_final_order_status(status):
                    return status_row
                if status and not is_working_order_status(status):
                    return {
                        **status_row,
                        "status": "UnknownTerminal",
                        "raw_status": str(status),
                    }

            state = self.broker.sync_state()
            row = self._find_open_order(state.open_orders, broker_order_id)
            if row is not None:
                status = row.get("status")
                if is_final_order_status(status):
                    return row
                if status and not is_working_order_status(status):
                    return {
                        **row,
                        "status": "UnknownTerminal",
                        "raw_status": str(status),
                    }

            filled_qty = self._filled_qty_from_fills(
                state.fills,
                broker_order_id,
            )
            if filled_qty >= expected_qty - 1e-8:
                wait_logger.info("订单已在成交回报中完整找到，视为完成")
                return {"status": "Filled", "filled_qty": filled_qty}
            if row is None and filled_qty > 1e-8:
                return {
                    "status": "UnknownTerminal",
                    "filled_qty": filled_qty,
                }
            time.sleep(sleep_seconds)

        wait_logger.warning("订单等待终态超时")
        return None

    def _resolve_reference_price(self, symbol: str, fallback_price: float) -> float:
        quotes = self.broker.get_market_quotes([symbol])
        price_map, _ = build_execution_reference_price_map(
            quotes,
            {symbol: fallback_price},
        )
        return float(price_map.get(symbol, fallback_price) or fallback_price)

    @staticmethod
    def _resize_order(order: OrderRequest, qty: float) -> OrderRequest:
        original_qty = abs(float(order.qty))
        resized_qty = max(float(qty), 0.0)
        planned_trade_value = order.planned_trade_value
        if planned_trade_value is not None and original_qty > 1e-12:
            planned_trade_value = (
                abs(float(planned_trade_value))
                * resized_qty
                / original_qty
            )
        return replace(
            order,
            qty=resized_qty,
            planned_trade_value=planned_trade_value,
        )

    @staticmethod
    def _filled_qty(row: dict, *, default: float = 0.0) -> float:
        for key in ("filled_qty", "filled"):
            value = row.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return float(default)

    @staticmethod
    def _uncertain_result(
        *,
        order: OrderRequest,
        submitted_result: OrderResult,
        attempts: list[dict],
        status: str,
        message: str,
    ) -> ChaseExecutionResult:
        logger.bind(
            command="execution.limit-chase",
            broker_order_id=submitted_result.broker_order_id,
        ).error(message)
        return ChaseExecutionResult(
            final_order=order,
            final_result=OrderResult(
                accepted=True,
                broker_order_id=submitted_result.broker_order_id,
                status=status,
                message=message,
                submitted_at=submitted_result.submitted_at,
            ),
            attempts=attempts,
            final_mode="uncertain_stop",
        )

    @staticmethod
    def _find_open_order(
        open_orders: list[dict],
        broker_order_id: str,
    ) -> dict | None:
        target = str(broker_order_id)
        for row in open_orders:
            if str(row.get("broker_order_id") or "") == target:
                return row
        return None

    @staticmethod
    def _filled_qty_from_fills(fills: list, broker_order_id: str) -> float:
        target = str(broker_order_id)
        return sum(
            float(getattr(fill, "qty", 0.0) or 0.0)
            for fill in fills
            if str(getattr(fill, "broker_order_id", "") or "") == target
        )
