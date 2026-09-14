"""Contract-owned durable work; supplier request windows remain internal children."""

from sqlalchemy import Connection


def initialize(c: Connection) -> None:
    c.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS data_contract_collections (
            scope text PRIMARY KEY REFERENCES data_sync_contracts(ts_code),
            start_date date NOT NULL,
            end_date date NOT NULL CHECK(end_date>=start_date),
            status text NOT NULL DEFAULT 'COLLECTING'
                CHECK(status IN ('COLLECTING','VERIFYING','REJECTED','PUBLISHED')),
            reason text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS data_contract_requests (
            scope text NOT NULL REFERENCES data_contract_collections(scope),
            request_id uuid NOT NULL REFERENCES data_sync_jobs(request_id),
            PRIMARY KEY(scope,request_id)
        );
        CREATE INDEX IF NOT EXISTS data_contract_request_owner
            ON data_contract_requests(request_id,scope);
        CREATE TABLE IF NOT EXISTS data_contract_source_releases (
            source_id uuid PRIMARY KEY REFERENCES data_sources(source_id),
            scope text NOT NULL REFERENCES data_contract_collections(scope),
            created_at timestamptz NOT NULL DEFAULT now(),
            reason text NOT NULL
        );
        DROP TRIGGER IF EXISTS immutable ON data_contract_source_releases;
        CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON data_contract_source_releases
            FOR EACH ROW EXECUTE FUNCTION data_sync_immutable_receipt();
        CREATE TABLE IF NOT EXISTS data_contract_publications (
            publication_id text PRIMARY KEY,
            scope text NOT NULL REFERENCES data_contract_collections(scope),
            manifest jsonb NOT NULL,
            package_hash text NOT NULL,
            package_bytes bigint NOT NULL CHECK(package_bytes>0),
            path text NOT NULL UNIQUE,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        DROP TRIGGER IF EXISTS immutable ON data_contract_publications;
        CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON data_contract_publications
            FOR EACH ROW EXECUTE FUNCTION data_sync_immutable_receipt();
    """)
