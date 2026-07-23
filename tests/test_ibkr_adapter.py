from types import SimpleNamespace

import pytest

from northstar_quant.config.instrument_registry import (
    InstrumentDefinition,
    InstrumentRegistry,
)
from northstar_quant.execution import ibkr_adapter
from northstar_quant.execution import ibkr_contracts
from northstar_quant.execution.models import OrderRequest


def _settings(**overrides):
    values = {
        "broker": "ibkr",
        "kill_switch_enabled": False,
        "live_trading_enabled": True,
        "ibkr_readonly": False,
        "ibkr_account": "DU123456",
        "ibkr_client_id": 7,
        "trading_currency": "USD",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeIB:
    def __init__(
        self,
        *,
        status: str = "Submitted",
        order_id: object = 42,
        qualified_con_id: int = 123,
        qualified_count: int = 1,
    ) -> None:
        self.status = status
        self.order_id = order_id
        self.qualified_con_id = qualified_con_id
        self.qualified_count = qualified_count
        self.qualified_contract = None
        self.placed_contract = None
        self.placed_order = None

    def qualifyContracts(self, contract):
        self.qualified_contract = contract
        contract.conId = self.qualified_con_id
        return [contract] * self.qualified_count

    def placeOrder(self, contract, order):
        self.placed_contract = contract
        self.placed_order = order
        return SimpleNamespace(
            order=SimpleNamespace(orderId=self.order_id),
            orderStatus=SimpleNamespace(status=self.status),
        )

    def sleep(self, _seconds):
        return None


class _FakeService:
    def __init__(self, ib: _FakeIB, settings) -> None:
        self.ib = ib
        self.settings = settings
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False


class _FakeContract:
    def __init__(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeOrder:
    def __init__(self, action: str, qty: float, *args) -> None:
        self.action = action
        self.totalQuantity = qty
        self.args = args


def _order(**overrides) -> OrderRequest:
    values = {
        "strategy_id": "test",
        "symbol": "SPY",
        "side": "BUY",
        "qty": 1.0,
        "account": "DU123456",
        "plan_id": "batch-1-plan-1",
        "attempt_no": 2,
    }
    values.update(overrides)
    return OrderRequest(**values)


def _registry(*, enabled: bool = True, con_id: int = 123) -> InstrumentRegistry:
    return InstrumentRegistry(
        broker="ibkr",
        version=1,
        instruments=(
            InstrumentDefinition(
                data_symbol="SPY",
                broker_symbol="SPY",
                con_id=con_id,
                sec_type="STK",
                exchange="SMART",
                primary_exchange="ARCA",
                currency="USD",
                enabled=enabled,
            ),
        ),
    )


def _build_adapter(
    monkeypatch,
    *,
    status: str = "Submitted",
    order_id: object = 42,
    qualified_con_id: int = 123,
    qualified_count: int = 1,
    registry: InstrumentRegistry | None = None,
):
    settings = _settings()
    fake_ib = _FakeIB(
        status=status,
        order_id=order_id,
        qualified_con_id=qualified_con_id,
        qualified_count=qualified_count,
    )
    service = _FakeService(fake_ib, settings)
    monkeypatch.setattr(ibkr_adapter, "get_settings", lambda: settings)
    monkeypatch.setattr(ibkr_adapter, "load_settings", lambda: settings)
    monkeypatch.setattr(ibkr_contracts, "Contract", _FakeContract)
    monkeypatch.setattr(ibkr_adapter, "MarketOrder", _FakeOrder)
    monkeypatch.setattr(ibkr_adapter, "LimitOrder", _FakeOrder)
    return (
        ibkr_adapter.IBKRBrokerAdapter(
            service,
            instrument_registry=registry or _registry(),
        ),
        fake_ib,
    )


def test_ibkr_adapter_sets_account_and_stable_order_ref(monkeypatch):
    adapter, fake_ib = _build_adapter(monkeypatch)

    result = adapter.submit_order(_order())

    assert result.accepted is True
    assert result.broker_order_id == "42"
    assert fake_ib.placed_order.account == "DU123456"
    assert fake_ib.placed_order.orderRef.startswith("NSQ-")
    assert len(fake_ib.placed_order.orderRef) == 28
    assert fake_ib.placed_contract is fake_ib.qualified_contract
    assert fake_ib.placed_contract.conId == 123
    assert fake_ib.placed_contract.symbol == "SPY"
    assert fake_ib.placed_contract.secType == "STK"
    assert fake_ib.placed_contract.exchange == "SMART"
    assert fake_ib.placed_contract.primaryExchange == "ARCA"
    assert fake_ib.placed_contract.currency == "USD"


def test_ibkr_adapter_reports_rejected_order_as_not_accepted(monkeypatch):
    adapter, _ = _build_adapter(monkeypatch, status="Inactive")

    result = adapter.submit_order(_order())

    assert result.accepted is False
    assert result.status == "Inactive"


def test_ibkr_adapter_never_fabricates_missing_broker_order_id(monkeypatch):
    adapter, _ = _build_adapter(monkeypatch, order_id="")

    with pytest.raises(RuntimeError, match="SUBMISSION_UNKNOWN"):
        adapter.submit_order(_order())


def test_ibkr_adapter_rechecks_kill_switch_inside_adapter(monkeypatch):
    adapter, fake_ib = _build_adapter(monkeypatch)
    monkeypatch.setattr(
        ibkr_adapter,
        "load_settings",
        lambda: _settings(kill_switch_enabled=True),
    )

    with pytest.raises(PermissionError, match="KILL_SWITCH_ENABLED"):
        adapter.submit_order(_order())

    assert fake_ib.placed_order is None


def test_ibkr_adapter_rejects_symbol_without_contract_mapping(monkeypatch):
    adapter, fake_ib = _build_adapter(monkeypatch)

    with pytest.raises(ValueError, match="INSTRUMENT_NOT_CONFIGURED"):
        adapter.submit_order(_order(symbol="510300.SS"))

    assert fake_ib.placed_order is None


def test_ibkr_adapter_rejects_disabled_contract_mapping(monkeypatch):
    adapter, fake_ib = _build_adapter(
        monkeypatch,
        registry=_registry(enabled=False),
    )

    with pytest.raises(ValueError, match="INSTRUMENT_DISABLED"):
        adapter.submit_order(_order())

    assert fake_ib.placed_order is None


def test_ibkr_adapter_rejects_qualified_con_id_mismatch(monkeypatch):
    adapter, fake_ib = _build_adapter(monkeypatch, qualified_con_id=999)

    with pytest.raises(ValueError, match="IBKR_CON_ID_MISMATCH"):
        adapter.submit_order(_order())

    assert fake_ib.placed_order is None


def test_ibkr_adapter_requires_unique_contract_qualification(monkeypatch):
    adapter, fake_ib = _build_adapter(monkeypatch, qualified_count=0)

    with pytest.raises(ValueError, match="qualification 必须唯一"):
        adapter.submit_order(_order())

    assert fake_ib.placed_order is None
