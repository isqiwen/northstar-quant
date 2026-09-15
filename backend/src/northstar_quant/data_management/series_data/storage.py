"""Durable series discovery, request ownership and immutable interval publications."""

from sqlalchemy import Connection


def initialize(c: Connection) -> None:
    c.exec_driver_sql("""
    CREATE INDEX IF NOT EXISTS data_sync_attempt_latest
        ON data_sync_attempts(started_at DESC,generation DESC);
    CREATE TABLE IF NOT EXISTS data_series_collections (
        dataset text NOT NULL, scope text NOT NULL,
        exchange text NOT NULL, product text NOT NULL, name text NOT NULL,
        start_date date NOT NULL, planned_through date,
        PRIMARY KEY(dataset,scope)
    );
    CREATE TABLE IF NOT EXISTS data_series_requests (
        dataset text NOT NULL, scope text NOT NULL,
        request_id uuid NOT NULL REFERENCES data_sync_jobs,
        processed_receipt_id uuid REFERENCES data_sync_receipts, publication_error text,
        PRIMARY KEY(dataset,scope,request_id),
        FOREIGN KEY(dataset,scope) REFERENCES data_series_collections
    );
    CREATE INDEX IF NOT EXISTS data_series_request_owner ON data_series_requests(request_id);
    CREATE TABLE IF NOT EXISTS data_series_publications (
        publication_id text PRIMARY KEY,
        dataset text NOT NULL, scope text NOT NULL,
        receipt_id uuid NOT NULL REFERENCES data_sync_receipts,
        start_date date NOT NULL, end_date date NOT NULL,
        manifest jsonb NOT NULL, manifest_hash text NOT NULL,
        manifest_bytes bigint NOT NULL CHECK(manifest_bytes>0),
        path text NOT NULL UNIQUE, created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(dataset,scope,receipt_id)
    );
    CREATE INDEX IF NOT EXISTS data_series_publication_scope
        ON data_series_publications(dataset,scope,end_date,created_at);
    DROP TRIGGER IF EXISTS immutable ON data_series_publications;
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON data_series_publications
        FOR EACH ROW EXECUTE FUNCTION data_sync_immutable_receipt();
    """)
