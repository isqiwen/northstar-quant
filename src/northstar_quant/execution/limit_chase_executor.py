"""限价单追价执行器。

这是个人量化日频系统里非常实用的一层执行逻辑：
1. 先按较保守的限价提交订单
2. 若在指定时间内未成交，则撤单
3. 重新按更积极的价格追价
4. 达到最大追价轮数后，选择最终撤单或转市价单

注意：
- 这不是机构级智能执行算法
- 但对于 ETF 日频再平衡，这种分层追价机制已经足够实用
- 目标是在“成交概率”和“价格控制”之间做可维护的平衡
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace

from northstar_quant.config.settings import get_settings
from northstar_quant.execution.broker_base import BrokerAdapter
from northstar_quant.execution.limit_executor import build_limit_order
from northstar_quant.execution.models import OrderRequest, OrderResult
from northstar_quant.execution.pricing import build_execution_reference_price_map
from northstar_quant.execution.router import OrderRouter
from northstar_quant.logging_.logger import get_logger
from northstar_quant.risk.models import RiskLimits


FINAL_STATUSES = {
    'Filled', 'Cancelled', 'ApiCancelled', 'Inactive', 'Rejected', 'filled', 'cancelled', 'rejected'
}
OPEN_STATUSES = {
    'PendingSubmit', 'PreSubmitted', 'Submitted', 'ApiPending', 'PendingCancel',
    'PartiallyFilled', 'submitted', 'open', 'partiallyfilled'
}
logger = get_logger(__name__)


@dataclass(slots=True)
class ChaseExecutionResult:
    """追价执行结果。"""

    final_order: OrderRequest
    final_result: OrderResult
    attempts: list[dict]
    final_mode: str


class LimitChaseExecutor:
    """限价单追价执行器。"""

    def __init__(self, broker: BrokerAdapter, limits: RiskLimits | None = None) -> None:
        self.broker = broker
        self.limits = limits or RiskLimits()
        self.router = OrderRouter(broker, self.limits)
        self.settings = get_settings()

    def execute(self, base_order: OrderRequest, reference_price: float) -> ChaseExecutionResult:
        """执行多轮限价追价。

        参数
        ----
        base_order:
            原始统一订单模型。通常是 MKT 意图，但这里会在内部转成限价单。
        reference_price:
            参考价格，一般取最新价。
        """

        attempts: list[dict] = []
        max_steps = max(1, int(self.settings.limit_chase_max_steps))
        last_submitted_order = base_order
        chase_logger = logger.bind(
            command="execution.limit-chase",
            strategy=base_order.strategy_id,
            symbol=base_order.symbol,
        )
        chase_logger.info("开始执行限价追价，max_steps=%s", max_steps)

        for step in range(max_steps):
            refreshed_reference_price = self._resolve_reference_price(
                base_order.symbol,
                fallback_price=reference_price,
            )
            limit_order = build_limit_order(
                base_order,
                reference_price=refreshed_reference_price,
                step=step,
            )
            last_submitted_order = limit_order
            result = self.router.route(limit_order)
            attempts.append({
                'step': step + 1,
                'mode': 'LMT',
                'broker_order_id': result.broker_order_id,
                'status': result.status,
                'limit_price': limit_order.limit_price,
                'reference_price': refreshed_reference_price,
            })
            chase_logger.info(
                "已提交追价订单，step=%s，broker_order_id=%s，limit_price=%s，reference_price=%s",
                step + 1,
                result.broker_order_id,
                limit_order.limit_price,
                refreshed_reference_price,
            )

            terminal = self._wait_for_terminal_or_timeout(result.broker_order_id)
            if terminal is None:
                # 超时仍未完成，尝试撤单后进入下一轮追价。
                canceled = self.broker.cancel_order(result.broker_order_id)
                attempts[-1]['cancel_requested'] = canceled
                attempts[-1]['status_after_wait'] = 'timeout'
                chase_logger.warning(
                    "追价等待超时，step=%s，broker_order_id=%s，cancel_requested=%s",
                    step + 1,
                    result.broker_order_id,
                    canceled,
                )
                continue

            attempts[-1]['status_after_wait'] = terminal.get('status')
            if str(terminal.get('status')) in FINAL_STATUSES and terminal.get('status') not in {'Cancelled', 'ApiCancelled', 'cancelled'}:
                final = OrderResult(
                    accepted=True,
                    broker_order_id=result.broker_order_id,
                    status=str(terminal.get('status')),
                    message=f"限价追价执行完成：{base_order.symbol} {base_order.side} {base_order.qty}",
                    submitted_at=result.submitted_at,
                )
                chase_logger.info(
                    "限价追价执行完成，final_mode=%s，status=%s",
                    'limit_filled',
                    final.status,
                )
                return ChaseExecutionResult(
                    final_order=limit_order,
                    final_result=final,
                    attempts=attempts,
                    final_mode='limit_filled',
                )

        # 走到这里说明所有限价轮次都没有完成。
        if self.settings.limit_chase_fallback_mode.lower() == 'market':
            market_order = replace(
                base_order,
                order_type='MKT',
                limit_price=None,
                reason=f"{base_order.reason}_limit_fallback_market",
            )
            result = self.router.route(market_order)
            attempts.append({
                'step': max_steps + 1,
                'mode': 'MKT',
                'broker_order_id': result.broker_order_id,
                'status': result.status,
            })
            chase_logger.warning("限价追价转市价单兜底执行，status=%s", result.status)
            return ChaseExecutionResult(
                final_order=market_order,
                final_result=result,
                attempts=attempts,
                final_mode='fallback_market',
            )

        final = OrderResult(
            accepted=False,
            broker_order_id='',
            status='cancelled_after_chase',
            message=f"限价追价达到最大轮数，最终撤单：{base_order.symbol} {base_order.side} {base_order.qty}",
            submitted_at=None,
        )
        chase_logger.warning("限价追价达到最大轮数，最终撤单")
        return ChaseExecutionResult(
            final_order=last_submitted_order,
            final_result=final,
            attempts=attempts,
            final_mode='cancel_after_chase',
        )

    def _wait_for_terminal_or_timeout(self, broker_order_id: str) -> dict | None:
        """等待订单进入终态；若超时则返回 None。"""

        wait_logger = logger.bind(command="execution.limit-chase.wait", broker_order_id=broker_order_id)
        deadline = time.time() + max(1, int(self.settings.limit_chase_per_step_timeout_seconds))
        sleep_seconds = max(0.2, float(self.settings.limit_chase_sleep_seconds))

        while time.time() < deadline:
            state = self.broker.sync_state()
            row = self._find_open_order(state.open_orders, broker_order_id)
            if row is None:
                # 不在未完成订单中，优先检查近期成交；若找到则视为已成交。
                fill = self._find_fill(state.fills, broker_order_id)
                if fill is not None:
                    wait_logger.info("订单已在成交回报中找到，视为完成")
                    return {'status': 'Filled', 'filled_qty': fill.qty, 'price': fill.price}
                # 既不在 open orders，也暂无 fills。对个人系统来说保守返回已离开 open book。
                wait_logger.warning("订单已离开 open orders，返回 UnknownTerminal")
                return {'status': 'UnknownTerminal'}

            status = str(row.get('status') or 'open')
            if status in FINAL_STATUSES or status not in OPEN_STATUSES:
                wait_logger.info("订单进入终态，status=%s", status)
                return row
            time.sleep(sleep_seconds)

        wait_logger.warning("订单等待超时")
        return None

    def _resolve_reference_price(self, symbol: str, fallback_price: float) -> float:
        quotes = self.broker.get_market_quotes([symbol])
        price_map, _ = build_execution_reference_price_map(
            quotes,
            {symbol: fallback_price},
        )
        return float(price_map.get(symbol, fallback_price) or fallback_price)

    @staticmethod
    def _find_open_order(open_orders: list[dict], broker_order_id: str) -> dict | None:
        target = str(broker_order_id)
        for row in open_orders:
            if str(row.get('broker_order_id') or '') == target:
                return row
        return None

    @staticmethod
    def _find_fill(fills: list, broker_order_id: str):
        target = str(broker_order_id)
        for fill in fills:
            if str(getattr(fill, 'broker_order_id', '') or '') == target:
                return fill
        return None
