"""Preserve retained facts and activate restoration only after evidence verification."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from northstar_quant.accounting.baselines import BrokerBaselines
from northstar_quant.accounting.funds import BrokerFunds
from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.apps.live.maintenance import backup, restore
from northstar_quant.apps.storage import initialize_database
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.broker.settings import get_profile
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.execution.reviews import OrderReviews
from northstar_quant.live.opening_budgets import BrokerOpeningBudgets
from northstar_quant.live.streams import LiveStreams
from tests.accounting.test_ledger import ledger_query, position, position_baseline, trade
from tests.execution.test_orders import order
from tests.live.test_market import OPEN, tick
from tests.live.test_streams import Clock, logins, prepare, start


@contextmanager
def _empty_restore_database(directory: Path) -> Iterator[Engine]:
    from northstar_quant.live.storage import open_store

    target = open_store(directory / (uuid4().hex + ".sqlite"))
    try:
        yield target
    finally:
        target.dispose()


def _disable_fact_guards(connection) -> None:
    names = (
        connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'")
        .scalars()
        .all()
    )
    for name in names:
        connection.exec_driver_sql('DROP TRIGGER "' + name.replace('"', '""') + '"')


def test_initialization_and_restore_keep_all_interrupted_query_evidence(
    live_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = BrokerRecords(live_engine)
    saved = [
        records.begin(
            get_profile("simnow_dev").identity(),
            "123456",
            "rb2610",
            request_id=UUID(int=number),
        )
        for number in range(1, 102)
    ]
    # Publication uses a completed synthetic session; its account observations
    # and every login/position/trade must declare that same trading day.
    trading_day = "20260904"
    library, stream_source, configuration, calls = prepare(
        live_engine, tmp_path, monkeypatch, trading_day=trading_day
    )
    baselines = BrokerBaselines(live_engine)
    baseline_id = UUID(baselines.context(stream_source)["baseline"]["baseline_id"])
    check_id = uuid4()
    baseline = baselines.get_baseline(baseline_id)
    comparison = baselines.compare(baseline_id, stream_source, request_id=check_id)
    ledger = BrokerLedger(live_engine)
    entry_id, position_check_id, order_check_id = uuid4(), uuid4(), uuid4()
    fill = trade(TradingDay=trading_day, TradeDate=trading_day)
    entry = ledger.ingest(
        baseline_id,
        ledger_query(
            live_engine,
            day=trading_day,
            trades=(fill,),
            positions=(position(TradingDay=trading_day),),
        ),
        request_id=entry_id,
    )
    position_check = ledger.compare(
        entry_id,
        ledger_query(
            live_engine,
            day=trading_day,
            trades=(fill,),
            positions=(position(TradingDay=trading_day),),
            orders=(order(TradingDay=trading_day),),
        ),
        request_id=position_check_id,
    )
    order_check = OrderReviews(live_engine).check(position_check_id, request_id=order_check_id)
    funds_id = uuid4()
    funds_entry = BrokerFunds(live_engine).observe(
        baseline_id, UUID(position_check["query_batch_id"]), request_id=funds_id
    )
    assert entry["status"] == "READY" and entry["fill_count"] == 1
    assert position_check["status"] == "MATCHED"
    assert order_check["status"] == "MATCHED" and len(order_check["orders"]) == 1
    streams, stream_id = LiveStreams(live_engine, library), uuid4()
    stream_open = OPEN - timedelta(days=3)
    try:
        start(streams, stream_source, configuration, stream_id)
        assert calls["ready"].wait(3)
        logins(calls["accept"], at=stream_open)
        # Leave headroom for receipt latency and the independent idle monitor.
        # A 5s source cadence plus 100ms transport legitimately exceeds the
        # 5s source-freshness limit before the next callback is projected.
        for sequence, seconds in enumerate(range(0, 181, 4), 3):
            event = tick(
                sequence,
                stream_open + timedelta(seconds=seconds),
                price="3100" if seconds < 120 else "3110",
                volume=100 + sequence,
            )
            Clock.at = datetime.fromisoformat(event.received_at)
            calls["accept"](event)
    finally:
        streams.close()
    archived = streams.archive(
        stream_id,
        through_sequence=48,
        session_open="2026-09-04T01:01:00Z",
        session_close="2026-09-04T01:03:00Z",
        request_id=uuid4(),
    )
    assert archived["status"] == "PUBLISHED", archived
    dataset = library.load_dataset(UUID(archived["snapshot_id"]))
    stream = streams.get(stream_id)
    events = streams.events(stream_id)
    assert stream["received"] == stream["cursor"] == len(events) == 48
    assert len(stream["steps"]) == 2 and stream["steps"][0]["result"]["intent"] is not None
    assert stream["status"] == "STOPPED" and stream["paused"]
    opening_budget = BrokerOpeningBudgets(live_engine, library).create(
        stream_id, 48, order_check_id, limit_price=Decimal("3110"), request_id=uuid4()
    )
    assert opening_budget["status"] == "UNKNOWN"
    # Explicit initialization may add current Module tables, never rebind facts.
    initialize_database(live_engine, owner="live")
    assert records.get(UUID(int=1)) == saved[0]
    assert records.get(UUID(int=101)) == saved[-1]
    assert baselines.get_baseline(baseline_id) == baseline
    assert baselines.get_check(check_id) == comparison
    assert ledger.get(entry_id) == entry
    assert ledger.get_check(position_check_id) == position_check
    assert OrderReviews(live_engine).get(order_check_id) == order_check
    assert BrokerFunds(live_engine).get(funds_id) == funds_entry
    assert streams.get(stream_id) == stream
    with _empty_restore_database(tmp_path) as target:
        backup(live_engine, SourceFiles(tmp_path / "archive"), tmp_path / "backup")
        result = restore(target, tmp_path / "restored", tmp_path / "backup")
        assert result["evidence"] == {
            "ctp_orders_count": 0,
            "ctp_receipts_count": 0,
            "ctp_fills_count": 0,
            "authorizations_count": 0,
            "query_batches_count": 105,
            "pending_queries_count": 101,
            "baselines_count": 1,
            "checks_count": 1,
            "position_entries_count": 1,
            "position_checks_count": 1,
            "order_checks_count": 1,
            "streams_count": 1,
            "materials_count": 1,
            "local_orders_count": 0,
        }
        assert result["execution"] == "RECONCILIATION_REQUIRED"
        restored = BrokerRecords(target)
        assert restored.get(UUID(int=1)) == saved[0]
        assert restored.get(UUID(int=101)) == saved[-1]
        restored_baselines = BrokerBaselines(target)
        assert restored_baselines.get_baseline(baseline_id) == baseline
        assert restored_baselines.get_check(check_id) == comparison
        restored_ledger = BrokerLedger(target)
        assert restored_ledger.get(entry_id) == entry
        assert restored_ledger.get_check(position_check_id) == position_check
        assert OrderReviews(target).get(order_check_id) == order_check
        assert BrokerFunds(target).get(funds_id) == funds_entry
        restored_streams = LiveStreams(
            target, DataLibrary(target, SourceFiles(tmp_path / "restored"))
        )
        assert restored_streams.get(stream_id) == stream
        assert restored_streams.events(stream_id) == events
        assert restored_streams.get(stream_id)["connection"] == "NOT_ATTACHED"
        restored_library = DataLibrary(target, SourceFiles(tmp_path / "restored"))
        assert restored_library.load_dataset(dataset.snapshot_id) == dataset
        assert restored_library.attempt(UUID(archived["attempt_id"])) == archived
        assert (
            BrokerOpeningBudgets(target, restored_library).get(UUID(opening_budget["budget_id"]))
            == opening_budget
        )
        assert calls["count"] == 1  # Recovery did not invoke the synthetic receiver again.
        assert not (tmp_path / "restored/.restore-incomplete").exists()
    # Fault injection: the archive bytes remain intact, but their parent receipt
    # evidence no longer matches. Restoration must not trust just the file hash.
    with live_engine.begin() as connection:
        _disable_fact_guards(connection)
        connection.execute(
            text(
                "UPDATE broker_stream_events SET committed_at=datetime(committed_at, '+1 second') "
                "WHERE stream_id=:id AND sequence=1"
            ),
            {"id": stream_id},
        )
    with pytest.raises(ValueError, match="differs from its fixed persisted callback prefix"):
        backup(live_engine, SourceFiles(tmp_path / "archive"), tmp_path / "changed-parent-backup")
    assert not (tmp_path / "changed-parent-backup/manifest.json").exists()


def test_corrupt_query_evidence_keeps_restore_unactivated(
    live_engine: Engine, tmp_path: Path
) -> None:
    records = BrokerRecords(live_engine)
    for number in range(1, 102):
        records.begin(
            get_profile("simnow_dev").identity(),
            "123456",
            "rb2610",
            request_id=UUID(int=number),
        )
    # Simulate damaged backup content; this is fault injection, not an app write path.
    # The oldest query lies outside the workspace's newest-100 list.
    with live_engine.begin() as connection:
        _disable_fact_guards(connection)
        connection.execute(
            text("UPDATE broker_query_batches SET binding_hash = :digest WHERE batch_id = :id"),
            {"digest": "0" * 64, "id": UUID(int=1)},
        )
    with pytest.raises(ValueError, match="query identity no longer matches"):
        backup(live_engine, SourceFiles(tmp_path / "sources"), tmp_path / "backup")
    assert not (tmp_path / "backup/manifest.json").exists()


def test_broken_position_chain_keeps_restore_unactivated(
    live_engine: Engine, tmp_path: Path
) -> None:
    baseline_id = position_baseline(live_engine)
    ledger = BrokerLedger(live_engine)
    first_id = uuid4()
    first_fill = trade()
    ledger.ingest(baseline_id, ledger_query(live_engine, trades=(first_fill,)), request_id=first_id)
    later = ledger.ingest(
        baseline_id,
        ledger_query(live_engine, trades=(first_fill, trade("T2", Volume=1))),
        request_id=uuid4(),
    )
    assert later["status"] == "READY" and later["fill_count"] == 2
    # Simulate a missing predecessor, not just a bad row digest. The later entry
    # and its source query remain intact, but the full position chain is broken.
    with live_engine.begin() as connection:
        _disable_fact_guards(connection)
        removed = connection.execute(
            text("DELETE FROM broker_position_entries WHERE entry_id = :id"), {"id": first_id}
        )
        assert removed.rowcount == 1
    with pytest.raises(ValueError, match="position ledger chain or source evidence"):
        backup(live_engine, SourceFiles(tmp_path / "sources"), tmp_path / "backup")
    assert not (tmp_path / "backup/manifest.json").exists()


def test_corrupt_stream_parent_source_keeps_restore_unactivated(
    live_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library, source, configuration, calls = prepare(live_engine, tmp_path, monkeypatch)
    streams, identifier = LiveStreams(live_engine, library), uuid4()
    try:
        start(streams, source, configuration, identifier)
        assert calls["ready"].wait(3)
        logins(calls["accept"])
        event = tick(3, OPEN)
        Clock.at = datetime.fromisoformat(event.received_at)
        calls["accept"](event)
    finally:
        streams.close()
    assert streams.get(identifier)["received"] == streams.get(identifier)["cursor"] == 3
    # A copied callback is the parent source of its stored projection. Damage
    # that source without changing the projection or its pinned event hash.
    with live_engine.begin() as connection:
        _disable_fact_guards(connection)
        damaged = connection.execute(
            text(
                "UPDATE broker_stream_events SET event_hash=:hash "
                "WHERE stream_id=:id AND sequence=3"
            ),
            {"hash": "0" * 64, "id": identifier},
        )
        assert damaged.rowcount == 1
    with pytest.raises(ValueError, match="stream (account )?source sequence or digest"):
        backup(live_engine, SourceFiles(tmp_path / "archive"), tmp_path / "backup")
    assert not (tmp_path / "backup/manifest.json").exists()
    assert calls["count"] == 1


def test_missing_order_check_parent_keeps_restore_unactivated(
    live_engine: Engine, tmp_path: Path
) -> None:
    ledger = BrokerLedger(live_engine)
    baseline_id = position_baseline(live_engine)
    entry_id, position_check_id, order_check_id = uuid4(), uuid4(), uuid4()
    fill = trade()
    ledger.ingest(
        baseline_id,
        ledger_query(live_engine, trades=(fill,), positions=(position(),)),
        request_id=entry_id,
    )
    ledger.compare(
        entry_id,
        ledger_query(live_engine, trades=(fill,), positions=(position(),), orders=(order(),)),
        request_id=position_check_id,
    )
    comparison = OrderReviews(live_engine).check(position_check_id, request_id=order_check_id)
    assert comparison["status"] == "MATCHED" and len(comparison["orders"]) == 1
    # Fault injection removes only the fixed parent. The order check's own
    # digest, ledger entry and source query remain intact; they cannot replace it.
    with live_engine.begin() as connection:
        _disable_fact_guards(connection)
        removed = connection.execute(
            text("DELETE FROM broker_position_checks WHERE check_id = :id"),
            {"id": position_check_id},
        )
        assert removed.rowcount == 1
    with pytest.raises(ValueError, match="order comparison parent evidence"):
        backup(live_engine, SourceFiles(tmp_path / "sources"), tmp_path / "backup")
    assert not (tmp_path / "backup/manifest.json").exists()


@pytest.mark.parametrize("damage", ["query", "position", "order_parent", "schema"])
def test_restore_rechecks_semantics_even_with_matching_backup_hash(
    live_engine: Engine, tmp_path: Path, damage: str
) -> None:
    import hashlib
    import json

    from sqlalchemy import inspect

    from northstar_quant.live.storage import open_store

    baseline_id = position_baseline(live_engine)
    ledger = BrokerLedger(live_engine)
    first_id, check_id = uuid4(), uuid4()
    fill = trade()
    ledger.ingest(baseline_id, ledger_query(live_engine, trades=(fill,)), request_id=first_id)
    ledger.ingest(
        baseline_id,
        ledger_query(live_engine, trades=(fill, trade("T2", Volume=1))),
        request_id=uuid4(),
    )
    ledger.compare(
        first_id,
        ledger_query(live_engine, trades=(fill,), positions=(position(),), orders=(order(),)),
        request_id=check_id,
    )
    OrderReviews(live_engine).check(check_id, request_id=uuid4())
    destination = tmp_path / "backup"
    backup(live_engine, SourceFiles(tmp_path / "sources"), destination)
    frozen = open_store(destination / "database.sqlite")
    try:
        with frozen.begin() as connection:
            _disable_fact_guards(connection)
            if damage == "query":
                connection.exec_driver_sql("UPDATE broker_query_batches SET binding_hash='broken'")
            elif damage == "position":
                connection.execute(
                    text("DELETE FROM broker_position_entries WHERE entry_id=:id"),
                    {"id": first_id},
                )
            elif damage == "order_parent":
                connection.execute(
                    text("DELETE FROM broker_position_checks WHERE check_id=:id"), {"id": check_id}
                )
            else:
                connection.exec_driver_sql("DROP TABLE execution_order_events")
    finally:
        frozen.dispose()
    manifest_path = destination / "manifest.json"
    document = json.loads(manifest_path.read_text())
    document["database_sha256"] = hashlib.sha256(
        (destination / "database.sqlite").read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(document))
    with _empty_restore_database(tmp_path) as target:
        with pytest.raises(ValueError):
            restore(target, tmp_path / "restored", destination)
        assert not inspect(target).get_table_names()
        assert not (tmp_path / "restored").exists()


def test_live_restore_manifest_and_target_preflight_cannot_block_or_modify_backup(tmp_path):
    import os

    destination = tmp_path / "backup"
    destination.mkdir()
    with _empty_restore_database(tmp_path) as target:
        with pytest.raises(ValueError, match="separate directories"):
            restore(target, destination / "restored", destination)
        os.mkfifo(destination / "manifest.json")
        with pytest.raises(ValueError, match="bounded regular file"):
            restore(target, tmp_path / "restored", destination)
        assert not (tmp_path / "restored").exists()


def test_joint_restore_preserves_unpriced_fills_and_confirmed_fee_coverage(
    live_engine: Engine, tmp_path: Path
) -> None:
    from dataclasses import replace
    from datetime import UTC

    from northstar_quant.accounting.fees import FeeFact
    from northstar_quant.execution.journal import OrderJournal
    from tests.execution.test_journal import fill, request

    journal = OrderJournal(live_engine, uuid4())
    pending, resolved = request(), request()
    facts = []
    for requested in (pending, resolved):
        journal.submit(
            requested, authorization_id=uuid4(), admit=lambda _: None, dispatch=lambda *_: None
        )
        fact = replace(fill(requested, 3), fee=None)
        # This case isolates OMS backup semantics; no broker/account is asserted.
        journal.fill(fact, post_account=lambda *_: None)
        facts.append(fact)
    at = datetime.now(UTC)
    fee = FeeFact(
        str(uuid4()),
        (facts[1].fill_id,),
        Decimal("5"),
        "CNY",
        at,
        at,
        "synthetic OMS coverage, no broker assertion",
    )
    journal.confirm_fee(fee, post_account=lambda *_: None)
    expected = [journal.get(row.order_id) for row in (pending, resolved)]
    destination = tmp_path / "fee-backup"
    backup(live_engine, SourceFiles(tmp_path / "fee-sources"), destination)
    with _empty_restore_database(tmp_path) as target:
        restore(target, tmp_path / "restored-fee-sources", destination)
        reopened = OrderJournal(target, uuid4())
        assert reopened.verify_all() == 2
        assert [reopened.get(row.order_id) for row in (pending, resolved)] == expected
        assert reopened.get(pending.order_id)["fee_pending_lots"] == 3
        assert reopened.get(resolved.order_id)["fee_pending_lots"] == 0


def test_rehashed_position_comparison_cannot_hide_broker_difference(live_engine):
    import hashlib
    import json

    from northstar_quant.persistence.sql import write_transaction

    baseline = position_baseline(live_engine)
    ledger = BrokerLedger(live_engine)
    identifier = uuid4()
    ledger.ingest(baseline, ledger_query(live_engine, trades=(trade(),)), request_id=identifier)
    check_id = uuid4()
    checked = ledger.compare(
        identifier,
        ledger_query(live_engine, trades=(trade(),), positions=(position(quantity=3),)),
        request_id=check_id,
    )
    assert checked["status"] == "DIFFERENCES"
    assert BrokerLedger(live_engine).get_check(check_id) == checked
    checked["status"] = "MATCHED"
    for row in checked["positions"]:
        row["observed_today"] = row["expected_today"]
        row["delta_today"] = 0
    encoded = json.dumps(
        checked, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    with write_transaction(live_engine) as connection:
        _disable_fact_guards(connection)
        connection.exec_driver_sql(
            "UPDATE broker_position_checks SET document=?, sha256=? WHERE check_id=?",
            (encoded, hashlib.sha256(encoded.encode()).hexdigest(), check_id.hex),
        )
    with pytest.raises(ValueError, match="comparison differs from retained source facts"):
        BrokerLedger(live_engine).verify_all()


@pytest.mark.parametrize("kind", ["opening", "comparison"])
def test_rehashed_baseline_cannot_invent_cash_or_hide_change(live_engine, kind):
    import hashlib
    import json

    from northstar_quant.persistence.sql import write_transaction
    from tests.accounting.test_baselines import saved_query

    owner, baseline_id = BrokerBaselines(live_engine), uuid4()
    document = owner.establish(saved_query(live_engine), request_id=baseline_id)
    if kind == "opening":
        table, key, identifier = "broker_account_baselines", "baseline_id", baseline_id
        document["opening"]["funds"]["Balance"] = "200000"
    else:
        identifier = uuid4()
        document = owner.compare(
            baseline_id, saved_query(live_engine, money={"Balance": "99000"}), request_id=identifier
        )
        assert document["status"] == "DIFFERENCES"
        table, key = "broker_baseline_checks", "check_id"
        document["status"] = "MATCHED"
        for row in document["funds"]:
            row["observed"] = row["expected"]
            row["delta"] = "0"
    encoded = json.dumps(
        document, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    with write_transaction(live_engine) as connection:
        _disable_fact_guards(connection)
        connection.exec_driver_sql(
            f"UPDATE {table} SET document=?, sha256=? WHERE {key}=?",
            (encoded, hashlib.sha256(encoded.encode()).hexdigest(), identifier.hex),
        )
    with pytest.raises(ValueError, match="differs from.*source"):
        BrokerBaselines(live_engine).verify_all()


@pytest.mark.parametrize("field", ["account", "trades", "execution"])
def test_rehashed_query_projection_cannot_replace_original_callbacks(live_engine, field):
    import json

    from northstar_quant.broker.events import capture_hash
    from northstar_quant.persistence.sql import write_transaction
    from tests.accounting.test_baselines import saved_query

    identifier = saved_query(live_engine)
    with write_transaction(live_engine) as connection:
        row = connection.exec_driver_sql(
            "SELECT result FROM broker_query_batches WHERE batch_id=?", (identifier.hex,)
        ).scalar_one()
    result = json.loads(row)
    saved = BrokerRecords(live_engine).get(identifier)
    binding = {
        key: saved[key]
        for key in (
            "batch_id",
            "profile",
            "account_id",
            "instrument",
            "query_scope",
            "created_at",
            "code_revision",
        )
    }
    if field == "account":
        result["completeness"]["sections"]["account"]["rows"][0]["Balance"] = "1000000"
    elif field == "trades":
        result["completeness"]["sections"]["trades"]["rows"] = [trade()]
    else:
        result["execution"]["order_sending"] = True
    with write_transaction(live_engine) as connection:
        _disable_fact_guards(connection)
        connection.exec_driver_sql(
            "UPDATE broker_query_batches SET result=?, result_hash=? WHERE batch_id=?",
            (
                json.dumps(result),
                capture_hash({"binding": binding, "result": result}),
                identifier.hex,
            ),
        )
    with pytest.raises(ValueError, match="projection differs from its retained callbacks"):
        BrokerRecords(live_engine).get(identifier)


@pytest.mark.parametrize("balance", ["100000", "0", "-2500.5"])
def test_broker_context_uses_shared_fifo_without_inventing_fees_or_cash(live_engine, balance):
    baseline = position_baseline(live_engine, balance=balance)
    source = ledger_query(
        live_engine,
        trades=(
            trade("OPEN", Price="3100", Volume=2),
            trade(
                "CLOSE", Price="3110", Volume=1, Direction="1", OffsetFlag="3", TradeTime="09:31:00"
            ),
            trade("SHORT", Price="3120", Volume=3, Direction="1", TradeTime="09:32:00"),
        ),
    )
    ledger, identifier = BrokerLedger(live_engine), uuid4()
    fixed = ledger.ingest(baseline, source, request_id=identifier)
    result = ledger.context(source)["accounting_projection"]
    assert result["realized_pnl_before_fees"] == "100"
    assert result["status"] == "INCOMPLETE" and result["cash"] is None
    assert result["total_fees"] is None and len(result["pending_fee_fill_ids"]) == 3
    assert result["fill_count"] == 3 and result["execution"]["order_sending"] is False
    assert BrokerLedger(live_engine).context(source)["accounting_projection"] == result
    assert ledger.get(identifier) == fixed
    assert all(fill["fee"] is None for fill in fixed["added_fills"])


def test_broker_fill_prices_remain_exact_even_outside_expected_tick(live_engine):
    baseline = position_baseline(live_engine)
    source = ledger_query(
        live_engine,
        trades=(
            trade("OPEN", Price="3100.1"),
            trade("CLOSE", Price="3110.2", Direction="1", OffsetFlag="3", TradeTime="09:31:00"),
        ),
    )
    ledger, identifier = BrokerLedger(live_engine), uuid4()
    ledger.ingest(baseline, source, request_id=identifier)
    result = ledger.context(source)["accounting_projection"]
    assert result["realized_pnl_before_fees"] == "202"
    assert result["status"] == "INCOMPLETE" and result["cash"] is None
    assert ledger.get(identifier)["added_fills"][0]["price"] == "3100.1"
