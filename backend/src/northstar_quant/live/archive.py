"""Archive admission, separate from the Live kernel's local recovery authority."""

import hashlib
import json
from uuid import UUID

from sqlalchemy import Connection, Engine, text


def initialize_archive(connection: Connection) -> None:
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS live_archive_records (
            writer_id uuid NOT NULL, sequence bigint NOT NULL CHECK (sequence > 0),
            content_hash char(64) NOT NULL, payload jsonb NOT NULL,
            received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (writer_id, sequence)
        );
        CREATE OR REPLACE FUNCTION preserve_live_archive() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'live archive facts are immutable'; END; $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS immutable ON live_archive_records;
        CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON live_archive_records
            FOR EACH ROW EXECUTE FUNCTION preserve_live_archive();
    """)


def accept(engine: Engine, writer_id: UUID, sequence: int, payload: dict[str, object]) -> str:
    """A duplicate acknowledges identical content only, after a durable archive commit.

    Called by the future isolated uploader, never by the kernel's trading path.
    This archive confirmation neither authorizes sending nor supplies failover authority.
    """
    content = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if type(sequence) is not int or sequence <= 0 or len(content.encode()) > 1024 * 1024:
        raise ValueError("invalid bounded archive record")
    digest = hashlib.sha256(content.encode()).hexdigest()
    with engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL synchronous_commit = on")
        connection.execute(
            text("""INSERT INTO live_archive_records
            (writer_id, sequence, content_hash, payload)
            VALUES (:writer, :sequence, :digest, CAST(:payload AS jsonb))
            ON CONFLICT DO NOTHING"""),
            {"writer": writer_id, "sequence": sequence, "digest": digest, "payload": content},
        )
        observed = connection.execute(
            text("""SELECT content_hash FROM live_archive_records
            WHERE writer_id=:writer AND sequence=:sequence"""),
            {"writer": writer_id, "sequence": sequence},
        ).scalar_one()
        if observed != digest:
            raise ValueError("archive identity conflicts with retained fact")
    return digest
