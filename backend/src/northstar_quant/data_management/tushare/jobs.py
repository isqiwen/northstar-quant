"""Durable bounded sync requests; one owner downloads and hands off to DataLibrary."""

import json
import os
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import RowMapping

from northstar_quant import code_revision

from ..library import DataLibrary
from ..maintenance import library_write
from ..research import ImportSpec, _digest
from .acquisition import fetch
from .request import fixed_spec

_LOCK = 0x4E53515453594E


def initialize(connection: Connection) -> None:
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS data_sync_jobs (
            request_id uuid PRIMARY KEY,
            request_hash text NOT NULL,
            parameters jsonb NOT NULL,
            code_revision text NOT NULL,
            status text NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'RECEIVED', 'FAILED')),
            attempt_id uuid REFERENCES data_processing_attempts(attempt_id),
            error text,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CHECK ((status = 'RECEIVED') = (attempt_id IS NOT NULL))
        );
        CREATE OR REPLACE FUNCTION data_protect_sync_job() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.status IN ('RECEIVED', 'FAILED') THEN
                RAISE EXCEPTION 'Terminal sync jobs are immutable';
            END IF;
            IF (NEW.request_id, NEW.request_hash, NEW.parameters, NEW.code_revision, NEW.created_at)
                IS DISTINCT FROM
                (OLD.request_id, OLD.request_hash, OLD.parameters,
                 OLD.code_revision, OLD.created_at)
            THEN RAISE EXCEPTION 'Sync inputs are immutable'; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS immutable ON data_sync_jobs;
        CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON data_sync_jobs
            FOR EACH ROW EXECUTE FUNCTION data_protect_sync_job();
    """)


def submit(engine: Engine, spec: ImportSpec, request_id: UUID) -> dict[str, object]:
    """Persist a non-secret request before network access; replay retrieves its first result."""
    parameters = fixed_spec(spec).to_mapping()
    revision = code_revision()
    digest = _digest({"parameters": parameters, "code_revision": revision})
    now = datetime.now(UTC)
    with library_write(engine), engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO data_sync_jobs
                (request_id, request_hash, parameters, code_revision,
                 status, created_at, updated_at)
            VALUES (:id, :hash, CAST(:parameters AS jsonb), :revision, 'PENDING', :now, :now)
            ON CONFLICT (request_id) DO NOTHING
        """),
            {
                "id": request_id,
                "hash": digest,
                "parameters": json.dumps(parameters),
                "revision": revision,
                "now": now,
            },
        )
        existing = connection.scalar(
            text("SELECT request_hash FROM data_sync_jobs WHERE request_id=:id"), {"id": request_id}
        )
        if existing != digest:
            raise ValueError(
                "sync request UUID is already bound to different inputs or implementation"
            )
    return get(engine, request_id)


def get(engine: Engine, request_id: UUID) -> dict[str, object]:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("SELECT * FROM data_sync_jobs WHERE request_id=:id"), {"id": request_id}
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise LookupError("sync request not found")
    return _serialize(row)


def _serialize(row: RowMapping) -> dict[str, object]:
    return {
        key: str(value)
        if isinstance(value, UUID)
        else value.isoformat()
        if isinstance(value, datetime)
        else value
        for key, value in row.items()
    }


def recent(engine: Engine) -> list[dict[str, object]]:
    with engine.connect() as connection:
        rows = (
            connection.execute(
                text("SELECT * FROM data_sync_jobs ORDER BY created_at DESC, request_id LIMIT 50")
            )
            .mappings()
            .all()
        )
    return [_serialize(row) for row in rows]


def _update(
    engine: Engine,
    request_id: UUID,
    status: str,
    *,
    attempt_id: UUID | None = None,
    error: str | None = None,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("""
            UPDATE data_sync_jobs SET status=:status, attempt_id=:attempt_id, error=:error,
                updated_at=:now WHERE request_id=:id
        """),
            {
                "id": request_id,
                "status": status,
                "attempt_id": attempt_id,
                "error": error,
                "now": datetime.now(UTC),
            },
        )


def process_next(library: DataLibrary) -> dict[str, object] | None:
    """An interrupted download fails explicitly; an already committed receipt is recovered."""
    engine = library._engine
    with library_write(engine), engine.begin() as claim:
        if not claim.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _LOCK}):
            return None
        with engine.connect() as connection:
            interrupted = (
                connection.execute(
                    text("SELECT request_id FROM data_sync_jobs WHERE status='RUNNING'")
                )
                .scalars()
                .all()
            )
        for request_id in interrupted:
            with engine.connect() as connection:
                attempt_id = connection.scalar(
                    text("""SELECT p.attempt_id FROM data_processing_attempts p
                        JOIN data_sources s ON s.source_id=p.source_id
                        JOIN data_sync_jobs j ON j.request_id=CAST(p.request_id AS uuid)
                        WHERE p.request_id=:id AND p.parameters=j.parameters
                        AND p.code_revision=j.code_revision AND s.input_kind='TUSHARE_JSON'
                        AND s.source_name='TUSHARE'"""),
                    {"id": str(request_id)},
                )
            _update(
                engine,
                request_id,
                "RECEIVED" if attempt_id else "FAILED",
                attempt_id=attempt_id,
                error=None
                if attempt_id
                else "Download interrupted; submit a new explicit request to retry",
            )
        with engine.connect() as connection:
            request_id = connection.scalar(
                text(
                    "SELECT request_id FROM data_sync_jobs WHERE status='PENDING' "
                    "ORDER BY created_at, request_id LIMIT 1"
                )
            )
        if request_id is None:
            return None
        job = get(engine, request_id)
        _update(engine, request_id, "RUNNING")
        try:
            if job["code_revision"] != code_revision():
                raise ValueError("sync implementation changed; submit a new explicit request")
            spec = ImportSpec.from_mapping(cast(dict[str, object], job["parameters"]))
            content = fetch(spec, os.environ.get("NORTHSTAR_TUSHARE_TOKEN", ""))
            attempt = library.submit(
                content,
                filename=f"tushare-{spec.symbol}-{spec.trading_day}.json",
                source_name="TUSHARE",
                use_basis="Tushare subscription: confirmed personal research and local retention.",
                allow_retention=True,
                allow_download=False,
                input_kind="TUSHARE_JSON",
                spec=spec.to_mapping(),
                request_id=str(request_id),
            )
            _update(engine, request_id, "RECEIVED", attempt_id=UUID(str(attempt["attempt_id"])))
        except (ValueError, OSError, LookupError) as error:
            _update(engine, request_id, "FAILED", error=str(error)[:1024])
        return get(engine, request_id)
