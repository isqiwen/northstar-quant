"""Durable, expiring Live commands; an indeterminate attempt is never executed twice."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Connection,
    Engine,
    MetaData,
    String,
    Table,
    inspect,
    update,
)
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from northstar_quant.live.storage import write_transaction

_metadata = MetaData()
_commands = Table(
    "live_commands",
    _metadata,
    Column("command_id", String(36), primary_key=True),
    Column("runtime_id", String(36), nullable=False),
    Column("path", String, nullable=False),
    Column("operator", String),
    Column("input", JSON, nullable=False),
    Column("expires_at", String, nullable=False),
    Column("status", String, nullable=False),
    CheckConstraint("status IN ('RUNNING', 'COMPLETED', 'REJECTED', 'UNKNOWN')"),
    Column("result", JSON),
    Column("error_code", String),
    Column("created_at", String, nullable=False),
    Column("finished_at", String),
)


def initialize_live_commands(connection: Connection) -> None:
    _metadata.create_all(connection)
    if "operator" not in {
        column["name"] for column in inspect(connection).get_columns("live_commands")
    }:
        # Historical commands retain unknown attribution; never invent an operator for old facts.
        connection.exec_driver_sql("ALTER TABLE live_commands ADD COLUMN operator VARCHAR")
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS live_command_identity
            BEFORE UPDATE ON live_commands
            WHEN OLD.command_id IS NOT NEW.command_id
              OR OLD.runtime_id IS NOT NEW.runtime_id
              OR OLD.operator IS NOT NEW.operator
              OR OLD.path IS NOT NEW.path OR OLD.input IS NOT NEW.input
              OR OLD.expires_at IS NOT NEW.expires_at
              OR OLD.created_at IS NOT NEW.created_at
              OR OLD.status != 'RUNNING' OR NEW.status = 'RUNNING'
              OR NEW.finished_at IS NULL
            BEGIN SELECT RAISE(ABORT, 'Live command facts cannot be rewritten'); END
        """)
        connection.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS live_command_retention
            BEFORE DELETE ON live_commands
            BEGIN SELECT RAISE(ABORT, 'Live command facts cannot be deleted'); END
        """)
    else:
        connection.exec_driver_sql("""
            CREATE OR REPLACE FUNCTION live_command_preserve() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'Live command facts cannot be deleted';
                END IF;
                IF OLD.command_id IS DISTINCT FROM NEW.command_id
                   OR OLD.runtime_id IS DISTINCT FROM NEW.runtime_id
                   OR OLD.operator IS DISTINCT FROM NEW.operator
                   OR OLD.path IS DISTINCT FROM NEW.path
                   OR OLD.input::text IS DISTINCT FROM NEW.input::text
                   OR OLD.expires_at IS DISTINCT FROM NEW.expires_at
                   OR OLD.created_at IS DISTINCT FROM NEW.created_at
                   OR OLD.status != 'RUNNING' OR NEW.status = 'RUNNING'
                   OR NEW.finished_at IS NULL THEN
                    RAISE EXCEPTION 'Live command facts cannot be rewritten';
                END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql;
            DROP TRIGGER IF EXISTS live_command_identity ON live_commands;
            CREATE TRIGGER live_command_identity BEFORE UPDATE OR DELETE ON live_commands
                FOR EACH ROW EXECUTE FUNCTION live_command_preserve();
        """)


class CommandConflict(ValueError):
    """A command cannot be admitted against its fixed input or runtime."""


class Commands:
    def __init__(self, engine: Engine, runtime_id: UUID) -> None:
        self._engine = engine
        self.runtime_id = runtime_id

    def get(self, command_id: UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    _commands.select().where(_commands.c.command_id == str(command_id))
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError("Live command was not recorded")
        result = dict(row)
        for key in ("command_id", "runtime_id", "expires_at", "created_at", "finished_at"):
            result[key] = None if result[key] is None else str(result[key])
        if result["status"] == "RUNNING" and row["runtime_id"] != str(self.runtime_id):
            result["status"] = "UNKNOWN"
        result["request_id"] = result.pop("command_id")
        return result

    def execute(
        self,
        command_id: UUID,
        runtime_id: UUID,
        expires_at: datetime,
        path: str,
        body: dict[str, Any],
        effect: Callable[[], dict[str, Any]],
        *,
        operator: str,
    ) -> dict[str, Any]:
        if operator not in {"owner", "maintenance"}:
            raise CommandConflict("Live command requires a trusted operator")
        # Identity lookup precedes liveness/expiry: a completed acknowledgement is
        # still readable after restart, but it cannot become permission to repeat.
        try:
            saved = self.get(command_id)
        except LookupError:
            saved = None
        if saved is not None:
            if (
                saved["runtime_id"] != str(runtime_id)
                or datetime.fromisoformat(saved["expires_at"]) != expires_at
                or saved["path"] != path
                or saved["input"] != body
                or saved["operator"] != operator
            ):
                raise CommandConflict("Live command identity is bound to different input")
            return saved
        now = datetime.now(UTC)
        if runtime_id != self.runtime_id:
            raise CommandConflict("Live runtime changed; observe it before creating a new command")
        if expires_at.tzinfo is None or not 0 < (expires_at - now).total_seconds() <= 60:
            raise CommandConflict("Live command deadline must be in the next 60 seconds")
        insert = sqlite_insert if self._engine.dialect.name == "sqlite" else postgres_insert
        with write_transaction(self._engine) as connection:
            admitted = connection.execute(
                insert(_commands)
                .values(
                    command_id=str(command_id),
                    runtime_id=str(runtime_id),
                    path=path,
                    operator=operator,
                    input=body,
                    expires_at=expires_at.isoformat(),
                    status="RUNNING",
                    created_at=now.isoformat(),
                )
                .on_conflict_do_nothing(index_elements=["command_id"])
                .returning(_commands.c.command_id)
            ).scalar_one_or_none()
        if admitted is None:
            # Another request won admission; run the same comparison, never its effect.
            return self.execute(
                command_id, runtime_id, expires_at, path, body, effect, operator=operator
            )
        status = "UNKNOWN"
        result: dict[str, Any] | None = None
        error_code: str | None = "COMMAND_OUTCOME_UNKNOWN"
        try:
            if datetime.now(UTC) >= expires_at:
                status, error_code = "REJECTED", "COMMAND_EXPIRED"
            else:
                result = effect()
                status, error_code = "COMPLETED", None
        except Exception:
            # Even a ValueError may follow a committed write or a started worker.
            # Only admission/expiry failures prove that the effect never began.
            status, error_code = "UNKNOWN", "COMMAND_OUTCOME_UNKNOWN"
        with write_transaction(self._engine) as connection:
            connection.execute(
                update(_commands)
                .where(_commands.c.command_id == str(command_id))
                .values(
                    status=status,
                    result=result,
                    error_code=error_code,
                    finished_at=datetime.now(UTC).isoformat(),
                )
            )
        return self.get(command_id)
