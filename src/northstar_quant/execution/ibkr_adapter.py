"""IBKR 实盘适配器。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from northstar_quant.common.order_identity import build_order_ref
from northstar_quant.common.order_status import (
    is_filled_order_status,
    is_rejected_order_status,
    is_working_order_status,
)
from northstar_quant.common.time import utc_now
from northstar_quant.config.instrument_registry import (
    InstrumentRegistry,
    load_instrument_registry,
)
from northstar_quant.config.settings import get_settings, load_settings
from northstar_quant.execution.broker_base import BrokerAdapter
from northstar_quant.execution.ibkr_contracts import qualify_ibkr_contract
from northstar_quant.execution.ibkr_service import IBKRService
from northstar_quant.execution.models import (
    BrokerStateSnapshot,
    MarketQuoteSnapshot,
    OrderRequest,
    OrderResult,
)
from northstar_quant.logging_.logger import get_logger

LimitOrder: Any = None
MarketOrder: Any = None
try:
    from ib_async import LimitOrder as _LimitOrder
    from ib_async import MarketOrder as _MarketOrder
except Exception:  # pragma: no cover
    pass
else:
    LimitOrder = _LimitOrder
    MarketOrder = _MarketOrder

logger = get_logger(__name__, broker="ibkr")


class IBKRBrokerAdapter(BrokerAdapter):
    """IBKR 券商适配器。

    这一层只关心“把统一订单模型翻译成 IBKR 订单”，
    持仓同步、账户状态、长连接重连等能力交给 IBKRService 处理。
    """

    def __init__(
        self,
        service: IBKRService | None = None,
        instrument_registry: InstrumentRegistry | None = None,
    ) -> None:
        self.settings = get_settings()
        self.service = service or IBKRService(
            instrument_registry=instrument_registry,
        )
        self.account = self.settings.ibkr_account
        self._instrument_registry = instrument_registry

    def connect(self) -> None:
        logger.bind(command="broker.connect").info("开始连接 IBKR")
        self.service.connect()

    def disconnect(self) -> None:
        logger.bind(command="broker.disconnect").info("断开 IBKR 连接")
        self.service.disconnect()

    def prepare_order(self, order: OrderRequest) -> OrderRequest:
        """把正式 instrument 身份写入最终券商载荷。"""

        registry = self._instrument_registry or load_instrument_registry()
        instrument = registry.resolve(order.symbol)
        return replace(
            order,
            broker_symbol=instrument.broker_symbol,
            con_id=instrument.con_id,
            sec_type=instrument.sec_type,
            exchange=instrument.exchange,
            primary_exchange=instrument.primary_exchange,
            currency=instrument.currency,
        )

    def submit_order(self, order: OrderRequest) -> OrderResult:
        order_logger = logger.bind(
            command="broker.submit-order",
            strategy=order.strategy_id,
            symbol=order.symbol,
            order_semantic=order.order_semantic,
        )
        if MarketOrder is None:
            raise RuntimeError("未安装 ib_async，无法发送 IBKR 订单。")

        runtime_settings = load_settings()
        if runtime_settings.broker != "ibkr":
            raise PermissionError("BROKER_CONFIG_CHANGED: 当前运行配置已不再指向 IBKR。")
        if runtime_settings.kill_switch_enabled:
            raise PermissionError("KILL_SWITCH_ENABLED: 交易 kill switch 已开启。")
        if not runtime_settings.live_trading_enabled:
            raise PermissionError("LIVE_TRADING_DISABLED: 真实券商下单开关未开启。")
        if runtime_settings.ibkr_readonly:
            raise PermissionError("IBKR_READONLY: IBKR 连接处于只读模式。")

        target_account = str(runtime_settings.ibkr_account or "").strip()
        if not target_account:
            raise PermissionError("IBKR_ACCOUNT_REQUIRED: 未显式配置目标 IBKR 账户。")
        service_settings = getattr(self.service, "settings", None)
        service_account = str(
            getattr(service_settings, "ibkr_account", target_account) or ""
        ).strip()
        if service_account != target_account:
            raise PermissionError(
                "BROKER_CONFIG_CHANGED: 已连接服务账户与最新配置不一致。"
            )
        service_client_id = int(
            getattr(
                service_settings,
                "ibkr_client_id",
                runtime_settings.ibkr_client_id,
            )
        )
        if service_client_id != int(runtime_settings.ibkr_client_id):
            raise PermissionError(
                "IBKR_CONNECTION_IDENTITY_CHANGED: 运行时 clientId 与当前"
                "连接身份不一致，必须重建连接后再提交。"
            )
        if order.account and str(order.account).strip() != target_account:
            raise PermissionError("ORDER_ACCOUNT_MISMATCH: 订单账户与目标账户不一致。")

        side = order.side.strip().upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("订单方向必须为 BUY 或 SELL")
        order_type = order.order_type.strip().upper()
        if order_type not in {"MKT", "LMT"}:
            raise ValueError("订单类型必须为 MKT 或 LMT")
        plan_id = str(order.plan_id or "").strip()
        if not plan_id:
            raise ValueError("IBKR 订单缺少 plan_id，无法生成可审计的 orderRef。")
        if int(order.attempt_no) < 1:
            raise ValueError("IBKR 订单 attempt_no 必须大于等于 1。")
        order_ref = build_order_ref(plan_id, order.attempt_no)

        # 同一次提交只读取一份 registry 快照，并用同一条 InstrumentDefinition
        # 完成载荷比对和 Contract 构造，避免配置文件在两次读取之间变化造成
        # “数据库记录为 A、实际合约为 B”的 TOCTOU 窗口。
        registry = self._instrument_registry or load_instrument_registry()
        instrument = registry.resolve(order.symbol)
        prepared_order = replace(
            order,
            broker_symbol=instrument.broker_symbol,
            con_id=instrument.con_id,
            sec_type=instrument.sec_type,
            exchange=instrument.exchange,
            primary_exchange=instrument.primary_exchange,
            currency=instrument.currency,
        )
        instrument_fields = (
            "broker_symbol",
            "con_id",
            "sec_type",
            "exchange",
            "primary_exchange",
            "currency",
        )
        for field_name in instrument_fields:
            original_value = getattr(order, field_name)
            if (
                original_value is not None
                and original_value != getattr(prepared_order, field_name)
            ):
                raise PermissionError(
                    "BROKER_INSTRUMENT_CONFIG_CHANGED: 提交前 instrument 映射"
                    f"发生变化，field={field_name}。"
                )
        order = prepared_order
        self.service.connect()
        ib = self.service.ib
        contract = qualify_ibkr_contract(ib, instrument)

        if order_type == "LMT":
            if LimitOrder is None:
                raise RuntimeError("未安装 ib_async，无法发送 IBKR 限价单。")
            if order.limit_price is None:
                raise ValueError("限价单必须提供 limit_price")
            ib_order = LimitOrder(side, order.qty, order.limit_price)
        else:
            ib_order = MarketOrder(side, order.qty)

        setattr(ib_order, "account", target_account)
        setattr(ib_order, "orderRef", order_ref)

        trade = ib.placeOrder(contract, ib_order)
        ib.sleep(1.0)

        raw_order_id = getattr(trade.order, "orderId", None)
        order_id = (
            str(raw_order_id).strip()
            if raw_order_id is not None
            else ""
        )
        status = str(
            getattr(getattr(trade, "orderStatus", None), "status", "") or ""
        ).strip()
        if not order_id:
            raise RuntimeError(
                "SUBMISSION_UNKNOWN: IBKR 未返回真实 orderId，禁止伪造券商订单号。"
            )
        if not (
            is_working_order_status(status)
            or is_filled_order_status(status)
            or is_rejected_order_status(status)
        ):
            raise RuntimeError(
                f"SUBMISSION_UNKNOWN: IBKR 返回无法识别的提交状态 {status!r}。"
            )
        accepted = not is_rejected_order_status(status)
        order_logger.info(
            "IBKR 订单提交结果，status=%s，broker_order_id=%s，order_ref=%s",
            status,
            order_id,
            order_ref,
        )

        return OrderResult(
            accepted=accepted,
            broker_order_id=order_id,
            status=status,
            message=(
                f"IBKR 订单{'已提交' if accepted else '被拒绝'}："
                f"{order.symbol} {order.side} {order.qty}"
            ),
            submitted_at=utc_now(),
            client_id=(
                int(getattr(trade.order, "clientId", 0) or 0)
                if getattr(trade.order, "clientId", None) is not None
                else None
            ),
            perm_id=int(getattr(trade.order, "permId", 0) or 0) or None,
        )

    def sync_state(self) -> BrokerStateSnapshot:
        logger.bind(command="broker.sync-state").info("开始同步 IBKR 状态")
        return self.service.sync_state()

    def get_market_quotes(self, symbols: list[str]) -> list[MarketQuoteSnapshot]:
        logger.bind(command="broker.market-quotes", symbol_count=len(symbols)).info(
            "开始拉取 IBKR 市场报价快照"
        )
        registry = self._instrument_registry or load_instrument_registry()
        return self.service.snapshot_quotes(
            symbols,
            instrument_registry=registry,
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        """向 IBKR 发送撤单请求。"""

        self.service.connect()
        canceled = self.service.cancel_order(broker_order_id)
        logger.bind(command="broker.cancel-order", broker_order_id=broker_order_id).info(
            "IBKR 撤单请求已发送，canceled=%s",
            canceled,
        )
        return canceled

    def cancel_order_with_identity(
        self,
        broker_order_id: str,
        *,
        order_ref: str | None = None,
        perm_id: int | None = None,
        client_id: int | None = None,
        con_id: int | None = None,
    ) -> bool:
        """使用持久化券商身份精确撤单。"""

        self.service.connect()
        canceled = self.service.cancel_order(
            broker_order_id,
            expected_order_ref=order_ref,
            expected_perm_id=perm_id,
            expected_client_id=client_id,
            expected_con_id=con_id,
        )
        logger.bind(
            command="broker.cancel-order",
            broker_order_id=broker_order_id,
            order_ref=order_ref,
            client_id=client_id,
            con_id=con_id,
        ).info("IBKR 精确撤单请求已发送，canceled=%s", canceled)
        return canceled

    def get_order_status(self, broker_order_id: str) -> dict | None:
        """查询 IBKR 单笔订单的最终或工作状态。"""

        return self.service.order_status(broker_order_id)

    def get_order_status_with_identity(
        self,
        broker_order_id: str,
        *,
        order_ref: str | None = None,
        perm_id: int | None = None,
        client_id: int | None = None,
        con_id: int | None = None,
    ) -> dict | None:
        return self.service.order_status(
            broker_order_id,
            expected_order_ref=order_ref,
            expected_perm_id=perm_id,
            expected_client_id=client_id,
            expected_con_id=con_id,
        )

    def get_account(self) -> str | None:
        return str(self.service.settings.ibkr_account or "").strip() or None

    def get_client_id(self) -> int | None:
        return int(self.service.settings.ibkr_client_id)

    def get_name(self) -> str:
        return "ibkr"
