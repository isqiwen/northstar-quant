"""持久化在先的券商提交包装器。

所有真实提交必须先把稳定幂等身份和完整订单意图提交到数据库，再进入券商
适配器。若进程在券商调用附近崩溃，记录会停留在 ``Submitting`` 或
``SubmissionUnknown``，后续调用只允许对账恢复，不允许猜测性重发。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from northstar_quant.platform.common.order_identity import build_order_idempotency_key
from northstar_quant.platform.common.order_status import (
    is_final_order_status,
    is_rejected_order_status,
)
from northstar_quant.platform.db.models import OrderRecord
from northstar_quant.platform.db.repositories import (
    claim_order_submission,
    complete_order_submission,
    finalize_order_cancel_request,
    mark_order_submission_unknown,
    prepare_order_cancel,
    prepare_order_submission,
    renew_execution_lease,
    update_single_order_status,
)
from northstar_quant.trading_execution.broker.broker_base import BrokerAdapter
from northstar_quant.trading_execution.broker.contracts import BrokerStatus, MarketGateway
from northstar_quant.trading_execution.execution.models import (
    BrokerStateSnapshot,
    MarketQuoteSnapshot,
    OrderRequest,
    OrderResult,
)
from northstar_quant.trading_execution.orders.state_machine import (
    BrokerOrderStatus,
    apply_broker_order_status,
    canonicalize_broker_order_status,
)
from northstar_quant.trading_execution.orders.ctp_sim_submission_guard import (
    CtpSimSubmissionAuthority,
)
from northstar_quant.platform.observability.logging.logger import get_logger

logger = get_logger(__name__)


class SubmissionRecoveryRequired(RuntimeError):
    """同一幂等订单处于不确定状态，必须先通过券商对账恢复。"""


@dataclass(frozen=True, slots=True)
class SubmissionLease:
    """提交 CAS 使用的租约与 fencing 身份。"""

    resource_key: str
    owner_token: str
    fencing_token: int
    ttl_seconds: int


def _require_ctp_sim_submission_authority(
    value: object,
) -> CtpSimSubmissionAuthority:
    """Require the opaque CTP-sim authority without a reverse import."""

    if value is None:
        raise PermissionError(
            "CTP_SIM_FINAL_SUBMISSION_AUTHORITY_REQUIRED: "
            "durable CTP-sim submission requires the P8 candidate authority"
        )
    if not isinstance(value, CtpSimSubmissionAuthority):
        raise PermissionError("CTP_SIM_SUBMISSION_AUTHORITY_INVALID")
    return value


class DurableBrokerAdapter(BrokerAdapter):
    """为任意券商适配器增加数据库幂等提交协议。"""

    persists_cancel_intents = True

    def __init__(
        self,
        delegate: BrokerAdapter,
        session: Session,
        *,
        lease: SubmissionLease | None = None,
        cancel_reason: str = "broker_cancel_request",
        cancel_batch_id: str | None = None,
        ctp_sim_submission_authority: CtpSimSubmissionAuthority | None = None,
        before_durable_intent: Callable[[OrderRequest], None] | None = None,
    ) -> None:
        self.delegate = delegate
        self.session = session
        self.lease = lease
        self.cancel_reason = cancel_reason
        self.cancel_batch_id = cancel_batch_id
        self.ctp_sim_submission_authority = ctp_sim_submission_authority
        self.before_durable_intent = before_durable_intent

    def _assert_lease(self) -> None:
        if self.lease is None:
            return
        if not renew_execution_lease(
            self.session,
            resource_key=self.lease.resource_key,
            owner_token=self.lease.owner_token,
            fencing_token=self.lease.fencing_token,
            ttl_seconds=self.lease.ttl_seconds,
        ):
            raise SubmissionRecoveryRequired(
                "EXECUTION_LEASE_LOST: 执行租约已过期或被接管，"
                "禁止继续修改券商订单。"
            )

    def submit_order(self, order: OrderRequest) -> OrderResult:
        """先落意图、原子认领，再调用券商并保存确认结果。"""

        self._assert_lease()
        # 不假设调用方一定经过 OrderRouter。持久化层自己再次补齐最终券商载荷，
        # 保证直接调用 DurableBrokerAdapter 时也不会先落下不完整的 instrument
        # 身份。适配器的 prepare_order 必须是无外部副作用的本地解析。
        order = self.delegate.prepare_order(order)
        broker = str(self.delegate.get_name()).strip().lower()
        account = str(order.account or self.delegate.get_account() or "").strip()
        if not account:
            raise ValueError("持久化提交前必须明确订单账户。")

        submission_guard: Callable[[OrderRequest], None] | None = None
        if broker == "ctp_sim":
            authority = _require_ctp_sim_submission_authority(
                self.ctp_sim_submission_authority
            )
            if (
                getattr(self.delegate, "submission_authority", None)
                is not authority
            ):
                raise PermissionError(
                    "CTP_SIM_AUTHORITY_BINDING_MISMATCH: "
                    "durable and simulator submission authorities must be the same instance"
                )
            submission_guard = authority.reserve

        row, _created = prepare_order_submission(
            self.session,
            order,
            broker=broker,
            account=account,
            before_durable_intent=self.before_durable_intent,
            submission_guard=submission_guard,
        )
        if row.broker_order_id or is_final_order_status(row.status):
            logger.bind(
                command="order.submit.replay",
                broker=broker,
                account=account,
                order_ref=row.order_ref,
                broker_order_id=row.broker_order_id,
            ).warning(
                "命中已持久化订单结果，本次不再向券商重复提交"
            )
            return OrderResult(
                accepted=not is_rejected_order_status(row.status),
                broker_order_id=str(row.broker_order_id or ""),
                status=canonicalize_broker_order_status(row.status).value,
                message="幂等命中：复用已持久化券商订单结果。",
                submitted_at=row.submitted_at,
                replayed=True,
                client_id=row.client_id,
                perm_id=row.perm_id,
            )

        submission_owner = f"submit-{uuid4().hex}"
        if not claim_order_submission(
            self.session,
            order_id=row.id,
            submission_owner=submission_owner,
            lease_resource_key=(
                self.lease.resource_key if self.lease is not None else None
            ),
            lease_owner_token=(
                self.lease.owner_token if self.lease is not None else None
            ),
            lease_fencing_token=(
                self.lease.fencing_token if self.lease is not None else None
            ),
        ):
            raise SubmissionRecoveryRequired(
                "SUBMISSION_RECOVERY_REQUIRED: 同一订单已提交或状态不确定；"
                "必须先同步券商 completed/open orders，禁止重复下单。"
            )

        try:
            result = self.delegate.submit_order(order)
        except Exception as exc:
            mark_order_submission_unknown(
                self.session,
                order_id=row.id,
                submission_owner=submission_owner,
                error=f"{type(exc).__name__}: {exc}",
            )
            logger.bind(
                command="order.submit.unknown",
                broker=broker,
                account=account,
                order_ref=row.order_ref,
            ).exception("券商提交未返回可持久化确认，订单进入待恢复状态")
            raise

        canonical_status = apply_broker_order_status(
            current=BrokerOrderStatus.SUBMITTING,
            broker_status=result.status,
        )
        result = replace(result, status=canonical_status.value)

        complete_order_submission(
            self.session,
            order_id=row.id,
            submission_owner=submission_owner,
            result=result,
        )
        return result

    def prepare_order(self, order: OrderRequest) -> OrderRequest:
        self._assert_lease()
        return self.delegate.prepare_order(order)

    def restore_order_attempt(self, order: OrderRequest) -> OrderRequest:
        """让追价重启复用已持久化 attempt，而不是按新报价重写同一幂等键。"""

        row = self._find_order_attempt(order)
        if row is None:
            return order

        immutable_pairs = (
            ("strategy_id", row.strategy_id, order.strategy_id),
            ("symbol", row.symbol, order.symbol),
            ("side", row.side, order.side),
            ("order_type", row.order_type, order.order_type),
            ("order_semantic", row.order_semantic, order.order_semantic),
            ("ctp_offset", row.ctp_offset, order.ctp_offset),
        )
        for field_name, persisted, regenerated in immutable_pairs:
            if str(persisted or "").strip().upper() != str(
                regenerated or ""
            ).strip().upper():
                raise RuntimeError(
                    "IDEMPOTENCY_CONFLICT: 追价重启的稳定身份字段发生变化，"
                    f"field={field_name}。"
                )

        return replace(
            order,
            strategy_id=row.strategy_id,
            symbol=row.symbol,
            side=row.side,
            qty=float(row.qty),
            profile_id=row.profile_id,
            target_weight=row.target_weight,
            order_type=row.order_type or order.order_type,
            limit_price=row.limit_price,
            order_semantic=row.order_semantic,
            account=row.account,
            reason=row.reason or order.reason,
            reference_price=row.reference_price,
            reference_price_source=row.reference_price_source,
            planned_trade_value=row.planned_trade_value,
            run_id=row.run_id,
            batch_id=row.batch_id,
            plan_id=row.plan_id,
            attempt_no=int(row.attempt_no),
            execution_policy_fingerprint=row.execution_policy_fingerprint,
            execution_planner_id=row.execution_planner_id,
            instrument_id=row.instrument_id,
            exchange_id=row.exchange_id,
            ctp_offset=row.ctp_offset,
            volume_multiple=row.volume_multiple,
            margin_rate=row.margin_rate,
            required_margin=row.required_margin,
            order_ref=row.order_ref,
            currency=row.currency,
        )

    def get_order_attempt_state(self, order: OrderRequest) -> dict | None:
        """返回持久化 attempt 的强身份和单调成交进度。"""

        row = self._find_order_attempt(order)
        if row is None:
            return None
        return self._order_state(row)

    def list_order_plan_attempts(self, order: OrderRequest) -> list[dict]:
        """返回同一 plan 的全部持久化 attempt，供配置漂移校验。"""

        plan_id = str(order.plan_id or "").strip()
        account = str(order.account or self.get_account() or "").strip()
        if not plan_id or not account:
            return []
        broker = str(self.get_name()).strip().lower()
        rows = list(
            self.session.scalars(
                select(OrderRecord)
                .where(
                    OrderRecord.broker == broker,
                    OrderRecord.account == account,
                    OrderRecord.plan_id == plan_id,
                )
                .order_by(OrderRecord.attempt_no.asc(), OrderRecord.id.asc())
            )
        )
        return [
            {
                "local_order_id": row.id,
                "attempt_no": int(row.attempt_no),
                "order_type": row.order_type,
                "execution_policy_fingerprint": (
                    row.execution_policy_fingerprint
                ),
            }
            for row in rows
        ]

    def _find_order_attempt(self, order: OrderRequest) -> OrderRecord | None:
        plan_id = str(order.plan_id or "").strip()
        account = str(order.account or self.get_account() or "").strip()
        if not plan_id or not account:
            return None
        broker = str(self.get_name()).strip().lower()
        idempotency_key = build_order_idempotency_key(
            broker=broker,
            account=account,
            plan_id=plan_id,
            attempt_no=order.attempt_no,
        )
        return self.session.scalar(
            select(OrderRecord).where(
                OrderRecord.broker == broker,
                OrderRecord.account == account,
                OrderRecord.idempotency_key == idempotency_key,
            )
        )

    @staticmethod
    def _order_state(order: OrderRecord) -> dict:
        return {
            "local_order_id": order.id,
            "broker_order_id": str(order.broker_order_id or ""),
            "account": order.account,
            "client_id": order.client_id,
            "order_ref": order.order_ref,
            "perm_id": order.perm_id,
            "instrument_id": order.instrument_id,
            "exchange_id": order.exchange_id,
            "ctp_offset": order.ctp_offset,
            "status": order.status,
            "qty": float(order.qty),
            "filled_qty": order.filled_qty,
            "remaining_qty": order.remaining_qty,
            "execution_policy_fingerprint": (
                order.execution_policy_fingerprint
            ),
        }

    def get_name(self) -> str:
        return self.delegate.get_name()

    def connect(self) -> None:
        self.delegate.connect()

    def disconnect(self) -> None:
        self.delegate.disconnect()

    def sync_state(self) -> BrokerStateSnapshot:
        self._assert_lease()
        return self.delegate.sync_state()

    def get_market_quotes(self, symbols: list[str]) -> list[MarketQuoteSnapshot]:
        self._assert_lease()
        return self.delegate.get_market_quotes(symbols)

    def market_gateway(self) -> MarketGateway:
        """Keep quote reads behind the durable wrapper's lease boundary."""

        return self

    def broker_status(self) -> BrokerStatus:
        """Persistence does not alter broker identity, capability or connection state."""

        return self.delegate.broker_status()

    def cancel_order(self, broker_order_id: str) -> bool:
        return self._cancel_order(broker_order_id)

    def cancel_order_for_local_order(
        self,
        local_order_id: int,
        broker_order_id: str,
    ) -> bool:
        return self._cancel_order(
            broker_order_id,
            local_order_id=local_order_id,
        )

    def _cancel_order(
        self,
        broker_order_id: str,
        *,
        local_order_id: int | None = None,
    ) -> bool:
        self._assert_lease()
        broker = str(self.get_name()).strip().lower()
        account = str(self.get_account() or "").strip()
        cancel_row, created = prepare_order_cancel(
            self.session,
            broker=broker,
            account=account,
            broker_order_id=broker_order_id,
            reason=self.cancel_reason,
            cancel_batch_id=self.cancel_batch_id,
            local_order_id=local_order_id,
            client_id=self.delegate.get_client_id(),
        )
        if not created:
            if cancel_row.status == "PendingCancel":
                return True
            raise SubmissionRecoveryRequired(
                "CANCEL_RECOVERY_REQUIRED: 已存在状态不确定的撤单意图，"
                "必须先完成 completed order 对账，禁止重复撤单。"
            )
        order = (
            self.session.get(OrderRecord, cancel_row.order_id)
            if cancel_row.order_id is not None
            else None
        )
        if order is None:
            raise SubmissionRecoveryRequired(
                "CANCEL_ORDER_IDENTITY_MISSING: 无法读取撤单意图关联订单，"
                "禁止调用券商。"
            )
        accepted = self.delegate.cancel_order_with_identity(
            broker_order_id,
            order_ref=order.order_ref,
            perm_id=order.perm_id,
            client_id=order.client_id,
            instrument_id=order.instrument_id,
            exchange_id=order.exchange_id,
        )
        finalize_order_cancel_request(
            self.session,
            cancel_id=cancel_row.id,
            accepted=accepted,
        )
        return accepted

    def get_order_status(self, broker_order_id: str) -> dict | None:
        self._assert_lease()
        broker = str(self.get_name()).strip().lower()
        account = str(self.get_account() or "").strip()
        client_id = self.delegate.get_client_id()
        order_conditions = [
            OrderRecord.broker == broker,
            OrderRecord.account == account,
            OrderRecord.broker_order_id == str(broker_order_id),
        ]
        if client_id is not None:
            order_conditions.append(OrderRecord.client_id == int(client_id))
        orders = list(
            self.session.scalars(
                select(OrderRecord)
                .where(*order_conditions)
                .order_by(OrderRecord.id.desc())
                .limit(2)
            )
        )
        if len(orders) != 1:
            raise SubmissionRecoveryRequired(
                "ORDER_STATUS_IDENTITY_AMBIGUOUS: 无法用本地券商身份唯一定位"
                "待查询订单，已停止追价。"
            )
        order = orders[0]
        row = self.delegate.get_order_status_with_identity(
            broker_order_id,
            order_ref=order.order_ref,
            perm_id=order.perm_id,
            client_id=order.client_id,
            instrument_id=order.instrument_id,
            exchange_id=order.exchange_id,
        )
        if row is not None:
            canonical_status = apply_broker_order_status(
                current=order.status,
                broker_status=row.get("status"),
            )
            row = {**row, "status": canonical_status.value}
            update_single_order_status(
                self.session,
                row,
                broker=self.get_name(),
                account=self.get_account(),
            )
        return row

    def get_account(self) -> str | None:
        return self.delegate.get_account()

    def get_client_id(self) -> int | None:
        return self.delegate.get_client_id()
