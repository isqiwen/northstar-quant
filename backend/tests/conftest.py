"""Real PostgreSQL lifecycle for data behavior tests."""

from __future__ import annotations

import os
from collections.abc import Callable, Generator
from contextlib import ExitStack

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from northstar_quant.apps.live.instances import Instances
from northstar_quant.apps.storage import initialize_database
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.live.instances import Instance


def _reset(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


@pytest.fixture(scope="session")
def postgres_engine() -> Generator[Engine, None, None]:
    database_url = os.environ.get("NORTHSTAR_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("NORTHSTAR_TEST_DATABASE_URL is required for PostgreSQL integration")
    parsed = make_url(database_url)
    if (
        parsed.drivername != "postgresql+psycopg"
        or parsed.database != "northstar_quant_test"
        or parsed.query
    ):
        raise pytest.UsageError(
            "use disposable postgresql+psycopg northstar_quant_test without URL options"
        )
    engine = create_engine(database_url, pool_pre_ping=True)
    _reset(engine)
    initialize_database(engine)
    try:
        yield engine
    finally:
        _reset(engine)
        engine.dispose()


@pytest.fixture
def clean_database(postgres_engine: Engine) -> None:
    from northstar_quant.data_management.db.base import Base

    quoted = [
        postgres_engine.dialect.identifier_preparer.quote(t.name)
        for t in Base.metadata.tables.values()
    ]
    quoted.append('"research_runs"')
    quoted.extend(
        f'"{name}"'
        for name in (
            "live_strategy_materials",
            "live_instance_binding",
            "factor_revisions",
            "factor_annotations",
            "factor_runs",
            "strategy_versions",
            "strategy_candidates",
            "research_attempts",
            "research_jobs",
            "research_job_attempts",
        )
    )
    quoted.extend(
        f'"{name}"'
        for name in ("paper_configurations", "paper_sessions", "paper_inputs", "paper_steps")
    )
    quoted.extend(
        f'"{name}"'
        for name in (
            "data_sources",
            "data_processing_attempts",
            "data_sync_jobs",
            "data_compactions",
            "data_sync_settings",
            "data_sync_contracts",
            "data_sync_calendar",
            "data_sync_receipts",
            "data_sync_coverage",
            "data_sync_attempts",
            "data_admission_rejections",
            "data_backups",
            "broker_query_batches",
            "broker_account_baselines",
            "broker_baseline_checks",
            "broker_position_entries",
            "broker_position_checks",
            "broker_order_checks",
            "broker_streams",
            "broker_stream_events",
            "broker_stream_steps",
            "broker_stream_commands",
            "broker_stream_accounts",
            "broker_opening_budgets",
            "broker_funds_entries",
            "live_commands",
        )
    )
    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.exec_driver_sql(f"TRUNCATE TABLE {', '.join(quoted)} RESTART IDENTITY CASCADE")
        connection.exec_driver_sql("INSERT INTO data_sync_settings(singleton) VALUES(true)")


@pytest.fixture
def session_factory(postgres_engine: Engine, clean_database: None) -> sessionmaker[Session]:
    del clean_database
    return sessionmaker(bind=postgres_engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def db_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    with session_factory() as session:
        yield session


@pytest.fixture
def live_client(
    tmp_path, monkeypatch
) -> Generator[Callable[[Engine, DataLibrary], object], None, None]:
    """Exercise the actual Live HTTP Interface; only this fixture stops its owner."""
    from northstar_quant.apps.live.kernel import create_app
    from northstar_quant.live import LiveAuth, LiveClient

    auth = LiveAuth(read_token="test-read-" + "r" * 40, control_token="test-control-" + "c" * 40)
    applications: dict[tuple[Engine, DataLibrary], FastAPI] = {}
    with ExitStack() as lifespans:

        def connect(engine: Engine, library: DataLibrary) -> LiveClient:
            key = engine, library
            if key not in applications:
                application = create_app(engine, library, auth)
                # Shared PostgreSQL domain tests still exercise the actual local
                # SQLite instance/account guard before their synthetic SDK calls.
                from northstar_quant.live import account_ownership
                from northstar_quant.live.instances import InstanceBinding
                from northstar_quant.live.storage import initialize, open_store

                accounts = tmp_path / "accounts"
                accounts.mkdir(mode=0o700, exist_ok=True)
                monkeypatch.setattr(account_ownership, "ACCOUNT_DIRECTORY", accounts)
                local = open_store(tmp_path / f"owner-{len(applications)}.sqlite")
                initialize(local)
                lifespans.callback(local.dispose)
                application.state.owner.binding = InstanceBinding(
                    local,
                    Instance.from_environment(),
                    "9999",
                    os.environ.get("NORTHSTAR_SIMNOW_USER_ID", ""),
                )
                lifespans.enter_context(TestClient(application, base_url="http://127.0.0.1"))
                applications[key] = application
            transport = TestClient(applications[key], base_url="http://127.0.0.1")
            return LiveClient("http://127.0.0.1", auth, client=transport)

        yield connect


@pytest.fixture
def live_web_app(live_client: Callable[[Engine, DataLibrary], object]) -> Callable[..., FastAPI]:
    from northstar_quant.apps.live import create_app

    def compose(engine: Engine, library: DataLibrary) -> FastAPI:
        return create_app(
            instances=Instances(
                {"sim": live_client(engine, library)}, [Instance("sim", "simnow_trading")]
            )
        )

    return compose


@pytest.fixture(scope="session", autouse=True)
def workspace_password() -> Generator[None, None, None]:
    from northstar_quant.web.passwords import hash_password
    from tests.apps.browser import WORKSPACE_PASSWORD

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("NORTHSTAR_WORKSPACE_PASSWORD_HASH", hash_password(WORKSPACE_PASSWORD))
        yield
