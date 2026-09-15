CREATE INDEX IF NOT EXISTS data_sync_created ON data_sync_jobs(created_at);
        CREATE INDEX IF NOT EXISTS data_sync_dispatch ON data_sync_jobs
            (dataset,start_at,created_at,request_id) INCLUDE(end_at,next_at,source_generation)
            WHERE status IN ('PENDING','WAITING');
        CREATE INDEX IF NOT EXISTS data_sync_recent ON data_sync_jobs(dataset,end_at)
            WHERE status IN ('PENDING','WAITING');
        CREATE INDEX IF NOT EXISTS data_sync_reprocess ON data_sync_jobs(dataset,start_at)
            WHERE status IN ('PENDING','WAITING') AND source_generation IS NOT NULL;
        CREATE INDEX IF NOT EXISTS data_sync_permission ON data_sync_jobs(dataset)
            WHERE status='BLOCKED' AND error LIKE 'Tushare 权限不足%';
        CREATE INDEX IF NOT EXISTS data_sync_attempt_request
            ON data_sync_attempts(request_id,started_at DESC,generation DESC);

ALTER TABLE data_sync_settings ADD COLUMN IF NOT EXISTS api_next_at jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE data_sync_settings ALTER COLUMN requests_per_minute SET DEFAULT 500;
UPDATE data_sync_settings SET requests_per_minute=500 WHERE requests_per_minute=60;

CREATE INDEX IF NOT EXISTS data_sync_receipt_source ON data_sync_receipts(source_hash);
CREATE INDEX IF NOT EXISTS data_sync_receipt_manifest ON data_sync_receipts(manifest_hash);
CREATE INDEX IF NOT EXISTS data_sync_receipt_parquet ON data_sync_receipts(parquet_hash);
CREATE INDEX IF NOT EXISTS data_source_content ON data_sources(content_hash);
CREATE INDEX IF NOT EXISTS data_source_upstream ON data_sources(upstream_source_id)
    WHERE upstream_source_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS data_processing_source ON data_processing_attempts(source_id);
CREATE INDEX IF NOT EXISTS data_sync_active_source ON data_sync_jobs(source_generation)
    WHERE source_generation IS NOT NULL;
