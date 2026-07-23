"""限价单追价执行器。

每一轮追价都必须先确认上一笔订单已经成交或撤销。任何未知状态、撤单失败、
撤单未确认都会立即停止后续提交，避免旧单与新单重叠成交。
"""

from __future__ import annotations

import math
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
from northstar_quant.common.order_identity import build_chase_policy_fingerprint
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
        fallback_mode = self.settings.limit_chase_fallback_mode.lower()
        policy_fingerprint = build_chase_policy_fingerprint(
            max_steps=max_steps,
            fallback_mode=fallback_mode,
            limit_price_offset_bps=self.settings.limit_price_offset_bps,
        )
        base_order = replace(
            base_order,
            execution_policy_fingerprint=policy_fingerprint,
        )
        self._assert_persisted_attempt_config(
            base_order,
            max_steps=max_steps,
            fallback_mode=fallback_mode,
            policy_fingerprint=policy_fingerprint,
        )
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
            limit_order = self.broker.restore_order_attempt(limit_order)
            last_submitted_order = limit_order
            result = self.router.route(limit_order)
            last_result = result
            attempt_state = self.broker.get_order_attempt_state(limit_order)
            submitted_qty = float(limit_order.qty)
            baseline_filled_qty = (
                self._persisted_filled_qty(
                    attempt_state,
                    expected_qty=submitted_qty,
                )
                if result.replayed
                else 0.0
            )
            if result.replayed:
                persisted_remaining_qty = self._persisted_remaining_qty(
                    attempt_state,
                    expected_qty=submitted_qty,
                    filled_qty=baseline_filled_qty,
                )
                if persisted_remaining_qty is not None:
                    # 当前 base_order 来自重连后的最新持仓，历史成交已经反映在
                    # 计划数量中。这里只用已持久化剩余量设置安全上限，不能再次
                    # 从当前 remaining 扣除历史累计成交。
                    remaining_qty = min(
                        remaining_qty,
                        persisted_remaining_qty,
                    )
            attempts.append(
                {
                    "step": step + 1,
                    "mode": "LMT",
                    "qty": float(limit_order.qty),
                    "broker_order_id": result.broker_order_id,
                    "status": result.status,
                    "limit_price": limit_order.limit_price,
                    "reference_price": refreshed_reference_price,
                    "replayed": result.replayed,
                    "baseline_filled_qty": baseline_filled_qty,
                }
            )
            chase_logger.info(
                "已提交追价订单，step=%s，broker_order_id=%s，limit_price=%s，reference_price=%s",
                step + 1,
                result.broker_order_id,
                limit_order.limit_price,
                refreshed_reference_price,
            )

            terminal = self._terminal_replay_state(
                result,
                attempt_state=attempt_state,
                expected_qty=submitted_qty,
            )
            if terminal is None and not result.broker_order_id:
                return self._uncertain_result(
                    order=limit_order,
                    submitted_result=result,
                    attempts=attempts,
                    status="missing_broker_identity",
                    message=(
                        "追价订单缺少可轮询的 broker_order_id，且没有已持久化"
                        "终态，已停止后续下单。"
                    ),
                )
            if terminal is None:
                terminal = self._wait_for_terminal_or_timeout(
                    result.broker_order_id,
                    expected_qty=submitted_qty,
                    expected_identity=attempt_state,
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
                    expected_qty=submitted_qty,
                    expected_identity=attempt_state,
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
                default=submitted_qty if is_filled_order_status(status) else 0.0,
            )
            filled_qty = min(max(filled_qty, 0.0), submitted_qty)
            if result.replayed and filled_qty + 1e-8 < baseline_filled_qty:
                raise RuntimeError(
                    "BROKER_ORDER_PROGRESS_REGRESSION: 重连后券商累计成交量"
                    "小于持久化进度，已停止追价。"
                )
            newly_filled_qty = (
                max(filled_qty - baseline_filled_qty, 0.0)
                if result.replayed
                else filled_qty
            )
            attempts[-1]["status_after_wait"] = status
            attempts[-1]["filled_qty"] = filled_qty
            attempts[-1]["newly_filled_qty"] = newly_filled_qty

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
                submitted_unfilled_qty = max(
                    submitted_qty - filled_qty,
                    0.0,
                )
                if (
                    result.accepted
                    and not result.replayed
                    and submitted_unfilled_qty > 1e-8
                ):
                    # 只有本轮 route 实际新增的预留才归本执行器所有。replay
                    # 没有新增预留，不能从聚合风控上下文误释放其他订单额度。
                    release_order_context(
                        self.risk_context,
                        self._resize_order(
                            limit_order,
                            submitted_unfilled_qty,
                        ),
                    )
                remaining_qty = max(
                    remaining_qty - newly_filled_qty,
                    0.0,
                )
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
                submitted_unfilled_qty = max(
                    submitted_qty - filled_qty,
                    0.0,
                )
                if (
                    result.accepted
                    and not result.replayed
                    and submitted_unfilled_qty > 1e-8
                ):
                    release_order_context(
                        self.risk_context,
                        self._resize_order(
                            limit_order,
                            submitted_unfilled_qty,
                        ),
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
        if fallback_mode == "market":
            remaining_order = self._resize_order(base_order, remaining_qty)
            market_order = replace(
                remaining_order,
                order_type="MKT",
                limit_price=None,
                reason=f"{base_order.reason}_limit_fallback_market",
                attempt_no=max_steps + 1,
            )
            market_order = self.broker.restore_order_attempt(market_order)
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
        expected_identity: dict | None = None,
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
            row = self._find_snapshot_order(
                [*state.open_orders, *state.completed_orders],
                broker_order_id,
                expected_identity=expected_identity,
            )
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
                expected_identity=expected_identity,
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

    def _assert_persisted_attempt_config(
        self,
        base_order: OrderRequest,
        *,
        max_steps: int,
        fallback_mode: str,
        policy_fingerprint: str,
    ) -> None:
        """在任何 route 前拒绝与当前追价 horizon 不兼容的旧 attempt。"""

        persisted_attempts = self.broker.list_order_plan_attempts(base_order)
        expected_types = {
            attempt_no: "LMT"
            for attempt_no in range(1, max_steps + 1)
        }
        if fallback_mode == "market":
            expected_types[max_steps + 1] = "MKT"

        seen_attempts: set[int] = set()
        for row in persisted_attempts:
            persisted_fingerprint = str(
                row.get("execution_policy_fingerprint") or ""
            ).strip()
            if persisted_fingerprint != policy_fingerprint:
                raise RuntimeError(
                    "IDEMPOTENCY_CONFIG_CONFLICT: 已持久化 attempt 的执行"
                    "策略指纹缺失或与当前配置不一致，"
                    f"attempt_no={row.get('attempt_no')!r}，"
                    f"persisted={persisted_fingerprint or 'N/A'}，"
                    f"current={policy_fingerprint}。"
                )
            raw_attempt_no = row.get("attempt_no")
            try:
                attempt_no = int(str(raw_attempt_no).strip())
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    "IDEMPOTENCY_CONFIG_CONFLICT: 持久化 attempt_no "
                    f"无效，value={raw_attempt_no!r}。"
                ) from exc
            if attempt_no < 1 or attempt_no in seen_attempts:
                raise RuntimeError(
                    "IDEMPOTENCY_CONFIG_CONFLICT: 同一 plan 的持久化 "
                    f"attempt_no 非法或重复，attempt_no={attempt_no}。"
                )
            seen_attempts.add(attempt_no)

            expected_order_type = expected_types.get(attempt_no)
            persisted_order_type = str(
                row.get("order_type") or ""
            ).strip().upper()
            if expected_order_type is None:
                raise RuntimeError(
                    "IDEMPOTENCY_CONFIG_CONFLICT: 已持久化 attempt 超出当前"
                    "追价 horizon，"
                    f"attempt_no={attempt_no}，max_steps={max_steps}，"
                    f"fallback_mode={fallback_mode}。"
                )
            if persisted_order_type != expected_order_type:
                raise RuntimeError(
                    "IDEMPOTENCY_CONFIG_CONFLICT: 已持久化 attempt 的订单"
                    "语义与当前配置冲突，"
                    f"attempt_no={attempt_no}，"
                    f"persisted={persisted_order_type or 'N/A'}，"
                    f"expected={expected_order_type}。"
                )

        if seen_attempts:
            highest_attempt = max(seen_attempts)
            missing_attempts = [
                attempt_no
                for attempt_no in range(1, highest_attempt + 1)
                if attempt_no not in seen_attempts
            ]
            if missing_attempts:
                raise RuntimeError(
                    "IDEMPOTENCY_CONFIG_CONFLICT: 同一 plan 的持久化 "
                    f"attempt 序列存在缺口，missing={missing_attempts}。"
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

    @classmethod
    def _terminal_replay_state(
        cls,
        result: OrderResult,
        *,
        attempt_state: dict | None,
        expected_qty: float,
    ) -> dict | None:
        """已持久化终态直接完成恢复，不依赖可能缺失的原始 orderId。"""

        if not result.replayed or not is_final_order_status(result.status):
            return None
        if attempt_state is not None and is_final_order_status(
            attempt_state.get("status")
        ):
            return dict(attempt_state)
        if is_filled_order_status(result.status):
            return {
                "status": result.status,
                "filled_qty": expected_qty,
                "remaining_qty": 0.0,
            }
        if is_rejected_order_status(result.status):
            return {
                "status": result.status,
                "filled_qty": 0.0,
                "remaining_qty": expected_qty,
            }
        # 撤单终态可能包含部分成交；缺少持久化进度时不能猜测为零成交。
        return None

    @classmethod
    def _persisted_filled_qty(
        cls,
        attempt_state: dict | None,
        *,
        expected_qty: float,
    ) -> float:
        if attempt_state is None:
            return 0.0
        value = attempt_state.get("filled_qty")
        if value is None:
            return 0.0
        filled_qty = cls._required_progress_float(
            value,
            field_name="filled_qty",
        )
        if filled_qty > expected_qty + 1e-8:
            raise RuntimeError(
                "PERSISTED_ORDER_PROGRESS_INVALID: filled_qty 超过订单数量。"
            )
        return min(filled_qty, expected_qty)

    @classmethod
    def _persisted_remaining_qty(
        cls,
        attempt_state: dict | None,
        *,
        expected_qty: float,
        filled_qty: float,
    ) -> float | None:
        if attempt_state is None:
            return None
        value = attempt_state.get("remaining_qty")
        if value is None:
            return max(expected_qty - filled_qty, 0.0)
        remaining_qty = cls._required_progress_float(
            value,
            field_name="remaining_qty",
        )
        if remaining_qty > expected_qty + 1e-8:
            raise RuntimeError(
                "PERSISTED_ORDER_PROGRESS_INVALID: remaining_qty 超过订单数量。"
            )
        return min(remaining_qty, expected_qty)

    @staticmethod
    def _required_progress_float(value: object, *, field_name: str) -> float:
        try:
            parsed = float(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "PERSISTED_ORDER_PROGRESS_INVALID: "
                f"{field_name}={value!r}。"
            ) from exc
        if not math.isfinite(parsed) or parsed < 0:
            raise RuntimeError(
                "PERSISTED_ORDER_PROGRESS_INVALID: "
                f"{field_name}={value!r}。"
            )
        return parsed

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
    def _find_snapshot_order(
        order_rows: list[dict],
        broker_order_id: str,
        *,
        expected_identity: dict | None,
    ) -> dict | None:
        """按持久化强身份精确匹配 open/completed snapshot。"""

        if not LimitChaseExecutor._has_strong_order_identity(expected_identity):
            return None
        target = str(broker_order_id)
        candidates = [
            row
            for row in order_rows
            if str(row.get("broker_order_id") or "") == target
        ]
        matches = [
            row
            for row in candidates
            if LimitChaseExecutor._matches_order_identity(
                row,
                expected_identity or {},
            )
        ]
        if len(matches) > 1:
            raise RuntimeError(
                "BROKER_ORDER_IDENTITY_AMBIGUOUS: 券商快照中存在多条"
                "完全相同的持久化订单身份，已停止追价。"
            )
        if len(matches) == 1:
            return matches[0]
        if candidates:
            raise RuntimeError(
                "BROKER_ORDER_IDENTITY_MISMATCH: broker_order_id 相同，"
                "但 clientId/orderRef/permId/conId 与持久化记录不一致。"
            )
        return None

    @classmethod
    def _filled_qty_from_fills(
        cls,
        fills: list,
        broker_order_id: str,
        *,
        expected_identity: dict | None,
    ) -> float:
        """只累计与持久化 clientId/orderRef/permId/conId 精确一致的成交。"""

        if not cls._has_strong_order_identity(expected_identity):
            return 0.0
        target = str(broker_order_id)
        candidates = [
            fill
            for fill in fills
            if str(getattr(fill, "broker_order_id", "") or "") == target
        ]
        matches = [
            fill
            for fill in candidates
            if cls._matches_fill_identity(fill, expected_identity or {})
        ]
        if candidates and not matches:
            raise RuntimeError(
                "BROKER_FILL_IDENTITY_MISMATCH: broker_order_id 相同，"
                "但 clientId/orderRef/permId/conId 与持久化记录不一致。"
            )
        filled_qty = 0.0
        for fill in matches:
            raw_qty = getattr(fill, "qty", 0.0)
            try:
                qty = float(str(raw_qty or 0.0).strip())
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    "BROKER_FILL_PROGRESS_INVALID: "
                    f"qty={raw_qty!r}。"
                ) from exc
            if not math.isfinite(qty) or qty < 0:
                raise RuntimeError(
                    "BROKER_FILL_PROGRESS_INVALID: "
                    f"qty={raw_qty!r}。"
                )
            filled_qty += qty
        return filled_qty

    @staticmethod
    def _has_strong_order_identity(identity: dict | None) -> bool:
        if identity is None:
            return False
        con_id = LimitChaseExecutor._identity_int(identity.get("con_id"))
        return bool(
            str(identity.get("account") or "").strip()
            and identity.get("client_id") is not None
            and str(identity.get("order_ref") or "").strip()
            and con_id is not None
            and con_id > 0
        )

    @classmethod
    def _matches_order_identity(
        cls,
        row: dict,
        expected: dict,
    ) -> bool:
        if str(row.get("account") or "").strip() != str(
            expected.get("account") or ""
        ).strip():
            return False
        if cls._identity_int(row.get("client_id")) != cls._identity_int(
            expected.get("client_id")
        ):
            return False
        if str(row.get("order_ref") or "").strip() != str(
            expected.get("order_ref") or ""
        ).strip():
            return False
        if cls._identity_int(row.get("con_id")) != cls._identity_int(
            expected.get("con_id")
        ):
            return False
        expected_perm_id = cls._identity_int(expected.get("perm_id"))
        return expected_perm_id is None or cls._identity_int(
            row.get("perm_id")
        ) == expected_perm_id

    @classmethod
    def _matches_fill_identity(
        cls,
        fill: object,
        expected: dict,
    ) -> bool:
        if str(getattr(fill, "account", "") or "").strip() != str(
            expected.get("account") or ""
        ).strip():
            return False
        if cls._identity_int(getattr(fill, "client_id", None)) != cls._identity_int(
            expected.get("client_id")
        ):
            return False
        if str(getattr(fill, "order_ref", "") or "").strip() != str(
            expected.get("order_ref") or ""
        ).strip():
            return False
        if cls._identity_int(getattr(fill, "con_id", None)) != cls._identity_int(
            expected.get("con_id")
        ):
            return False
        expected_perm_id = cls._identity_int(expected.get("perm_id"))
        return expected_perm_id is None or cls._identity_int(
            getattr(fill, "perm_id", None)
        ) == expected_perm_id

    @staticmethod
    def _identity_int(value: object) -> int | None:
        if value is None or str(value).strip() == "":
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None
