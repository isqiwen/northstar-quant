from datetime import timedelta

import pytest

from northstar_quant.common.time import utc_now
from northstar_quant.config.settings import get_settings
from northstar_quant.execution.ctp_sim_broker import CtpSimBrokerAdapter
from northstar_quant.execution.models import OrderRequest


def _broker(tmp_path, monkeypatch) -> CtpSimBrokerAdapter:
    monkeypatch.setenv("NORTHSTAR_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv(
        "NORTHSTAR_CTP_SIM_STATE_PATH",
        str(tmp_path / "storage" / "ctp_sim_state.json"),
    )
    monkeypatch.setenv("NORTHSTAR_CTP_SIM_ACCOUNT", "ctp-sim-test")
    monkeypatch.setenv("NORTHSTAR_DEFAULT_CASH", "1000000")
    get_settings.cache_clear()
    broker = CtpSimBrokerAdapter()
    broker.connect()
    broker.seed_market_quotes({"RB2610": 3100.0})
    return broker


def _order(**updates) -> OrderRequest:
    payload = {
        "strategy_id": "ctp-sim-test",
        "symbol": "RB2610",
        "side": "BUY",
        "qty": 2.0,
        "plan_id": "ctp-sim-plan-1",
        "account": "ctp-sim-test",
        "reference_price": 3100.0,
        "ctp_offset": "open",
        "margin_rate": 0.1,
        "volume_multiple": 10,
        "instrument_id": "rb2610",
        "exchange_id": "SHFE",
    }
    payload.update(updates)
    return OrderRequest(**payload)


def test_ctp_sim_recovers_submitted_order_after_disconnect(tmp_path, monkeypatch):
    try:
        broker = _broker(tmp_path, monkeypatch)
        result = broker.submit_order(_order())
        broker.disconnect()

        with pytest.raises(ConnectionError, match="CTP_SIM_DISCONNECTED"):
            broker.sync_state()

        recovered = _broker(tmp_path, monkeypatch)
        snapshot = recovered.sync_state()
        position = snapshot.positions[0]

        assert result.status == "Submitted"
        assert snapshot.open_orders == []
        assert snapshot.completed_orders[0]["status"] == "Filled"
        assert snapshot.fills[0].ctp_offset == "open"
        assert position.symbol == "RB2610"
        assert position.qty == 2.0
        assert position.long_today_qty == 2.0
        assert snapshot.account_values["CurrMargin"] == pytest.approx(6200.0)
        assert snapshot.account_values["AvailableFunds"] == pytest.approx(993800.0)
    finally:
        get_settings.cache_clear()


def test_ctp_sim_requires_explicit_shfe_close_and_tracks_yesterday(
    tmp_path,
    monkeypatch,
):
    try:
        broker = _broker(tmp_path, monkeypatch)
        broker.submit_order(_order())
        broker.sync_state()
        broker.roll_trading_day(utc_now().date() + timedelta(days=1))

        with pytest.raises(ValueError, match="CTP_SIM_EXPLICIT_CLOSE_REQUIRED"):
            broker.submit_order(
                _order(
                    plan_id="close-generic",
                    side="SELL",
                    qty=1.0,
                    ctp_offset="close",
                    margin_rate=None,
                )
            )

        result = broker.submit_order(
            _order(
                plan_id="close-yesterday",
                side="SELL",
                qty=1.0,
                ctp_offset="close_yesterday",
                margin_rate=None,
            )
        )
        snapshot = broker.sync_state()

        assert result.status == "Submitted"
        assert snapshot.positions[0].qty == 1.0
        assert snapshot.positions[0].long_today_qty == 0.0
        assert snapshot.positions[0].long_yesterday_qty == 1.0
        assert snapshot.fills[-1].ctp_offset == "close_yesterday"
    finally:
        get_settings.cache_clear()


def test_ctp_sim_account_override_derives_an_independent_state_path(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("NORTHSTAR_CTP_SIM_ACCOUNT", "ctp-sim-configured")
    monkeypatch.delenv("NORTHSTAR_CTP_SIM_STATE_PATH", raising=False)
    get_settings.cache_clear()
    try:
        overridden = CtpSimBrokerAdapter(account="ctp-sim-override")
        configured = CtpSimBrokerAdapter()

        assert overridden.state_path == (
            tmp_path / "storage/brokers/ctp_sim/ctp-sim-override/state.json"
        )
        assert configured.state_path == (
            tmp_path / "storage/brokers/ctp_sim/ctp-sim-configured/state.json"
        )
        assert overridden.state_path != configured.state_path
    finally:
        get_settings.cache_clear()
