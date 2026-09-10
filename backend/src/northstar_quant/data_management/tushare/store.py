"""Persistent synchronization configuration, windows and immutable download revisions."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text


def initialize(connection: Connection) -> None:
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS data_sync_settings (
            singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
            revision bigint NOT NULL DEFAULT 1,
            enabled boolean NOT NULL DEFAULT false,
            lookback integer NOT NULL DEFAULT 5 CHECK(lookback BETWEEN 1 AND 30),
            requests_per_minute integer NOT NULL DEFAULT 60 CHECK(requests_per_minute BETWEEN
                1 AND 500),
            next_request_at timestamptz NOT NULL DEFAULT now(),
            refresh_at timestamptz NOT NULL DEFAULT now(),
            planned_at timestamptz,
            error text,
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS data_sync_contracts (
            ts_code text PRIMARY KEY,
            exchange text NOT NULL,
            product text NOT NULL,
            kind text NOT NULL,
            details jsonb NOT NULL,
            planning_error text,
            planned_revision bigint NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS data_sync_calendar (
            exchange text NOT NULL,
            cal_date date NOT NULL,
            is_open boolean NOT NULL,
            PRIMARY KEY(exchange,cal_date)
        );
        CREATE TABLE IF NOT EXISTS data_sync_jobs (
            request_id uuid PRIMARY KEY,
            identity text NOT NULL UNIQUE,
            dataset text NOT NULL,
            scope text NOT NULL,
            parameters jsonb NOT NULL,
            start_at text NOT NULL,
            end_at text NOT NULL,
            status text NOT NULL DEFAULT 'PENDING'
                CHECK(status IN ('PENDING','RUNNING','WAITING','BLOCKED','VALIDATED','SPLIT')),
            generation uuid,
            source_generation uuid,
            attempts integer NOT NULL DEFAULT 0,
            next_at timestamptz NOT NULL DEFAULT now(),
            checked_at timestamptz,
            receipt_id uuid,
            error text,
            code_revision text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS data_sync_ready ON data_sync_jobs(status,next_at);
        CREATE TABLE IF NOT EXISTS data_sync_receipts (
            receipt_id uuid PRIMARY KEY,
            request_id uuid NOT NULL REFERENCES data_sync_jobs,
            source_hash text NOT NULL,
            source_bytes bigint NOT NULL,
            content_hash text NOT NULL,
            row_count bigint NOT NULL,
            manifest_hash text NOT NULL,
            manifest_bytes bigint NOT NULL,
            parquet_hash text NOT NULL,
            parquet_bytes bigint NOT NULL,
            quality jsonb NOT NULL,
            code_revision text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(request_id, content_hash)
        );
        CREATE INDEX IF NOT EXISTS data_sync_scope ON data_sync_jobs(dataset,scope,start_at,end_at);
        CREATE INDEX IF NOT EXISTS data_sync_revisions
            ON data_sync_receipts(request_id,created_at DESC);
        CREATE TABLE IF NOT EXISTS data_sync_coverage (
            request_id uuid PRIMARY KEY REFERENCES data_sync_jobs,
            receipt_id uuid NOT NULL REFERENCES data_sync_receipts,
            checked_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS data_sync_attempts (
            generation uuid PRIMARY KEY,
            request_id uuid NOT NULL REFERENCES data_sync_jobs,
            started_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            outcome text,
            error text,
            source_hash text,
            source_bytes bigint,
            parent_generation uuid REFERENCES data_sync_attempts(generation),
            receipt_id uuid REFERENCES data_sync_receipts,
            code_revision text
        );
        ALTER TABLE data_sync_jobs ADD COLUMN IF NOT EXISTS source_generation uuid;
        ALTER TABLE data_sync_attempts ADD COLUMN IF NOT EXISTS parent_generation uuid
            REFERENCES data_sync_attempts(generation);
        ALTER TABLE data_sync_attempts ADD COLUMN IF NOT EXISTS receipt_id uuid
            REFERENCES data_sync_receipts;
        ALTER TABLE data_sync_attempts ADD COLUMN IF NOT EXISTS code_revision text;
        CREATE OR REPLACE FUNCTION data_sync_immutable_receipt() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'Downloaded revisions are immutable'; END;
        $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS immutable ON data_sync_receipts;
        CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON data_sync_receipts
            FOR EACH ROW EXECUTE FUNCTION data_sync_immutable_receipt();
    """)
    connection.exec_driver_sql(
        "INSERT INTO data_sync_settings(singleton) VALUES(true) ON CONFLICT DO NOTHING"
    )


def serial(row: Any) -> dict[str, Any]:
    return {
        key: value.isoformat()
        if isinstance(value, (date, datetime))
        else str(value)
        if isinstance(value, UUID)
        else value
        for key, value in row.items()
    }


def settings(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        return serial(connection.execute(text("SELECT * FROM data_sync_settings")).mappings().one())


def job(engine: Engine, request_id: UUID) -> dict[str, Any]:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("SELECT * FROM data_sync_jobs WHERE request_id=:id"), {"id": request_id}
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise LookupError("同步分片不存在")
        result = serial(row)
        source = (
            connection.execute(
                text("""SELECT generation,source_hash,source_bytes FROM data_sync_attempts
            WHERE request_id=:id AND source_hash IS NOT NULL AND parent_generation IS NULL
            AND finished_at IS NOT NULL ORDER BY started_at DESC,generation DESC LIMIT 1"""),
                {"id": request_id},
            )
            .mappings()
            .one_or_none()
        )
        result["reprocess_source"] = serial(source) if source else None
        result["attempts_detail"] = [
            serial(value)
            for value in connection.execute(
                text("""
            SELECT * FROM data_sync_attempts WHERE request_id=:id ORDER BY started_at DESC LIMIT 50
        """),
                {"id": request_id},
            ).mappings()
        ]
        return result
