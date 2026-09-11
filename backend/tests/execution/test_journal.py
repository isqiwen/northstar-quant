"""Real local transactions: uncertain dispatch never repeats or releases money."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from northstar_quant.accounting.fifo import FillFact
from northstar_quant.execution.journal import OrderJournal, initialize_journal
from northstar_quant.execution.orders import Offset, OrderBudget, PendingOrder, Side
from northstar_quant.live.storage import open_store
from northstar_quant.persistence.sql import write_transaction


def setup_journal(tmp_path):
    path = tmp_path / "execution.sqlite"
    engine = open_store(path)
    with write_transaction(engine) as connection:
        initialize_journal(connection)
        connection.exec_driver_sql(
            "CREATE TABLE posted_fills (identity TEXT PRIMARY KEY, fee TEXT NOT NULL)"
        )
    return engine, OrderJournal(engine, uuid4())


def request():
    now = datetime.now(UTC)
    return PendingOrder(
        str(uuid4()),
        uuid4(),
        now,
        now + timedelta(minutes=1),
        Side.BUY,
        Offset.OPEN,
        3,
        Decimal("99"),
        Decimal("101"),
        contract_id=uuid4(),
        budget=OrderBudget(Decimal("2"), Decimal("101"), Decimal("1010"), Decimal("10")),
    )


def post(connection, fact):
    connection.exec_driver_sql(
        "INSERT INTO posted_fills VALUES (?, ?)", (fact.fill_id, str(fact.fee))
    )


def fill(order, quantity=1, *, identifier=None):
    return FillFact(
        identifier or str(uuid4()),
        order.order_id,
        order.contract_id,
        None,
        datetime.now(UTC),
        datetime.now(UTC).date(),
        order.side,
        order.offset,
        quantity,
        Decimal("100"),
        Decimal("2"),
    )


@pytest.mark.parametrize(
    "crash", ["before_commit", "before_sdk", "inside_sdk", "after_sdk", "returned"]
)
def test_dispatch_crash_points_and_restart(tmp_path, crash):
    engine, journal = setup_journal(tmp_path)
    order, permit, calls = request(), uuid4(), []

    def admit(connection):
        connection.exec_driver_sql("INSERT INTO posted_fills VALUES (?, ?)", ("admission", "0"))
        if crash == "before_commit":
            raise RuntimeError("crash before commit")

    def dispatch(value):
        # Dispatch can see the committed order through a different connection.
        assert journal.get(value.order_id)["attempt_id"]
        if crash == "before_sdk":
            raise KeyboardInterrupt("process died before transport")
        calls.append(value.order_id)
        if crash == "inside_sdk":
            raise RuntimeError("transport error with unknown remote outcome")
        if crash == "after_sdk":
            raise KeyboardInterrupt("process died before acknowledgement")

    if crash == "before_commit":
        with pytest.raises(RuntimeError):
            journal.submit(order, permit, admit=admit, dispatch=dispatch)
        with pytest.raises(LookupError):
            journal.get(order.order_id)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT count(*) FROM posted_fills").scalar_one() == 0
        journal.submit(
            order, permit, admit=lambda c: None, dispatch=lambda o: calls.append(o.order_id)
        )
    elif crash in {"before_sdk", "after_sdk"}:
        with pytest.raises(KeyboardInterrupt):
            journal.submit(order, permit, admit=admit, dispatch=dispatch)
    else:
        journal.submit(order, permit, admit=admit, dispatch=dispatch)
    assert journal.verify_all() == 1
    saved = journal.get(order.order_id)
    assert saved["status"] == "UNKNOWN"
    assert saved["reservation"]["reserved_margin"] == "303"
    path = engine.url.database
    engine.dispose()
    from pathlib import Path

    engine = open_store(Path(path))
    reopened = OrderJournal(engine, uuid4())
    repeat = reopened.submit(
        order,
        permit,
        admit=lambda c: pytest.fail("old runtime must not re-admit"),
        dispatch=lambda o: pytest.fail("uncertain attempt must not resend"),
    )
    assert repeat == saved
    assert len(calls) == (0 if crash == "before_sdk" else 1)
    with pytest.raises(ValueError, match="different input"):
        reopened.submit(
            replace(order, quantity_lots=2), permit, admit=lambda c: None, dispatch=lambda o: None
        )
    with pytest.raises(ValueError, match="unresolved"):
        reopened.submit(
            replace(order, order_id=str(uuid4())),
            uuid4(),
            admit=lambda c: None,
            dispatch=lambda o: None,
        )
    assert reopened.working() == (order,)
    assert reopened.verify_all() == 1
    engine.dispose()


def test_terminal_report_waits_for_individual_posting_and_rollback(tmp_path):
    engine, journal = setup_journal(tmp_path)
    order = request()
    journal.submit(order, uuid4(), admit=lambda c: None, dispatch=lambda o: None)
    report = uuid4()
    result = journal.report(order.order_id, evidence_id=report, state="CANCELED", cumulative_lots=1)
    assert result["status"] == "UNKNOWN"
    assert result["reservation"]["reserved_margin"] == "303"
    fact = fill(order)

    def failed_post(connection, value):
        post(connection, value)
        raise RuntimeError("account transaction failed")

    with pytest.raises(RuntimeError):
        journal.fill(fact, post_account=failed_post)
    assert journal.get(order.order_id) == result
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM posted_fills").scalar_one() == 0
    result = journal.fill(fact, post_account=post)
    assert result["status"] == "CANCELED"
    assert result["reservation"]["reserved_margin"] == "0"
    assert journal.working() == ()
    assert (
        journal.fill(fact, post_account=lambda *args: pytest.fail("duplicate account posting"))
        == result
    )
    assert (
        journal.report(order.order_id, evidence_id=report, state="CANCELED", cumulative_lots=1)
        == result
    )
    # Contradictory late fact still posts. It never silently authorizes new risk.
    result = journal.fill(fill(order), post_account=post)
    assert result["status"] == "UNKNOWN"
    assert result["reservation"]["reserved_margin"] == "101"
    assert result["order"]["filled_lots"] == 2
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM posted_fills").scalar_one() == 2
    assert journal.verify_all() == 1
    engine.dispose()


def test_fill_order_scope_expiry_and_retention(tmp_path):
    engine, journal = setup_journal(tmp_path)
    order = request()
    journal.submit(order, uuid4(), admit=lambda c: None, dispatch=lambda o: None)
    fact = fill(order)
    with pytest.raises(ValueError, match="fixed local order"):
        journal.fill(replace(fact, contract_id=uuid4()), post_account=post)
    first = journal.fill(fact, post_account=post)
    assert first["status"] == "PARTIALLY_FILLED"
    assert first["reservation"]["reserved_margin"] == "202"
    with pytest.raises(ValueError, match="different input"):
        journal.fill(replace(fact, fee=Decimal("3")), post_account=post)
    late = replace(fill(order, 2), filled_at=order.expires_at + timedelta(seconds=1))
    result = journal.fill(late, post_account=post)
    assert result["status"] == "FILLED"
    assert result["reservation"]["reserved_fee"] == "0"
    with pytest.raises(ValueError, match="exceed"):
        journal.fill(fill(order), post_account=post)
    for sql in [
        "DELETE FROM execution_orders",
        "UPDATE execution_orders SET authorization_id='other'",
        "UPDATE execution_order_events SET kind='changed'",
        "DELETE FROM execution_order_events",
    ]:
        with pytest.raises(DBAPIError):
            with write_transaction(engine) as connection:
                connection.exec_driver_sql(sql)
    assert journal.get(order.order_id) == result
    assert journal.verify_all() == 1
    engine.dispose()


def test_corrupt_projection_cannot_become_recovered_authority(tmp_path):
    engine, journal = setup_journal(tmp_path)
    order = request()
    journal.submit(order, uuid4(), admit=lambda c: None, dispatch=lambda o: None)
    assert journal.verify_all() == 1
    with write_transaction(engine) as connection:
        connection.exec_driver_sql(
            "UPDATE execution_orders SET status='CANCELED', "
            "broker_state='CANCELED', reported_lots=0"
        )
    with pytest.raises(ValueError, match="retained facts"):
        OrderJournal(engine, uuid4()).verify_all()
    engine.dispose()


def test_out_of_order_reports_never_clear_a_conflict(tmp_path):
    engine, journal = setup_journal(tmp_path)
    order = request()
    journal.submit(order, uuid4(), admit=lambda c: None, dispatch=lambda o: None)
    journal.fill(fill(order), post_account=post)
    journal.report(order.order_id, evidence_id=uuid4(), state="PARTIALLY_FILLED", cumulative_lots=1)
    journal.report(order.order_id, evidence_id=uuid4(), state="ACCEPTED", cumulative_lots=0)
    result = journal.report(
        order.order_id, evidence_id=uuid4(), state="CANCELED", cumulative_lots=1
    )
    assert result["status"] == "UNKNOWN"
    assert result["reservation"]["reserved_margin"] == "202"
    assert journal.verify_all() == 1
    engine.dispose()


def test_cancel_loss_rejection_retry_and_fill_race_keep_budget(tmp_path):
    engine, journal = setup_journal(tmp_path)
    order, request_id, calls = request(), uuid4(), []
    journal.submit(order, uuid4(), admit=lambda c: None, dispatch=lambda o: None)

    def lost(order):
        calls.append(order.order_id)
        raise KeyboardInterrupt("lost cancel acknowledgement")

    with pytest.raises(KeyboardInterrupt):
        journal.cancel(order.order_id, request_id, admit=lambda c: None, dispatch=lost)
    journal = OrderJournal(engine, uuid4())
    journal.cancel(
        order.order_id,
        request_id,
        admit=lambda c: pytest.fail("re-admitted cancellation"),
        dispatch=lost,
    )
    assert calls == [order.order_id]
    with pytest.raises(ValueError, match="unresolved"):
        journal.cancel(order.order_id, uuid4(), admit=lambda c: None, dispatch=lost)
    assert journal.get(order.order_id)["reservation"]["reserved_margin"] == "303"
    journal.fill(fill(order), post_account=post)
    journal.reject_cancel(order.order_id, request_id, evidence_id=uuid4())
    second = uuid4()
    result = journal.cancel(
        order.order_id, second, admit=lambda c: None, dispatch=lambda o: calls.append(o.order_id)
    )
    assert result["reservation"]["reserved_margin"] == "202"
    journal.report(order.order_id, evidence_id=uuid4(), state="CANCELED", cumulative_lots=1)
    journal.cancel(
        order.order_id, uuid4(), admit=lambda c: pytest.fail("terminal admission"), dispatch=lost
    )
    assert calls == [order.order_id, order.order_id]
    assert journal.get(order.order_id)["reservation"]["reserved_margin"] == "0"
    assert journal.verify_all() == 1
    engine.dispose()


def test_joint_restore_preserves_uncertain_attempt_without_resending(live_engine, tmp_path):
    from northstar_quant.apps.live.maintenance import backup, restore
    from northstar_quant.data_management.files import SourceFiles

    journal = OrderJournal(live_engine, uuid4())
    order, permit = request(), uuid4()
    saved = journal.submit(order, permit, admit=lambda c: None, dispatch=lambda o: None)
    destination = tmp_path / "backup"
    backup(live_engine, SourceFiles(tmp_path / "source"), destination)
    restored = open_store(tmp_path / "restored.sqlite")
    try:
        result = restore(restored, tmp_path / "restored-sources", destination)
        assert result["execution"] == "RECONCILIATION_REQUIRED"
        journal = OrderJournal(restored, uuid4())
        assert journal.verify_all() == 1
        assert (
            journal.submit(
                order,
                permit,
                admit=lambda c: pytest.fail("restore is not authorization"),
                dispatch=lambda o: pytest.fail("restore must not resend"),
            )
            == saved
        )
        assert journal.working() == (order,)
    finally:
        restored.dispose()


def test_browser_reads_exact_order_facts_and_cannot_submit(live_engine, live_web_app, tmp_path):
    from northstar_quant.data_management.files import SourceFiles
    from northstar_quant.data_management.library import DataLibrary
    from tests.apps.browser import ProtocolClient, login_response

    journal = OrderJournal(live_engine, uuid4())
    order = request()
    journal.submit(order, uuid4(), admit=lambda c: None, dispatch=lambda o: None)
    journal.report(order.order_id, evidence_id=uuid4(), state="CANCELED", cumulative_lots=1)
    application = live_web_app(
        live_engine, DataLibrary(live_engine, SourceFiles(tmp_path / "files"))
    )
    with ProtocolClient(application, base_url="http://127.0.0.1") as http:
        path = f"/api/orders/{order.order_id}"
        assert http.get(path).status_code == 401
        login_response(http)
        listing = http.get("/api/orders").json()
        assert listing["next_before"] is None
        assert listing["orders"][0]["quantity_lots"] == 3
        record = http.get(path).json()
        assert record["record"]["status"] == "UNKNOWN"
        assert record["record"]["reservation"]["reserved_margin"] == "303"
        assert [event["kind"] for event in record["events"]] == [
            "SEND_ATTEMPT",
            "TRANSPORT_RETURNED",
            "BROKER_REPORT",
        ]
        assert http.get(path + "?after=2").json()["events"] == record["events"][2:]
        assert http.get(path + "?after=-1").status_code == 422
        assert http.get("/api/orders?before=0").status_code == 422
        assert http.post("/api/orders", json=order.to_dict()).status_code in {403, 405}
        assert journal.verify_all() == 1


def test_order_history_pages_do_not_drop_or_duplicate_facts(tmp_path):
    engine, journal = setup_journal(tmp_path)
    identifiers = []
    for _ in range(102):
        order = request()
        identifiers.append(order.order_id)
        journal.submit(order, uuid4(), admit=lambda c: None, dispatch=lambda o: None)
    first = journal.list()
    assert len(first["orders"]) == 100 and first["next_before"] is not None
    second = journal.list(before=first["next_before"])
    assert second["next_before"] is None
    assert [row["order_id"] for row in first["orders"] + second["orders"]] == identifiers[::-1]
    order_id = identifiers[-1]
    for _ in range(101):
        journal.report(order_id, evidence_id=uuid4(), state="ACCEPTED", cumulative_lots=0)
    events = journal.detail(order_id)
    assert len(events["events"]) == 100
    following = journal.detail(order_id, after=events["next_after"])
    assert len(following["events"]) == 3 and following["next_after"] is None
    assert len({e["event_id"] for e in events["events"] + following["events"]}) == 103
    assert journal.verify_all() == 102
    engine.dispose()
