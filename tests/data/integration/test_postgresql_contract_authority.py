"""Integration coverage for PostgreSQL-only futures Contract Authority releases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import sessionmaker

from northstar_quant.application.contract_authority import (
    FuturesContractAuthorityError,
    resolve_futures_contract_authority,
)
from northstar_quant.data.contracts.postgresql_contract_authority import (
    ContractAuthorityError,
    PostgresContractMasterPublicationRepository,
)
from northstar_quant.foundation.config.settings import get_settings
from northstar_quant.trading_execution.broker.ctp_contract_mapping import (
    CtpContractMappingError,
)
from northstar_quant.trading_execution.broker.postgresql_contract_registry import (
    PostgresCtpContractRegistryPublicationRepository,
)
from tests.helpers.contract_authority import (
    build_test_futures_contract_authority,
    publish_test_futures_contract_authority,
)
from tests.helpers.paths import PROJECT_ROOT
from tests.helpers.postgresql import postgresql_test_url


def _alembic_config(project_root: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    return config


def _assert_mutation_rejected(engine, statement: str, parameters: dict[str, object]) -> None:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(
                DatabaseError,
                match="CONTRACT_AUTHORITY_PUBLICATION_IMMUTABLE",
            ):
                connection.execute(text(statement), parameters)
        finally:
            transaction.rollback()


def test_postgresql_contract_authority_replays_pit_and_rejects_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only releases visible at the decision time may compose an authority."""

    database_url = postgresql_test_url(tmp_path / "contract-authority.db")
    monkeypatch.setenv("NORTHSTAR_DATABASE_URL", database_url)
    get_settings.cache_clear()
    engine = None
    try:
        command.upgrade(_alembic_config(PROJECT_ROOT), "head")
        engine = create_engine(database_url, future=True)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        initial_decision_at = datetime(2026, 8, 27, 12, tzinfo=UTC)
        initial = build_test_futures_contract_authority(
            decision_at=initial_decision_at,
            publication_id="pit-master-v1",
            registry_publication_id="pit-registry-v1",
        )
        later = build_test_futures_contract_authority(
            decision_at=initial_decision_at + timedelta(hours=4),
            publication_id="pit-master-v2",
            registry_publication_id="pit-registry-v2",
        )
        with session_factory() as session:
            publish_test_futures_contract_authority(session, initial)
            publish_test_futures_contract_authority(session, later)

        with session_factory() as session:
            master_repository = PostgresContractMasterPublicationRepository(session)
            registry_repository = PostgresCtpContractRegistryPublicationRepository(session)
            with pytest.raises(ContractAuthorityError, match="UNAVAILABLE"):
                master_repository.load_at(
                    initial.authority_id,
                    decision_at=initial.master_publication.available_at - timedelta(seconds=1),
                )
            with pytest.raises(CtpContractMappingError, match="UNAVAILABLE"):
                registry_repository.load_at(
                    initial.authority_id,
                    broker="ctp_sim",
                    decision_at=initial.registry_publication.available_at - timedelta(seconds=1),
                )
            assert (
                master_repository.load_at(
                    initial.authority_id,
                    decision_at=initial_decision_at,
                ).publication_hash
                == initial.master_publication.publication_hash
            )
            assert (
                registry_repository.load_at(
                    initial.authority_id,
                    broker="ctp_sim",
                    decision_at=initial_decision_at,
                ).publication_hash
                == initial.registry_publication.publication_hash
            )

        resolved_initial = resolve_futures_contract_authority(
            initial.authority_id,
            broker="ctp_sim",
            decision_at=initial_decision_at,
            session_factory=session_factory,
        )
        resolved_later = resolve_futures_contract_authority(
            initial.authority_id,
            broker="ctp_sim",
            decision_at=initial_decision_at + timedelta(hours=4),
            session_factory=session_factory,
        )
        assert resolved_initial.master_publication.publication_hash == initial.master_publication.publication_hash
        assert resolved_later.master_publication.publication_hash == later.master_publication.publication_hash
        assert resolved_initial.registry_publication.publication_hash == initial.registry_publication.publication_hash
        assert resolved_later.registry_publication.publication_hash == later.registry_publication.publication_hash

        with pytest.raises(FuturesContractAuthorityError, match="UNAVAILABLE"):
            resolve_futures_contract_authority(
                initial.authority_id,
                broker="ctp_sim",
                decision_at=initial.master_publication.available_at - timedelta(seconds=1),
                session_factory=session_factory,
            )

        with engine.connect() as connection:
            master_id = connection.scalar(
                text(
                    "SELECT id FROM contract_master_publication_records "
                    "WHERE publication_hash = :publication_hash"
                ),
                {"publication_hash": initial.master_publication.publication_hash},
            )
            registry_id = connection.scalar(
                text(
                    "SELECT id FROM ctp_contract_registry_publication_records "
                    "WHERE publication_hash = :publication_hash"
                ),
                {"publication_hash": initial.registry_publication.publication_hash},
            )
        assert isinstance(master_id, int)
        assert isinstance(registry_id, int)
        for statement, parameters in (
            (
                "UPDATE contract_master_publication_records SET master_version = 'tampered' "
                "WHERE id = :record_id",
                {"record_id": master_id},
            ),
            (
                "DELETE FROM contract_master_publication_records WHERE id = :record_id",
                {"record_id": master_id},
            ),
            ("TRUNCATE TABLE contract_master_publication_records", {}),
            (
                "UPDATE ctp_contract_registry_publication_records SET broker = 'ctp' "
                "WHERE id = :record_id",
                {"record_id": registry_id},
            ),
            (
                "DELETE FROM ctp_contract_registry_publication_records WHERE id = :record_id",
                {"record_id": registry_id},
            ),
            ("TRUNCATE TABLE ctp_contract_registry_publication_records", {}),
        ):
            _assert_mutation_rejected(engine, statement, parameters)
    finally:
        if engine is not None:
            engine.dispose()
        get_settings.cache_clear()
