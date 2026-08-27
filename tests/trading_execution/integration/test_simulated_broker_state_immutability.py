"""PostgreSQL migration integration coverage for simulated-broker immutability."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError

from northstar_quant.foundation.config.settings import get_settings
from tests.helpers.paths import PROJECT_ROOT
from tests.helpers.postgresql import postgresql_test_url


def _alembic_config(project_root: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    return config


def _assert_database_rejects_transition_mutation(
    engine,
    statement: str,
    parameters: dict[str, object],
) -> None:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(DatabaseError, match="SIMULATED_BROKER_STATE_TRANSITION_IMMUTABLE"):
                connection.execute(text(statement), parameters)
        finally:
            transaction.rollback()


def test_migrated_simulated_broker_transition_ledger_rejects_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deployed baseline, not ORM create_all, rejects all ledger mutation."""

    database_url = postgresql_test_url(tmp_path / "simulated-broker-immutability.db")
    config = _alembic_config(PROJECT_ROOT)
    engine = None
    monkeypatch.setenv("NORTHSTAR_DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        command.upgrade(config, "head")
        engine = create_engine(database_url, future=True)
        with engine.begin() as connection:
            transition_id = connection.scalar(
                text(
                    """
                    INSERT INTO simulated_broker_state_transition_records (
                        broker,
                        account,
                        schema_version,
                        revision,
                        action,
                        state_json,
                        state_hash,
                        predecessor_transition_hash,
                        transition_hash,
                        occurred_at
                    ) VALUES (
                        'paper',
                        'immutability-test',
                        1,
                        0,
                        'initialize',
                        :state_json,
                        :state_hash,
                        NULL,
                        :transition_hash,
                        :occurred_at
                    ) RETURNING id
                    """
                ),
                {
                    "state_hash": "b" * 64,
                    "state_json": '{"cash":100000.0}',
                    "transition_hash": "a" * 64,
                    "occurred_at": datetime(2026, 8, 27, tzinfo=UTC),
                },
            )

        assert isinstance(transition_id, int)
        for statement, parameters in (
            (
                "UPDATE simulated_broker_state_transition_records "
                "SET action = 'tampered' WHERE id = :transition_id",
                {"transition_id": transition_id},
            ),
            (
                "DELETE FROM simulated_broker_state_transition_records WHERE id = :transition_id",
                {"transition_id": transition_id},
            ),
            ("TRUNCATE TABLE simulated_broker_state_transition_records", {}),
        ):
            _assert_database_rejects_transition_mutation(
                engine,
                statement,
                parameters,
            )

        with engine.connect() as connection:
            remaining = connection.scalar(
                text(
                    "SELECT COUNT(*) FROM simulated_broker_state_transition_records "
                    "WHERE id = :transition_id"
                ),
                {"transition_id": transition_id},
            )
        assert remaining == 1
    finally:
        if engine is not None:
            engine.dispose()
        get_settings.cache_clear()
