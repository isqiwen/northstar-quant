"""Live command recovery and local ownership on actual SQLite files."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from northstar_quant.live.commands import Commands, initialize_live_commands
from northstar_quant.live.storage import KernelLock, open_store, write_transaction


def test_command_acknowledgement_and_unknown_survive_reopen(tmp_path):
    path = tmp_path / "live.sqlite"
    engine = open_store(path)
    with write_transaction(engine) as connection:
        initialize_live_commands(connection)
    runtime, request = uuid4(), uuid4()
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    calls = []
    commands = Commands(engine, runtime)
    result = commands.execute(
        request,
        runtime,
        deadline,
        "/test",
        {"value": "1.01"},
        lambda: calls.append(1) or {"amount": "1.01"},
    )
    assert result["status"] == "COMPLETED"
    engine.dispose()
    engine = open_store(path)
    commands = Commands(engine, uuid4())
    repeated = commands.execute(
        request, runtime, deadline, "/test", {"value": "1.01"}, lambda: calls.append(2) or {}
    )
    assert repeated["result"] == {"amount": "1.01"}
    assert calls == [1]
    unknown = uuid4()
    with write_transaction(engine) as connection:
        connection.exec_driver_sql(
            "INSERT INTO live_commands "
            "(command_id,runtime_id,path,input,expires_at,status,created_at) "
            "VALUES (?,?,?,?,?,'RUNNING',?)",
            (
                str(unknown),
                str(runtime),
                "/test",
                "{}",
                deadline.isoformat(),
                datetime.now(UTC).isoformat(),
            ),
        )
    assert commands.get(unknown)["status"] == "UNKNOWN"
    engine.dispose()


def test_writer_is_exclusive_and_failed_transaction_rolls_back(tmp_path):
    path = tmp_path / "live.sqlite"
    engine = open_store(path)
    lock = KernelLock(path)
    try:
        with pytest.raises(BlockingIOError):
            KernelLock(path)
        with write_transaction(engine) as connection:
            connection.exec_driver_sql("CREATE TABLE ledger (amount TEXT NOT NULL)")
        with pytest.raises(RuntimeError):
            with write_transaction(engine) as connection:
                connection.exec_driver_sql("INSERT INTO ledger VALUES ('123.45')")
                raise RuntimeError("failure before commit")
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT count(*) FROM ledger").scalar_one() == 0
            assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 2
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
    finally:
        lock.close()
        engine.dispose()
    reopened = KernelLock(path)
    reopened.close()


def test_sqlite_broker_query_and_account_baseline(tmp_path, monkeypatch):
    from northstar_quant.accounting.baselines import BrokerBaselines, initialize_broker_baselines
    from northstar_quant.broker.records import initialize_broker_records
    from tests.accounting.test_baselines import saved_query

    monkeypatch.setenv("NORTHSTAR_BROKER_PROFILE", "simnow_dev")
    engine = open_store(tmp_path / "evidence.sqlite")
    try:
        with write_transaction(engine) as connection:
            initialize_broker_records(connection)
            initialize_broker_baselines(connection)
        from northstar_quant.accounting.funds import BrokerFunds, initialize_broker_funds
        from northstar_quant.accounting.ledger import BrokerLedger, initialize_broker_ledger

        with write_transaction(engine) as connection:
            initialize_broker_ledger(connection)
            initialize_broker_funds(connection)
            from northstar_quant.accounting.stream_progress import initialize_stream_accounts

            initialize_stream_accounts(connection)
        source = saved_query(engine)
        baseline = BrokerBaselines(engine).establish(source, request_id=uuid4())
        baseline_id = UUID(baseline["baseline_id"])
        later = saved_query(engine)
        ledger = BrokerLedger(engine)
        entry = ledger.ingest(baseline_id, later, request_id=uuid4())
        funds = BrokerFunds(engine).observe(baseline_id, later, request_id=uuid4())
        assert entry["entry_id"] and funds["entry_id"]
    finally:
        engine.dispose()


def test_sqlite_reception_preserves_existing_recovery_rules(tmp_path, monkeypatch):
    from northstar_quant.live.storage import initialize
    from tests.live import test_streams

    exercise = test_streams.test_stream_durable_inputs_pause_retry_restart

    engine = open_store(tmp_path / "stream.sqlite")
    try:
        initialize(engine)
        from northstar_quant.research.factor_catalog import initialize_factor_catalog

        with engine.begin() as connection:
            initialize_factor_catalog(connection)
        exercise(engine, None, tmp_path, monkeypatch)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "module_name,test_name",
    [
        (
            "live.test_streams",
            "test_stream_retains_unprocessed_source_and_retries_only_the_missing_projection",
        ),
        (
            "live.test_streams",
            "test_account_failure_keeps_source_and_local_catchup_never_replays_shadow",
        ),
        (
            "integration.test_stream_archive",
            "test_incomplete_requested_range_stays_failed_then_reprocesses_same_original_bytes",
        ),
        (
            "accounting.test_stream_ledger",
            "test_stream_prefixes_share_query_dedup_and_fixed_order_history_while_paused",
        ),
        (
            "accounting.test_stream_ledger",
            "test_stream_conflicts_unknown_identity_and_source_chronology_do_not_rewrite_fills",
        ),
        (
            "accounting.test_stream_account",
            "test_entry_commit_before_progress_failure_retries_without_duplicate_fills",
        ),
    ],
)
def test_sqlite_saved_broker_facts(tmp_path, monkeypatch, module_name, test_name):
    import importlib

    from northstar_quant.live.storage import initialize
    from northstar_quant.research.factor_catalog import initialize_factor_catalog

    engine = open_store(tmp_path / "facts.sqlite")
    try:
        initialize(engine)
        with engine.begin() as connection:
            initialize_factor_catalog(connection)
        getattr(importlib.import_module("tests." + module_name), test_name)(
            engine, None, tmp_path, monkeypatch
        )
        if module_name == "integration.test_stream_archive":
            from northstar_quant.apps.live.maintenance import backup, restore
            from northstar_quant.data_management.files import SourceFiles
            from northstar_quant.data_management.library import DataLibrary
            from northstar_quant.live.streams import LiveStreams

            files = SourceFiles(tmp_path / "archive")
            snapshot = tmp_path / "backup"
            backup(engine, files, snapshot)
            restored = open_store(tmp_path / "restored.sqlite")
            try:
                restore(restored, tmp_path / "restored-sources", snapshot)
                library = DataLibrary(restored, SourceFiles(tmp_path / "restored-sources"))
                datasets = library.list_datasets()
                assert datasets
                for source in library.list_sources():
                    detail = library.source(UUID(source["source_id"]))
                    assert all(
                        str(UUID(p["snapshot_id"])) == p["snapshot_id"] for p in detail["products"]
                    )
                assert library.reconcile()["sources"]
                assert LiveStreams(restored, library).verify_all() > 0
                with pytest.raises(ValueError, match="empty|overwrites"):
                    restore(restored, tmp_path / "second-sources", snapshot)
            finally:
                restored.dispose()

    finally:
        engine.dispose()
