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
