from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

import pytest
from sqlalchemy import select, update

import northstar_quant.trading_execution.broker.paper_broker as paper_broker
from northstar_quant.foundation.config.settings import Settings, get_settings
from northstar_quant.foundation.db.models import (
    SimulatedBrokerStateRecord,
    SimulatedBrokerStateTransitionRecord,
)
from northstar_quant.trading_execution.broker.simulated_state import (
    SimulatedBrokerStateIntegrityError,
)
from northstar_quant.trading_execution.execution.models import OrderRequest
from northstar_quant.trading_execution.broker.paper_broker import PaperBrokerAdapter


def _make_paper_broker(
    tmp_path,
    monkeypatch,
    *,
    session_factory,
    default_cash: float = 100000.0,
    paper_account: str = "paper-test",
) -> PaperBrokerAdapter:
    storage_dir = tmp_path / "storage"
    settings = Settings(
        _env_file=None,
        storage_dir=storage_dir,
        downloads_dir=storage_dir / "downloads",
        reports_dir=tmp_path / "reports",
        log_dir=tmp_path / "logs",
        default_cash=default_cash,
        paper_fill_price_mode="reference",
        paper_account=paper_account,
    )
    monkeypatch.setattr(paper_broker, "get_settings", lambda: settings)
    return PaperBrokerAdapter(session_factory=session_factory)


def test_paper_broker_market_order_persists_positions_fills_and_quotes(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    try:
        broker = _make_paper_broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
        )
        result = broker.submit_order(
            OrderRequest(
                strategy_id="paper-test",
                symbol="MA2405",
                side="BUY",
                qty=10.0,
                reference_price=100.0,
            )
        )

        assert result.accepted is True

        snapshot = broker.sync_state()
        positions = {row.symbol: row for row in snapshot.positions}

        assert snapshot.open_orders == []
        assert len(snapshot.fills) == 1
        assert positions["MA2405"].qty == 10.0
        assert positions["MA2405"].avg_cost == 100.0
        assert positions["MA2405"].market_price == 100.0
        assert snapshot.account_values["CashBalance"] == 99000.0
        assert snapshot.account_values["NetLiquidation"] == 100000.0

        reloaded_broker = _make_paper_broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
        )
        reloaded_snapshot = reloaded_broker.sync_state()
        reloaded_positions = {row.symbol: row for row in reloaded_snapshot.positions}
        quotes = reloaded_broker.get_market_quotes(["MA2405", "TA2405"])

        assert reloaded_positions["MA2405"].qty == 10.0
        assert len(reloaded_snapshot.fills) == 1
        assert len(quotes) == 1
        assert quotes[0].symbol == "MA2405"
        assert quotes[0].market_price == 100.0
        assert quotes[0].source == "paper_postgresql_state"
    finally:
        get_settings.cache_clear()


def test_paper_submission_transaction_serializes_concurrent_lifecycle_reads(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    """A polling thread cannot reuse the durable submitter's Session."""

    try:
        broker = _make_paper_broker(
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

        assert snapshot.account == "paper-test"
        assert repository_entered.is_set()
    finally:
        get_settings.cache_clear()


def test_paper_broker_limit_order_can_partial_fill_then_complete_on_next_sync(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    try:
        broker = _make_paper_broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
        )
        broker.submit_order(
            OrderRequest(
                strategy_id="paper-test",
                symbol="I2405",
                side="BUY",
                qty=10.0,
                order_type="LMT",
                limit_price=101.0,
                reference_price=100.0,
            )
        )

        first_snapshot = broker.sync_state()
        first_positions = {row.symbol: row for row in first_snapshot.positions}

        assert len(first_snapshot.open_orders) == 1
        assert first_snapshot.open_orders[0]["status"] == "PartiallyFilled"
        assert first_snapshot.open_orders[0]["filled_qty"] == 5.0
        assert first_snapshot.open_orders[0]["remaining_qty"] == 5.0
        assert len(first_snapshot.fills) == 1
        assert first_positions["I2405"].qty == 5.0

        reloaded_broker = _make_paper_broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
        )
        second_snapshot = reloaded_broker.sync_state()
        second_positions = {row.symbol: row for row in second_snapshot.positions}

        assert second_snapshot.open_orders == []
        assert len(second_snapshot.fills) == 2
        assert second_positions["I2405"].qty == 10.0
        assert second_positions["I2405"].avg_cost == 100.0
        assert second_snapshot.account_values["CashBalance"] == 99000.0
        assert second_snapshot.account_values["NetLiquidation"] == 100000.0
    finally:
        get_settings.cache_clear()


def test_paper_broker_cancel_removes_unmarketable_open_order(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    try:
        broker = _make_paper_broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
        )
        result = broker.submit_order(
            OrderRequest(
                strategy_id="paper-test",
                symbol="RB2405",
                side="BUY",
                qty=10.0,
                order_type="LMT",
                limit_price=95.0,
                reference_price=100.0,
            )
        )

        pending_snapshot = broker.sync_state()

        assert pending_snapshot.positions == []
        assert pending_snapshot.fills == []
        assert len(pending_snapshot.open_orders) == 1
        assert pending_snapshot.open_orders[0]["status"] == "Submitted"

        reloaded_broker = _make_paper_broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
        )

        assert reloaded_broker.cancel_order(result.broker_order_id) is True

        cancelled_snapshot = reloaded_broker.sync_state()

        assert cancelled_snapshot.open_orders == []
        assert cancelled_snapshot.positions == []
        assert cancelled_snapshot.fills == []
        assert cancelled_snapshot.account_values["CashBalance"] == 100000.0
    finally:
        get_settings.cache_clear()


def test_paper_broker_updates_avg_cost_and_equity_across_multiple_fills(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    try:
        broker = _make_paper_broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
        )
        broker.submit_order(
            OrderRequest(
                strategy_id="paper-test",
                symbol="IWM",
                side="BUY",
                qty=10.0,
                reference_price=100.0,
            )
        )
        broker.sync_state()

        reloaded_broker = _make_paper_broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
        )
        reloaded_broker.submit_order(
            OrderRequest(
                strategy_id="paper-test",
                symbol="IWM",
                side="BUY",
                qty=10.0,
                reference_price=110.0,
            )
        )

        snapshot = reloaded_broker.sync_state()
        position = {row.symbol: row for row in snapshot.positions}["IWM"]

        assert position.qty == 20.0
        assert position.avg_cost == 105.0
        assert position.market_price == 110.0
        assert snapshot.account_values["CashBalance"] == 97900.0
        assert snapshot.account_values["GrossPositionValue"] == 2200.0
        assert snapshot.account_values["NetLiquidation"] == 100100.0
    finally:
        get_settings.cache_clear()


def test_paper_broker_state_isolated_by_account(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    try:
        alpha = _make_paper_broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
            paper_account="paper-alpha",
        )
        alpha.submit_order(
            OrderRequest(
                strategy_id="paper-test",
                symbol="RB2405",
                side="BUY",
                qty=1.0,
                reference_price=100.0,
            )
        )
        alpha.sync_state()

        beta = _make_paper_broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
            paper_account="paper-beta",
        )
        beta_snapshot = beta.sync_state()

        assert beta_snapshot.positions == []
        assert not (tmp_path / "storage" / "brokers").exists()
        with postgresql_session_factory() as session:
            rows = list(
                session.execute(
                    select(SimulatedBrokerStateRecord)
                    .where(SimulatedBrokerStateRecord.broker == "paper")
                    .order_by(SimulatedBrokerStateRecord.account)
                ).scalars()
        )
        assert [row.account for row in rows] == ["paper-alpha", "paper-beta"]
        assert {row.account: row.revision for row in rows} == {
            "paper-alpha": 2,
            "paper-beta": 0,
        }
    finally:
        get_settings.cache_clear()


def test_paper_broker_account_scope_does_not_share_postgresql_state(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    try:
        alpha = _make_paper_broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
            paper_account="paper-alpha",
        )
        beta = _make_paper_broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
            paper_account="paper-beta",
        )
        alpha.submit_order(
            OrderRequest(
                strategy_id="paper-test",
                symbol="RB2405",
                side="BUY",
                qty=1.0,
                reference_price=100.0,
            )
        )
        assert alpha.sync_state().positions[0].qty == 1.0
        assert beta.sync_state().positions == []
    finally:
        get_settings.cache_clear()


def test_paper_broker_postgresql_state_audit_is_hash_chained_and_fails_closed_on_tamper(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    """The adapter-private snapshot remains PG-backed and independently verifiable."""

    try:
        broker = _make_paper_broker(
            tmp_path,
            monkeypatch,
            session_factory=postgresql_session_factory,
            paper_account="paper-audit",
        )
        initialized_evidence = broker.simulator_state_evidence()
        assert broker.sync_state().positions == []
        assert broker.simulator_state_evidence() == initialized_evidence

        broker.submit_order(
            OrderRequest(
                strategy_id="paper-test",
                symbol="RB2405",
                side="BUY",
                qty=1.0,
                reference_price=100.0,
                order_ref="paper-audit-order",
            )
        )
        broker.sync_state()

        with postgresql_session_factory() as session:
            record = session.scalar(
                select(SimulatedBrokerStateRecord).where(
                    SimulatedBrokerStateRecord.broker == "paper",
                    SimulatedBrokerStateRecord.account == "paper-audit",
                )
            )
            transitions = list(
                session.scalars(
                    select(SimulatedBrokerStateTransitionRecord)
                    .where(
                        SimulatedBrokerStateTransitionRecord.broker == "paper",
                        SimulatedBrokerStateTransitionRecord.account == "paper-audit",
                    )
                    .order_by(SimulatedBrokerStateTransitionRecord.revision)
                )
            )

        assert record is not None
        assert [row.revision for row in transitions] == [0, 1, 2]
        assert [row.action for row in transitions] == [
            "initialize",
            "submit_order",
            "sync_state",
        ]
        assert transitions[0].predecessor_transition_hash is None
        assert transitions[1].predecessor_transition_hash == transitions[0].transition_hash
        assert transitions[2].predecessor_transition_hash == transitions[1].transition_hash
        assert record.revision == transitions[-1].revision
        assert record.state_hash == transitions[-1].state_hash
        assert record.last_transition_hash == transitions[-1].transition_hash

        with postgresql_session_factory.begin() as session:
            session.execute(
                update(SimulatedBrokerStateRecord)
                .where(SimulatedBrokerStateRecord.id == record.id)
                .values(state_hash="0" * 64)
            )

        with pytest.raises(
            SimulatedBrokerStateIntegrityError,
            match="SIMULATED_BROKER_STATE_HASH_MISMATCH",
        ):
            broker.simulator_state_evidence()
    finally:
        get_settings.cache_clear()
