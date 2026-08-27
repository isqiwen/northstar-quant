"""实盘服务批次级失败关闭测试。"""

from datetime import date
from types import SimpleNamespace

import pytest
import polars as pl

import northstar_quant.application.live_service as live_service
from northstar_quant.foundation.common.enums import AssetType
from northstar_quant.trading_execution.execution.models import OrderRequest, OrderResult
from northstar_quant.application.live_service import (
    _assert_p8_ctp_sim_candidate_execution_path,
    _collect_execution_symbols,
    _latest_valuation_price_map,
    _pick_broker,
    _route_order_batch_fail_closed,
)


class _RecordingLogger:
    def bind(self, **_kwargs):
        return self

    def info(self, *_args, **_kwargs):
        return None

    def exception(self, *_args, **_kwargs):
        return None


class _FailingSecondRouter:
    def __init__(self) -> None:
        self.symbols: list[str] = []

    def route(self, order: OrderRequest) -> OrderResult:
        self.symbols.append(order.symbol)
        if len(self.symbols) == 2:
            raise RuntimeError("券商拒绝测试订单")
        return OrderResult(
            accepted=True,
            broker_order_id=f"order-{len(self.symbols)}",
            status="Submitted",
            message=f"accepted:{order.symbol}",
        )


def test_order_batch_stops_after_first_failure_and_reports_remaining_count():
    router = _FailingSecondRouter()
    orders = [
        OrderRequest(
            strategy_id="test",
            symbol=symbol,
            side="BUY",
            qty=1.0,
        )
        for symbol in ("AAA", "BBB", "CCC")
    ]

    messages, halted_reason = _route_order_batch_fail_closed(
        router,
        orders,
        run_logger=_RecordingLogger(),
    )

    assert router.symbols == ["AAA", "BBB"]
    assert messages[0] == "accepted:AAA"
    assert halted_reason is not None
    assert "已停止剩余 1 笔订单" in halted_reason
    assert messages[-1] == halted_reason


def test_latest_valuation_price_is_selected_by_market_date():
    market_df = pl.DataFrame(
        {
            "date": [
                date(2026, 7, 28),
                date(2026, 7, 27),
                date(2026, 7, 28),
            ],
            "symbol": ["AAA", "AAA", "BBB"],
            "close": [12.0, 99.0, 23.0],
        }
    )

    assert _latest_valuation_price_map(market_df) == {
        "AAA": 12.0,
        "BBB": 23.0,
    }


def test_live_service_rejects_continuous_research_symbol_before_ctp_lookup():
    profile = SimpleNamespace(
        asset_type=AssetType.FUTURES,
        futures=SimpleNamespace(contract_authority_id="test-contract-authority"),
    )

    with pytest.raises(ValueError, match="FUTURES_CONTINUOUS_CONTRACT_FORBIDDEN"):
        _collect_execution_symbols(
            profile,
            pl.DataFrame({"symbol": ["RB_CONT"]}),
            SimpleNamespace(positions=(), open_orders=()),
            broker_name="ctp_sim",
        )


def test_application_composition_root_still_rejects_real_ctp_before_connecting(
    monkeypatch,
):
    monkeypatch.setattr(live_service, "get_settings", lambda: SimpleNamespace(broker="ctp"))

    with pytest.raises(NotImplementedError, match="CTP_EXECUTION_ADAPTER_REQUIRED"):
        _pick_broker()


def test_legacy_live_execute_blocks_ctp_sim_before_broker_creation(monkeypatch):
    monkeypatch.setattr(
        live_service,
        "_load_broker_profile",
        lambda *_args, **_kwargs: SimpleNamespace(profile_id="ctp-sim-profile"),
    )
    monkeypatch.setattr(
        live_service,
        "get_settings",
        lambda: SimpleNamespace(broker="ctp_sim", kill_switch_enabled=False),
    )
    monkeypatch.setattr(
        live_service,
        "_pick_broker",
        lambda: pytest.fail("legacy CTP-sim execution must not create a broker"),
    )

    with pytest.raises(PermissionError, match="P8_CTP_SIM_CANDIDATE_GATE_REQUIRED"):
        live_service.execute_latest_targets_once()


def test_p8_ctp_sim_candidate_gate_leaves_paper_execution_path_available():
    _assert_p8_ctp_sim_candidate_execution_path("paper")


def test_sync_broker_once_keeps_ctp_sim_non_submit_path_available(monkeypatch):
    class _CtpSimSyncBroker:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def connect(self) -> None:
            self.calls.append("connect")

        def disconnect(self) -> None:
            self.calls.append("disconnect")

        def get_name(self) -> str:
            return "ctp_sim"

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    broker = _CtpSimSyncBroker()
    monkeypatch.setattr(
        live_service,
        "_load_broker_profile",
        lambda *_args, **_kwargs: SimpleNamespace(profile_id="ctp-sim-profile"),
    )
    monkeypatch.setattr(live_service, "_pick_broker", lambda **_kwargs: broker)
    monkeypatch.setattr(live_service, "SessionLocal", _Session)
    monkeypatch.setattr(
        live_service,
        "reconcile_broker_state",
        lambda _session, observed_broker, *, profile_id: {
            "positions_synced": 0,
            "fills_synced": 0,
            "broker": observed_broker.get_name(),
            "profile_id": profile_id,
        },
    )

    result = live_service.sync_broker_once()

    assert result["broker"] == "ctp_sim"
    assert result["profile_id"] == "ctp-sim-profile"
    assert broker.calls == ["connect", "disconnect"]
