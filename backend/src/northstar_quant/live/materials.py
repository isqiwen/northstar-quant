"""Live-local receipt of fixed candidates; receiving never creates a trading session."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    Connection,
    Engine,
    MetaData,
    String,
    Table,
    func,
    select,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from northstar_quant.persistence.sql import UTCDateTime, write_transaction
from northstar_quant.strategies.artifacts import verify_candidate

_metadata = MetaData()
_materials = Table(
    "live_strategy_materials",
    _metadata,
    Column("candidate_id", String(64), primary_key=True),
    Column("configuration_id", String(64), nullable=False, index=True),
    Column("document", JSON().with_variant(JSONB, "postgresql"), nullable=False),
    Column("received_at", UTCDateTime(), server_default=func.now(), nullable=False),
)


def initialize_materials(connection: Connection) -> None:
    _metadata.create_all(connection)
    if connection.dialect.name == "sqlite":
        for table in ("live_strategy_materials",):
            for action in ("UPDATE", "DELETE"):
                connection.exec_driver_sql(
                    f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} "
                    f"BEFORE {action} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'Confirmed facts are immutable'); END"
                )
        return
    connection.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION live_material_immutable() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'Live strategy materials are immutable'; END; $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS immutable ON live_strategy_materials;
        CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON live_strategy_materials
            FOR EACH ROW EXECUTE FUNCTION live_material_immutable();
    """)


class StrategyMaterials:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def accept(self, candidate: dict[str, Any]) -> dict[str, Any]:
        verified = verify_candidate(candidate, require_installed_revision=True)
        with write_transaction(self._engine) as connection:
            connection.execute(
                (sqlite_insert if connection.dialect.name == "sqlite" else pg_insert)(_materials)
                .values(
                    candidate_id=verified["candidate_id"],
                    configuration_id=verified["document"]["configuration"]["configuration_id"],
                    document=verified,
                )
                .on_conflict_do_nothing()
            )
            saved = (
                connection.execute(
                    select(_materials).where(_materials.c.candidate_id == verified["candidate_id"])
                )
                .mappings()
                .one()
            )
            if (
                saved["document"] != verified
                or saved["configuration_id"]
                != verified["document"]["configuration"]["configuration_id"]
            ):
                raise ValueError("received candidate differs from its stored identity")
        return {
            "candidate_id": verified["candidate_id"],
            "status": "RECEIVED",
            "execution_authorized": False,
        }

    def get_configuration(
        self,
        configuration_id: str,
        *,
        candidate_id: str | None = None,
        require_installed_revision: bool = True,
    ) -> dict[str, Any]:
        """Read a fixed local binding; activation must match the installed revision.

        Offline evidence verification may compare retained current-format facts
        without executing the historical implementation or granting admission.
        """
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(_materials)
                    .where(_materials.c.configuration_id == configuration_id)
                    .where(
                        true()
                        if candidate_id is None
                        else _materials.c.candidate_id == candidate_id
                    )
                    .order_by(_materials.c.received_at.desc(), _materials.c.candidate_id)
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError("Live has not received this fixed configuration")
        verified = verify_candidate(
            row["document"], require_installed_revision=require_installed_revision
        )
        configuration = verified["document"]["configuration"]
        if (
            configuration["configuration_id"] != configuration_id
            or verified["candidate_id"] != row["candidate_id"]
        ):
            raise ValueError("received configuration index differs from its artifact")
        return {**configuration, "candidate_id": verified["candidate_id"]}

    def configurations(self) -> list[dict[str, Any]]:
        """Review received configuration identities; listing never admits execution."""
        result: dict[str, dict[str, Any]] = {}
        for item in self.list():
            configuration = item["document"]["document"]["configuration"]
            result.setdefault(
                configuration["configuration_id"],
                {**configuration, "candidate_id": item["candidate_id"]},
            )
        return list(result.values())

    def list(self) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(_materials)
                    .order_by(_materials.c.received_at.desc(), _materials.c.candidate_id)
                    .limit(100)
                )
                .mappings()
                .all()
            )
        result = []
        for row in rows:
            verified = verify_candidate(row["document"])
            if (
                verified["candidate_id"] != row["candidate_id"]
                or verified["document"]["configuration"]["configuration_id"]
                != row["configuration_id"]
            ):
                raise ValueError("received material index differs from its fixed content")
            result.append(
                {
                    **{
                        key: value.isoformat() if isinstance(value, datetime) else value
                        for key, value in row.items()
                    },
                    "status": "RECEIVED",
                    "execution_authorized": False,
                }
            )
        return result

    def verify_all(self) -> int:
        """Verify every retained receipt, independently of workspace pagination."""
        count = 0
        with self._engine.connect().execution_options(yield_per=100) as connection:
            for row in connection.execute(select(_materials)).mappings():
                verified = verify_candidate(row["document"])
                if (
                    verified["candidate_id"] != row["candidate_id"]
                    or verified["document"]["configuration"]["configuration_id"]
                    != row["configuration_id"]
                ):
                    raise ValueError("received material index differs from its fixed content")
                count += 1
        return count
