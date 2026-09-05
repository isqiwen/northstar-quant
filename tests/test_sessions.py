"""Protect durable Paper money, command identity and recovery on real PostgreSQL."""

from __future__ import annotations

import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import DBAPIError

from northstar_quant.data.research import ImportSpec, ResearchDataset, import_csv
from northstar_quant.research import ResearchConfig, run_research
from northstar_quant.sessions import SessionStore


def _study(engine: Engine) -> tuple[ResearchDataset, ResearchConfig]:
    path = Path(__file__).resolve().parents[1] / "examples/intraday.toml"
    study = tomllib.loads(path.read_text())
    source = dict(study["source"])
    csv = path.parent / source.pop("file")
    return import_csv(engine, csv, ImportSpec.from_mapping(source)), ResearchConfig.from_mapping(
        study["research"]
    )


def test_paper_restarts_with_fixed_configuration_and_matches_batch_account(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    dataset, config = _study(postgres_engine)
    batch = run_research(dataset, config).to_dict()
    store = SessionStore(postgres_engine)
    saved = store.save_configuration("日盘基线", config)
    assert store.save_configuration("日盘基线", config) == saved
    session_id = uuid4()
    created = store.create(
        dataset.snapshot_id, str(saved["configuration_id"]), request_id=session_id
    )
    assert (
        store.create(dataset.snapshot_id, str(saved["configuration_id"]), request_id=session_id)
        == created
    )
    assert created["status"] == "PAUSED"
    assert created["cursor"] == 0
    assert created["summary"]["ending_cash"] == config.to_dict()["initial_cash"]
    assert created["input_type"] == "FILE_REPLAY"
    commands = [uuid4() for _ in dataset.bars]
    for command in commands[:4]:
        response = store.advance(session_id, request_id=command)
        assert store.advance(session_id, request_id=command) == response
    paused = store.get(session_id)
    newer = store.save_configuration("日盘基线", replace(config, max_lots=config.max_lots + 1))
    assert newer["configuration_id"] != saved["configuration_id"]
    with pytest.raises(ValueError, match="creation request identity"):
        store.create(dataset.snapshot_id, str(newer["configuration_id"]), request_id=session_id)
    with pytest.raises(DBAPIError, match="immutable"):
        with postgres_engine.begin() as connection:
            connection.execute(text("UPDATE paper_configurations SET name = 'changed'"))

    reopened = create_engine(postgres_engine.url)
    try:
        restored = SessionStore(reopened)
        assert restored.get(session_id) == paused
        assert paused["configuration"] == saved
        assert paused["status"] == "PAUSED"
        for command in commands[4:]:
            restored.advance(session_id, request_id=command)
        complete = restored.get(session_id)
        assert complete["summary"] == batch["summary"]
        assert complete["fills"] == batch["fills"]
        assert complete["equity_curve"] == batch["equity_curve"]
        assert complete["decisions"] == batch["decisions"]
        assert complete["pending_order"] == batch["pending_order"]
        assert complete["status"] == "COMPLETED"
        assert complete["can_advance"] is False
        with pytest.raises(ValueError, match="all accepted inputs"):
            restored.advance(session_id, request_id=uuid4())
        assert len(restored.list()) == 1
    finally:
        reopened.dispose()


def test_failed_commit_lost_ack_and_competing_workers_never_duplicate_money(
    postgres_engine: Engine, clean_database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    del clean_database
    dataset, config = _study(postgres_engine)
    store = SessionStore(postgres_engine)
    saved = store.save_configuration("transaction check", config)
    identity = uuid4()
    store.create(dataset.snapshot_id, str(saved["configuration_id"]), request_id=identity)
    for _ in range(3):
        store.advance(identity, request_id=uuid4())
    before = store.get(identity)

    def fail_checkpoint(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("UPDATE paper_sessions"):
            raise RuntimeError("interrupted before commit")

    command = uuid4()
    event.listen(postgres_engine, "before_cursor_execute", fail_checkpoint)
    try:
        with pytest.raises(RuntimeError, match="before commit"):
            store.advance(identity, request_id=command)
    finally:
        event.remove(postgres_engine, "before_cursor_execute", fail_checkpoint)
    assert store.get(identity) == before
    acknowledged_later = store.advance(identity, request_id=command)
    # Discarding a committed response simulates a lost acknowledgement. A new
    # worker must find the persisted command result, not advance the next bar.
    assert SessionStore(postgres_engine).advance(identity, request_id=command) == acknowledged_later
    same_command = uuid4()
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(store.advance, identity, request_id=same_command)
        duplicate = workers.submit(
            SessionStore(postgres_engine).advance, identity, request_id=same_command
        )
        assert first.result() == duplicate.result()
    assert store.get(identity)["cursor"] == before["cursor"] + 2

    # Delivery order does not choose the market input: each worker must read the
    # next accepted sequence under the account lock, even if submitted in reverse.
    earlier, later = uuid4(), uuid4()
    with ThreadPoolExecutor(max_workers=2) as workers:
        notifications = [
            workers.submit(store.advance, identity, request_id=notification)
            for notification in (later, earlier)
        ]
        handled = sorted(
            (future.result() for future in notifications), key=lambda row: row["sequence"]
        )
    assert [row["sequence"] for row in handled] == [6, 7]
    assert [row["step"]["point"]["observation_id"] for row in handled] == [
        str(bar.observation_id) for bar in dataset.bars[5:7]
    ]
    assert store.advance(identity, request_id=command) == acknowledged_later
    for _ in range(7, len(dataset.bars)):
        store.advance(identity, request_id=uuid4())
    assert store.get(identity)["summary"] == run_research(dataset, config).to_dict()["summary"]

    def unavailable_source(_engine, _snapshot):
        raise ValueError("source is unavailable after the command was committed")

    monkeypatch.setattr("northstar_quant.sessions.load_dataset", unavailable_source)
    assert store.advance(identity, request_id=command) == acknowledged_later


def test_changed_implementation_or_corrupted_checkpoint_cannot_resume(
    postgres_engine: Engine, clean_database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    del clean_database
    dataset, config = _study(postgres_engine)
    store = SessionStore(postgres_engine)
    saved = store.save_configuration("identity check", config)
    identity = uuid4()
    store.create(dataset.snapshot_id, str(saved["configuration_id"]), request_id=identity)
    store.advance(identity, request_id=uuid4())
    with monkeypatch.context() as changed:
        changed.setattr("northstar_quant.sessions.implementation_hash", lambda: "f" * 64)
        assert store.get(identity)["can_advance"] is False
        with pytest.raises(ValueError, match="exact implementation"):
            store.advance(identity, request_id=uuid4())
        assert store.get(identity)["cursor"] == 1
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE paper_sessions SET checkpoint = jsonb_set(checkpoint, '{bar_count}', '9') "
                "WHERE session_id = :identity"
            ),
            {"identity": identity},
        )
    with pytest.raises(ValueError, match="integrity"):
        store.advance(identity, request_id=uuid4())


def test_unsupported_cross_day_input_never_creates_a_paper_account(
    postgres_engine: Engine, clean_database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    del clean_database
    dataset, config = _study(postgres_engine)
    changed_bar = replace(
        dataset.bars[-1], trading_day=dataset.bars[-1].trading_day + timedelta(days=1)
    )
    unsupported = replace(dataset, bars=(*dataset.bars[:-1], changed_bar))
    monkeypatch.setattr(
        "northstar_quant.sessions.load_dataset", lambda _engine, _snapshot: unsupported
    )
    store = SessionStore(postgres_engine)
    saved = store.save_configuration("no overnight", config)
    with pytest.raises(ValueError, match="one trading day"):
        store.create(dataset.snapshot_id, str(saved["configuration_id"]), request_id=uuid4())
    assert store.list() == []
