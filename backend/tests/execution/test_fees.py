"""Actual shared monetary journal and OMS commit, rollback and cold verification."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from northstar_quant.accounting import journal as money
from northstar_quant.accounting.fees import FeeFact
from northstar_quant.execution import journal as execution
from northstar_quant.execution.journal import OrderJournal
from northstar_quant.persistence.sql import write_transaction
from tests.accounting.test_portfolio_account import A
from tests.execution.test_journal import fill, post, request, setup_journal


def prepare(tmp_path):
    engine, journal = setup_journal(tmp_path)
    orders = [request(), request()]
    markets = tuple(
        replace(A, contract_id=order.contract_id, symbol="SYNTHETIC_" + str(index))
        for index, order in enumerate(orders)
    )
    with write_transaction(engine) as connection:
        money.initialize(connection)
    fills = []

    def post_fill(connection, fact):
        money.post(
            connection,
            "account",
            opening_cash=Decimal(10000),
            markets=markets,
            facts=(fact,),
            source_id="fill:" + fact.fill_id,
            source_hash="a" * 64,
        )

    for order in orders:
        journal.submit(
            order, authorization_id=uuid4(), admit=lambda _: None, dispatch=lambda *_: None
        )
        fact = replace(fill(order, 3), fee=None)
        state = journal.fill(fact, post_account=post_fill)
        assert state["status"] == "FILLED" and state["pending_fees"] == {fact.fill_id: 3}
        assert state["requires_reconciliation"] and state["reservation"]["reserved_fee"] == "6"
        assert state["reservation"]["reserved_margin"] == "0"
        fills.append(fact)
    return engine, journal, orders, fills


def charge(fills, amount="9"):
    at = datetime.now(UTC)
    return FeeFact(
        str(uuid4()),
        tuple(sorted(f.fill_id for f in fills)),
        Decimal(amount),
        "CNY",
        at,
        at,
        "synthetic verified fee coverage",
    )


def balance(engine):
    with engine.connect() as connection:
        return money.replay(connection, "account").cash


def test_aggregate_fee_commits_once_with_all_order_reservations_and_restarts(tmp_path, monkeypatch):
    engine, journal, orders, fills = prepare(tmp_path)
    with pytest.raises(ValueError, match="unresolved"):
        journal.submit(
            replace(orders[0], order_id=str(uuid4())),
            authorization_id=uuid4(),
            admit=lambda _: None,
            dispatch=lambda *_: pytest.fail("pending fees cannot send"),
        )
    fee = charge(fills)
    before = [journal.get(order.order_id) for order in orders]
    original = execution.post_fee

    def fail(*args):
        original(*args)
        raise RuntimeError("account post failed")

    with monkeypatch.context() as patch:
        patch.setattr(execution, "post_fee", fail)
        with pytest.raises(RuntimeError, match="account post failed"):
            journal.confirm_fee(fee, account_id="account")
    assert [journal.get(order.order_id) for order in orders] == before
    with pytest.raises(ValueError, match="confirmed fees"):
        balance(engine)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM execution_fees").scalar_one() == 0
        assert len(money.replay(connection, "account").applied_fees) == 0
    states = journal.confirm_fee(fee, account_id="account")
    assert all(
        not row["requires_reconciliation"] and row["reservation"]["reserved_fee"] == "0"
        for row in states
    )
    with monkeypatch.context() as patch:
        patch.setattr(execution, "post_fee", lambda *_: pytest.fail("duplicate debit"))
        journal.confirm_fee(fee, account_id="account")
    assert balance(engine) == Decimal("9991")
    reopened = OrderJournal(engine, uuid4())
    assert reopened.verify_all() == 2
    with pytest.raises(ValueError, match="different input"):
        reopened.confirm_fee(replace(fee, amount=Decimal("10")), account_id="account")
    with pytest.raises(ValueError, match="different input"):
        reopened.confirm_fee(fee, account_id="other")
    with pytest.raises(ValueError, match="already resolved"):
        reopened.confirm_fee(charge(fills), account_id="account")
    assert reopened.verify_all() == 2
    engine.dispose()


def test_larger_confirmed_fee_is_booked_and_marks_orders_for_reconciliation(tmp_path):
    engine, journal, orders, fills = prepare(tmp_path)
    states = journal.confirm_fee(charge(fills, "15"), account_id="account")
    assert all(row["status"] == "UNKNOWN" and not row["pending_fees"] for row in states)
    for order in orders:
        journal.cancel(
            order.order_id,
            uuid4(),
            admit=lambda _: pytest.fail("no remaining lots"),
            dispatch=lambda _: pytest.fail("fully filled order must not cancel"),
        )
    assert balance(engine) == Decimal("9985")
    assert journal.verify_all() == 2
    engine.dispose()


@pytest.mark.parametrize("missing", ["order", "account", "execution_charge"])
def test_fee_projection_loss_is_not_silently_accepted_on_restart(tmp_path, missing):
    engine, journal, orders, fills = prepare(tmp_path)
    fee = charge(fills)
    journal.confirm_fee(fee, account_id="account")
    with engine.begin() as connection:
        if missing == "order":
            connection.exec_driver_sql("DROP TRIGGER retain_execution_order_events_DELETE")
            connection.exec_driver_sql(
                "DELETE FROM execution_order_events WHERE event_id=?",
                ("fee:" + fee.fee_id + ":" + orders[1].order_id,),
            )
        elif missing == "execution_charge":
            connection.exec_driver_sql("DROP TRIGGER retain_execution_fees_DELETE")
            connection.exec_driver_sql("DROP TRIGGER retain_execution_order_events_DELETE")
            connection.exec_driver_sql("DELETE FROM execution_fees")
            connection.exec_driver_sql(
                "DELETE FROM execution_order_events WHERE kind='FEE_CONFIRMED'"
            )
        else:
            connection.exec_driver_sql("DROP TRIGGER account_journal_DELETE")
            connection.exec_driver_sql(
                "DELETE FROM account_journal WHERE source_id=?",
                ("execution-fee:" + fee.fee_id,),
            )
    with pytest.raises(ValueError, match="coverage|missing|postings"):
        journal.verify_all()
    if missing == "account":
        with pytest.raises(ValueError, match="missing"):
            journal.confirm_fee(fee, account_id="account")
    engine.dispose()


def test_known_execution_fee_beyond_limit_is_not_rejected_or_ignored(tmp_path):
    engine, journal = setup_journal(tmp_path)
    order = request()
    journal.submit(order, authorization_id=uuid4(), admit=lambda _: None, dispatch=lambda *_: None)
    fact = replace(fill(order, 3), fee=Decimal("20"))
    state = journal.fill(fact, post_account=post)
    assert state["filled_lots"] == 3 and state["status"] == "UNKNOWN"
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT fee FROM posted_fills").scalar_one() == "20"
    assert journal.verify_all() == 1
    engine.dispose()


def test_fee_corrections_commit_delta_replay_and_keep_risk_conflicts(tmp_path, monkeypatch):
    engine, journal, orders, fills = prepare(tmp_path)
    original = charge(fills, "9")
    journal.confirm_fee(original, account_id="account")
    revision = replace(charge(fills, "15"), supersedes_fee_id=original.fee_id)
    post_revision = execution.post_fee

    def fail(*args):
        post_revision(*args)
        raise RuntimeError("abort correction")

    with monkeypatch.context() as patch:
        patch.setattr(execution, "post_fee", fail)
        with pytest.raises(RuntimeError):
            journal.confirm_fee(revision, account_id="account")
    assert journal.verify_all() == 2
    assert all(journal.get(order.order_id)["status"] == "FILLED" for order in orders)
    journal.confirm_fee(revision, account_id="account")
    refund = replace(charge(fills, "2"), supersedes_fee_id=revision.fee_id)
    journal.confirm_fee(refund, account_id="account")
    journal.confirm_fee(refund, account_id="account")
    assert all(journal.get(order.order_id)["status"] == "UNKNOWN" for order in orders)
    assert balance(engine) == Decimal("9998")
    with engine.connect() as connection:
        assert len(money.replay(connection, "account").applied_fees) == 3
    with pytest.raises(ValueError, match="fork"):
        journal.confirm_fee(
            replace(charge(fills), supersedes_fee_id=original.fee_id),
            account_id="account",
        )
    assert OrderJournal(engine, uuid4()).verify_all() == 2
    engine.dispose()


def test_fee_cannot_release_reservations_without_corresponding_account_fills(tmp_path):
    engine, journal, orders, fills = prepare(tmp_path)
    with engine.begin() as connection:
        money.post(
            connection,
            "other",
            opening_cash=Decimal(10000),
            markets=(A,),
            facts=(),
            source_id="opening",
            source_hash="a" * 64,
        )
    before = [journal.get(order.order_id) for order in orders]
    with pytest.raises(ValueError, match="same accepted account"):
        journal.confirm_fee(charge(fills), account_id="other")
    assert [journal.get(order.order_id) for order in orders] == before
    with pytest.raises(ValueError, match="confirmed fees"):
        balance(engine)
    engine.dispose()
