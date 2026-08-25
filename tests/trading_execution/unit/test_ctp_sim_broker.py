from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Event

import pytest
from sqlalchemy import select

import northstar_quant.trading_execution.broker.ctp_sim_broker as ctp_sim_broker
from northstar_quant.foundation.common.time import utc_now
from northstar_quant.foundation.config.settings import Settings, get_settings
from northstar_quant.foundation.db.models import SimulatedBrokerStateRecord
from northstar_quant.trading_execution.broker.ctp_sim_broker import CtpSimBrokerAdapter
from northstar_quant.trading_execution.broker.contracts import BrokerConnectionState
from northstar_quant.trading_execution.execution.models import (
    BrokerStateSnapshot,
    OrderRequest,
)
from tests.helpers.ctp_sim_submission import create_test_ctp_sim_submission_authority


def _broker(
    tmp_path,
    monkeypatch,
    *,
    session_factory,
    default_cash: float = 1000000,
) -> CtpSimBrokerAdapter:
    settings = Settings(
        _env_file=None,
        storage_dir=tmp_path / "storage",
        downloads_dir=tmp_path / "storage" / "downloads",
        reports_dir=tmp_path / "reports",
        log_dir=tmp_path / "logs",
        ctp_sim_account="ctp-sim-test",
        default_cash=default_cash,
    )
    monkeypatch.setattr(ctp_sim_broker, "get_settings", lambda: settings)
    broker = CtpSimBrokerAdapter(
        submission_authority=create_test_ctp_sim_submission_authority(),
        session_factory=session_factory,
    )
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


def test_ctp_sim_recovers_submitted_order_after_disconnect(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    try:
        broker = _broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
        )
        result = broker.submit_order(_order())
        broker.disconnect()

        with pytest.raises(ConnectionError, match="CTP_SIM_DISCONNECTED"):
            broker.sync_state()

        recovered = _broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
        )
        snapshot = recovered.sync_state()
        position = snapshot.positions[0]

        assert result.status == "Submitted"
        assert snapshot.open_orders == []
        assert snapshot.completed_orders[0]["status"] == "Filled"
        assert snapshot.fills[0].ctp_offset == "open"
        assert position.symbol == "RB2610"
        assert position.qty == 2.0
        assert position.long_today_qty == 2.0
        assert position.long_frozen_qty == 0.0
        assert position.long_closable_qty == 2.0
        assert position.short_closable_qty == 0.0
        assert position.margin == pytest.approx(6200.0)
        assert position.realized_pnl == 0.0
        assert position.unrealized_pnl == 0.0
        assert snapshot.account_values["CurrMargin"] == pytest.approx(6200.0)
        assert snapshot.account_values["AvailableFunds"] == pytest.approx(993800.0)
    finally:
        get_settings.cache_clear()


def test_ctp_sim_submission_transaction_serializes_concurrent_lifecycle_reads(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    """A polling thread must not borrow the candidate submitter's Session."""

    try:
        broker = _broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
        )
        bound = Event()
        release = Event()
        lifecycle_started = Event()
        lifecycle_finished = Event()
        repository_entered = Event()
        original_locked_state = broker._state_repository.locked_state

        @contextmanager
        def observe_locked_state(*args, **kwargs):
            repository_entered.set()
            with original_locked_state(*args, **kwargs) as locked:
                yield locked

        monkeypatch.setattr(broker._state_repository, "locked_state", observe_locked_state)

        def hold_submission_session() -> None:
            with postgresql_session_factory() as session:
                with broker.submission_transaction(session):
                    bound.set()
                    assert release.wait(timeout=5)

        def poll_lifecycle():
            lifecycle_started.set()
            try:
                return broker.sync_state()
            finally:
                lifecycle_finished.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            holder = executor.submit(hold_submission_session)
            assert bound.wait(timeout=5)
            lifecycle = executor.submit(poll_lifecycle)
            assert lifecycle_started.wait(timeout=5)
            try:
                assert not repository_entered.wait(timeout=0.1)
                assert not lifecycle_finished.wait(timeout=0.1)
            finally:
                release.set()
            holder.result(timeout=5)
            snapshot = lifecycle.result(timeout=5)

        assert snapshot.account == "ctp-sim-test"
        assert repository_entered.is_set()
    finally:
        get_settings.cache_clear()


def test_ctp_sim_requires_explicit_shfe_close_and_tracks_yesterday(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    try:
        broker = _broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
        )
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
        assert snapshot.positions[0].realized_pnl == 0.0
        assert snapshot.fills[-1].ctp_offset == "close_yesterday"
    finally:
        get_settings.cache_clear()


def test_ctp_sim_freezes_pending_close_quantity_and_rejects_over_close(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    try:
        broker = _broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
        )
        broker.submit_order(_order())
        broker.sync_state()
        broker.submit_order(
            _order(
                plan_id="pending-close",
                side="SELL",
                qty=1.0,
                ctp_offset="close_today",
                margin_rate=None,
                order_type="LMT",
                limit_price=3200.0,
            )
        )

        position = broker.sync_state().positions[0]
        assert position.long_frozen_qty == 1.0
        assert position.long_closable_qty == 1.0

        with pytest.raises(ValueError, match="CTP_SIM_CLOSE_POSITION_EXCEEDED"):
            broker.submit_order(
                _order(
                    plan_id="over-close",
                    side="SELL",
                    qty=2.0,
                    ctp_offset="close_today",
                    margin_rate=None,
                )
            )
    finally:
        get_settings.cache_clear()


def test_ctp_sim_rejects_opening_order_when_margin_is_insufficient(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    try:
        broker = _broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
            default_cash=6_000,
        )

        with pytest.raises(ValueError, match="CTP_SIM_MARGIN_INSUFFICIENT"):
            broker.submit_order(_order())

        assert broker.sync_state().open_orders == []
    finally:
        get_settings.cache_clear()


def test_ctp_sim_cancel_is_async_idempotent_and_recovers_after_reconnect(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    try:
        broker = _broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
        )
        result = broker.submit_order(
            _order(
                plan_id="cancel-pending",
                order_type="LMT",
                limit_price=3000.0,
            )
        )

        assert broker.cancel_order(result.broker_order_id) is True
        assert broker.cancel_order(result.broker_order_id) is True
        assert broker.get_order_status(result.broker_order_id)["status"] == "PendingCancel"

        broker.disconnect()
        recovered = _broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
        )
        snapshot = recovered.sync_state()

        assert snapshot.open_orders == []
        assert snapshot.fills == []
        assert snapshot.completed_orders[0]["status"] == "Cancelled"
        assert recovered.cancel_order(result.broker_order_id) is False
    finally:
        get_settings.cache_clear()


def test_ctp_sim_rejects_accepted_order_once_without_fabricating_fill(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    try:
        broker = _broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
        )
        result = broker.submit_order(
            _order(
                plan_id="front-rejection",
                order_type="LMT",
                limit_price=3000.0,
            )
        )

        assert broker.reject_order(result.broker_order_id, reason="front validation rejected") is True
        assert broker.reject_order(result.broker_order_id, reason="duplicate callback") is False
        snapshot = broker.sync_state()

        assert snapshot.open_orders == []
        assert snapshot.fills == []
        assert snapshot.completed_orders[0]["status"] == "Rejected"
        assert snapshot.completed_orders[0]["rejection_reason"] == "front validation rejected"
    finally:
        get_settings.cache_clear()


def test_ctp_sim_rejects_direct_continuous_symbol(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    try:
        broker = _broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
        )

        with pytest.raises(ValueError, match="CTP_SIM_CONTINUOUS_CONTRACT_FORBIDDEN"):
            broker.submit_order(_order(symbol="RB_CONT"))
    finally:
        get_settings.cache_clear()


def test_ctp_sim_rejects_raw_submission_without_final_guard_without_mutating_state(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    settings = Settings(
        _env_file=None,
        storage_dir=tmp_path / "storage",
        downloads_dir=tmp_path / "storage" / "downloads",
        reports_dir=tmp_path / "reports",
        log_dir=tmp_path / "logs",
        ctp_sim_account="ctp-sim-test",
    )
    monkeypatch.setattr(ctp_sim_broker, "get_settings", lambda: settings)
    try:
        broker = CtpSimBrokerAdapter(session_factory=postgresql_session_factory)
        broker.connect()
        broker.seed_market_quotes({"RB2610": 3100.0})
        state_before = broker._state_repository.read_state()
        revision_before = broker._state_repository.current_revision()

        with pytest.raises(
            PermissionError,
            match="CTP_SIM_FINAL_SUBMISSION_AUTHORITY_REQUIRED",
        ):
            broker.submit_order(_order())

        assert broker._state_repository.read_state() == state_before
        assert broker._state_repository.current_revision() == revision_before
        snapshot = broker.sync_state()
        assert snapshot.open_orders == []
        assert snapshot.completed_orders == []
        assert snapshot.fills == []
    finally:
        get_settings.cache_clear()


def test_ctp_sim_rejects_a_structural_noop_object_as_submission_authority(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    """A duck-typed guard cannot reopen the P8 candidate submission boundary."""

    class ForgedAuthority:
        def reserve(self, order: OrderRequest) -> None:
            del order

        def assert_reserved(self, order: OrderRequest) -> None:
            del order

        def mark_submitted(
            self,
            order: OrderRequest,
            *,
            snapshot: BrokerStateSnapshot,
        ) -> None:
            del order, snapshot

    settings = Settings(
        _env_file=None,
        storage_dir=tmp_path / "storage",
        downloads_dir=tmp_path / "storage" / "downloads",
        reports_dir=tmp_path / "reports",
        log_dir=tmp_path / "logs",
        ctp_sim_account="ctp-sim-test",
    )
    monkeypatch.setattr(ctp_sim_broker, "get_settings", lambda: settings)
    try:
        with pytest.raises(
            PermissionError,
            match="CTP_SIM_SUBMISSION_AUTHORITY_INVALID",
        ):
            CtpSimBrokerAdapter(
                submission_authority=ForgedAuthority(),  # type: ignore[arg-type]
                session_factory=postgresql_session_factory,
            )
    finally:
        get_settings.cache_clear()


def test_ctp_sim_account_override_uses_an_independent_postgresql_state_scope(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    settings = Settings(
        _env_file=None,
        storage_dir=tmp_path / "storage",
        downloads_dir=tmp_path / "storage" / "downloads",
        reports_dir=tmp_path / "reports",
        log_dir=tmp_path / "logs",
        ctp_sim_account="ctp-sim-configured",
    )
    monkeypatch.setattr(ctp_sim_broker, "get_settings", lambda: settings)
    try:
        overridden = CtpSimBrokerAdapter(
            account="ctp-sim-override",
            session_factory=postgresql_session_factory,
        )
        configured = CtpSimBrokerAdapter(session_factory=postgresql_session_factory)
        overridden.connect()
        configured.connect()
        overridden.seed_market_quotes({"RB2610": 3100.0})

        with postgresql_session_factory() as session:
            records = list(
                session.scalars(
                    select(SimulatedBrokerStateRecord)
                    .where(
                        SimulatedBrokerStateRecord.broker == "ctp_sim",
                        SimulatedBrokerStateRecord.account.in_(
                            ("ctp-sim-override", "ctp-sim-configured")
                        ),
                    )
                    .order_by(SimulatedBrokerStateRecord.account.asc())
                )
            )

        assert overridden.account == "ctp-sim-override"
        assert configured.account == "ctp-sim-configured"
        assert [record.account for record in records] == [
            "ctp-sim-configured",
            "ctp-sim-override",
        ]
        assert records[0].revision == 0
        assert records[1].revision == 1
        assert configured._state_repository.read_state()["quotes"] == {}
        assert overridden._state_repository.read_state()["quotes"]["RB2610"]["last"] == 3100.0
    finally:
        get_settings.cache_clear()


def test_ctp_sim_broker_status_never_allows_risk_before_explicit_connect(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    settings = Settings(
        _env_file=None,
        storage_dir=tmp_path / "storage",
        downloads_dir=tmp_path / "storage" / "downloads",
        reports_dir=tmp_path / "reports",
        log_dir=tmp_path / "logs",
        ctp_sim_account="ctp-sim-status",
    )
    monkeypatch.setattr(ctp_sim_broker, "get_settings", lambda: settings)
    try:
        broker = CtpSimBrokerAdapter(session_factory=postgresql_session_factory)
        assert broker.broker_status().connection_state is BrokerConnectionState.DISCONNECTED
        assert broker.broker_status().permits_new_risk is False
        broker.connect()
        assert broker.broker_status().permits_new_risk is True
    finally:
        get_settings.cache_clear()
