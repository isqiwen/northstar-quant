"""Durable, expiring Live commands; an indeterminate attempt is never executed twice."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Connection, Engine, text


def initialize_live_commands(connection: Connection) -> None:
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS live_commands (
            command_id uuid PRIMARY KEY,
            runtime_id uuid NOT NULL,
            path text NOT NULL,
            input jsonb NOT NULL,
            expires_at timestamptz NOT NULL,
            status text NOT NULL CHECK (status IN ('RUNNING','COMPLETED','REJECTED','UNKNOWN')),
            result jsonb,
            error_code text,
            created_at timestamptz NOT NULL,
            finished_at timestamptz
        )
    """)


class Commands:
    def __init__(self, engine: Engine, runtime_id: UUID) -> None:
        self._engine = engine
        self.runtime_id = runtime_id

    def get(self, command_id: UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text("SELECT * FROM live_commands WHERE command_id=:id"), {"id": command_id}
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError("Live command was not recorded")
        result = dict(row)
        for key in ("command_id", "runtime_id", "expires_at", "created_at", "finished_at"):
            result[key] = None if result[key] is None else str(result[key])
        if result["status"] == "RUNNING" and row["runtime_id"] != self.runtime_id:
            result["status"] = "UNKNOWN"
        return result

    def execute(
        self,
        command_id: UUID,
        runtime_id: UUID,
        expires_at: datetime,
        path: str,
        body: dict[str, Any],
        effect: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
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
            ):
                raise HTTPException(409, "Live command identity is bound to different input")
            return saved
        now = datetime.now(UTC)
        if runtime_id != self.runtime_id:
            raise HTTPException(
                409, "Live runtime changed; observe it before creating a new command"
            )
        if expires_at.tzinfo is None or not 0 < (expires_at - now).total_seconds() <= 60:
            raise HTTPException(409, "Live command deadline must be in the next 60 seconds")
        with self._engine.begin() as connection:
            admitted = connection.execute(
                text("""
                    INSERT INTO live_commands
                    (command_id,runtime_id,path,input,expires_at,status,created_at)
                    VALUES (:id,:owner,:path,CAST(:input AS jsonb),:expiry,'RUNNING',:now)
                    ON CONFLICT (command_id) DO NOTHING RETURNING command_id
                """),
                {
                    "id": command_id,
                    "owner": runtime_id,
                    "path": path,
                    "input": json.dumps(body, sort_keys=True, allow_nan=False),
                    "expiry": expires_at,
                    "now": now,
                },
            ).scalar_one_or_none()
        if admitted is None:
            # Another request won admission; run the same comparison, never its effect.
            return self.execute(command_id, runtime_id, expires_at, path, body, effect)
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
        with self._engine.begin() as connection:
            connection.execute(
                text("""
                    UPDATE live_commands SET status=:status,result=CAST(:result AS jsonb),
                        error_code=:error,finished_at=:finished WHERE command_id=:id
                """),
                {
                    "id": command_id,
                    "status": status,
                    "result": json.dumps(result, allow_nan=False),
                    "error": error_code,
                    "finished": datetime.now(UTC),
                },
            )
        return self.get(command_id)
