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


def _initialize_portable_components(engine: Engine) -> None:
    """PG adapter regression fixtures only; deployed stores never compose these owners."""
    from northstar_quant.accounting.baselines import initialize_broker_baselines
    from northstar_quant.accounting.funds import initialize_broker_funds
    from northstar_quant.accounting.ledger import initialize_broker_ledger
    from northstar_quant.accounting.stream_progress import initialize_stream_accounts
    from northstar_quant.broker.records import initialize_broker_records
    from northstar_quant.execution.reviews import initialize_order_reviews
    from northstar_quant.live.commands import initialize_live_commands
    from northstar_quant.live.materials import initialize_materials
    from northstar_quant.live.opening_budgets import initialize_opening_budgets
    from northstar_quant.live.streams import initialize_streams
    from northstar_quant.research.configurations import initialize_configuration_store
    from northstar_quant.research.factor_catalog import initialize_factor_catalog
    from northstar_quant.research.paper import initialize_paper_store
    from northstar_quant.research.runs import initialize_run_store
    from northstar_quant.research.strategy_management import initialize_strategy_management

    with engine.begin() as connection:
        for install in (
            initialize_factor_catalog,
            initialize_strategy_management,
            initialize_run_store,
            initialize_configuration_store,
            initialize_paper_store,
            initialize_materials,
            initialize_broker_records,
            initialize_broker_baselines,
            initialize_broker_ledger,
            initialize_order_reviews,
            initialize_streams,
            initialize_stream_accounts,
            initialize_opening_budgets,
            initialize_broker_funds,
            initialize_live_commands,
        ):
            install(connection)
        from northstar_quant.live.instances import initialize

        initialize(connection)


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
    _initialize_portable_components(engine)
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
            "data_cleanup_receipts",
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
def live_engine(tmp_path) -> Generator[Engine, None, None]:
    from northstar_quant.live.storage import initialize, open_store

    engine = open_store(tmp_path / "live.sqlite")
    initialize(engine)
    try:
        yield engine
    finally:
        engine.dispose()


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
                # The tested facts and account ownership use the same local file.
                assert engine.dialect.name == "sqlite"
                from northstar_quant.live import account_ownership
                from northstar_quant.live.instances import InstanceBinding

                accounts = tmp_path / "accounts"
                accounts.mkdir(mode=0o700, exist_ok=True)
                monkeypatch.setattr(account_ownership, "ACCOUNT_DIRECTORY", accounts)
                application.state.owner.binding = InstanceBinding(
                    engine,
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


@pytest.fixture(scope="session")
def workspace_hash():
    from northstar_quant.web.passwords import hash_password
    from tests.apps.browser import WORKSPACE_PASSWORD

    return hash_password(WORKSPACE_PASSWORD)


@pytest.fixture(autouse=True)
def workspace_password(tmp_path, monkeypatch, workspace_hash):
    import json

    directory = tmp_path / "workspace"
    directory.mkdir()
    monkeypatch.setenv("NORTHSTAR_WORKSPACE_DIR", str(directory))
    for app in ("data_hub", "research", "live"):
        (directory / f"northstar_{app}.json").write_text(
            json.dumps({"username": "owner", "password_hash": workspace_hash})
        )
