"""Actual SQLite fee/OMS transaction, retained unknown costs and cold verification."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from northstar_quant.accounting.fees import FeeFact
from northstar_quant.accounting.fifo import Account
from northstar_quant.execution.journal import OrderJournal
from northstar_quant.persistence.sql import write_transaction
from tests.accounting.test_portfolio_account import A
from tests.execution.test_journal import fill, post, request, setup_journal


def prepare(tmp_path):
    engine, journal = setup_journal(tmp_path)
    with write_transaction(engine) as connection:
        connection.exec_driver_sql(
            "CREATE TABLE posted_fees (identity TEXT PRIMARY KEY, amount TEXT NOT NULL)"
        )
        connection.exec_driver_sql("CREATE TABLE cash (amount TEXT NOT NULL)")
        connection.exec_driver_sql("INSERT INTO cash VALUES ('10000')")
    orders, fills = [], []
    for _ in range(2):
        order = request()
        journal.submit(
            order, authorization_id=uuid4(), admit=lambda _: None, dispatch=lambda *_: None
        )
        fact = replace(fill(order, 3), fee=None)
        state = journal.fill(fact, post_account=post)
        assert state["status"] == "FILLED" and state["pending_fees"] == {fact.fill_id: 3}
        assert state["requires_reconciliation"] and state["reservation"]["reserved_fee"] == "6"
        assert state["reservation"]["reserved_margin"] == "0"
        orders.append(order)
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


def post_fee(connection, fact):
    connection.exec_driver_sql(
        "INSERT INTO posted_fees VALUES (?, ?)", (fact.fee_id, str(fact.amount))
    )
    balance = Decimal(connection.exec_driver_sql("SELECT amount FROM cash").scalar_one())
    connection.exec_driver_sql("UPDATE cash SET amount=?", (str(balance - fact.amount),))


def test_aggregate_fee_commits_once_with_all_order_reservations_and_restarts(tmp_path):
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

    def fail(connection, fact):
        post_fee(connection, fact)
        raise RuntimeError("account post failed")

    with pytest.raises(RuntimeError, match="account post failed"):
        journal.confirm_fee(fee, post_account=fail)
    assert [journal.get(order.order_id) for order in orders] == before
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT amount FROM cash").scalar_one() == "10000"
        assert connection.exec_driver_sql("SELECT count(*) FROM execution_fees").scalar_one() == 0
    states = journal.confirm_fee(fee, post_account=post_fee)
    assert all(
        not row["requires_reconciliation"] and row["reservation"]["reserved_fee"] == "0"
        for row in states
    )
    journal.confirm_fee(fee, post_account=lambda *_: pytest.fail("duplicate fee must not debit"))
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT amount FROM cash").scalar_one() == "9991"
    reopened = OrderJournal(engine, uuid4())
    assert reopened.verify_all() == 2
    with pytest.raises(ValueError, match="different input"):
        reopened.confirm_fee(replace(fee, amount=Decimal("10")), post_account=post_fee)
    with pytest.raises(ValueError, match="already resolved"):
        reopened.confirm_fee(charge(fills), post_account=post_fee)
    assert reopened.verify_all() == 2
    engine.dispose()


def test_larger_confirmed_fee_is_booked_and_marks_orders_for_reconciliation(tmp_path):
    engine, journal, orders, fills = prepare(tmp_path)
    states = journal.confirm_fee(charge(fills, "15"), post_account=post_fee)
    assert all(row["status"] == "UNKNOWN" and not row["pending_fees"] for row in states)
    for order in orders:
        journal.cancel(
            order.order_id,
            uuid4(),
            admit=lambda _: pytest.fail("no remaining lots"),
            dispatch=lambda _: pytest.fail("fully filled order must not cancel"),
        )
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT amount FROM cash").scalar_one() == "9985"
    assert journal.verify_all() == 2
    engine.dispose()


def test_fee_projection_loss_is_not_silently_accepted_on_restart(tmp_path):
    engine, journal, orders, fills = prepare(tmp_path)
    fee = charge(fills)
    journal.confirm_fee(fee, post_account=post_fee)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER retain_execution_order_events_DELETE")
        connection.exec_driver_sql(
            "DELETE FROM execution_order_events WHERE event_id=?",
            ("fee:" + fee.fee_id + ":" + orders[1].order_id,),
        )
    with pytest.raises(ValueError, match="complete order coverage"):
        journal.verify_all()
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


def test_fee_corrections_commit_delta_replay_and_keep_risk_conflicts(tmp_path):
    engine, journal, orders, fills = prepare(tmp_path)

    def post_revision(connection, fact):
        # Rebuild the actual shared account from retained fills and fee facts,
        # in the same SQLite transaction as the order projections.
        import json

        account = Account(
            Decimal(10000),
            tuple(
                replace(A, contract_id=order.contract_id, symbol="SYNTHETIC_" + str(index))
                for index, order in enumerate(orders)
            ),
        )
        for execution in fills:
            account.apply(execution)
        for document in connection.exec_driver_sql(
            "SELECT document FROM execution_fees ORDER BY rowid"
        ).scalars():
            account.confirm_fee(FeeFact.from_dict(json.loads(document)))
        connection.exec_driver_sql(
            "INSERT INTO posted_fees VALUES (?, ?)", (fact.fee_id, str(fact.amount))
        )
        connection.exec_driver_sql("UPDATE cash SET amount=?", (str(account.cash),))

    original = charge(fills, "9")
    journal.confirm_fee(original, post_account=post_revision)
    revision = replace(charge(fills, "15"), supersedes_fee_id=original.fee_id)

    def fail(connection, fact):
        post_revision(connection, fact)
        raise RuntimeError("abort correction")

    with pytest.raises(RuntimeError):
        journal.confirm_fee(revision, post_account=fail)
    assert journal.verify_all() == 2
    assert all(journal.get(order.order_id)["status"] == "FILLED" for order in orders)
    journal.confirm_fee(revision, post_account=post_revision)
    refund = replace(charge(fills, "2"), supersedes_fee_id=revision.fee_id)
    journal.confirm_fee(refund, post_account=post_revision)
    journal.confirm_fee(refund, post_account=lambda *_: pytest.fail("duplicate"))
    assert all(journal.get(order.order_id)["status"] == "UNKNOWN" for order in orders)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT amount FROM cash").scalar_one() == "9998"
        assert connection.exec_driver_sql("SELECT count(*) FROM posted_fees").scalar_one() == 3
    with pytest.raises(ValueError, match="fork"):
        journal.confirm_fee(
            replace(charge(fills), supersedes_fee_id=original.fee_id), post_account=post_revision
        )
    reopened = OrderJournal(engine, uuid4())
    assert reopened.verify_all() == 2
    engine.dispose()
