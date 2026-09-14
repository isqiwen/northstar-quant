"""Release rejected raw inputs only after rechecking every retained reference root."""

from sqlalchemy import Engine, text

from ..files import SourceFiles
from ..library import manifest
from ..maintenance import try_freeze_sources


def release_rejected(engine: Engine, files: SourceFiles) -> int:
    with engine.begin() as ownership:
        if not try_freeze_sources(ownership):
            return 0
        # Intent is durable before unlink. A crash leaves an ordinary unreferenced
        # file, handled by the existing verified orphan cleanup, not a broken pin.
        with engine.begin() as c:
            candidates = (
                c.execute(
                    text("""SELECT DISTINCT s.source_id,s.content_hash,s.byte_count,w.scope
                FROM data_sources s JOIN data_sync_attempts a ON a.generation=s.source_id
                JOIN data_sync_jobs j USING(request_id)
                JOIN data_contract_requests cr USING(request_id)
                JOIN data_contract_collections w ON w.scope=cr.scope AND w.status='REJECTED'
                WHERE s.input_kind='TUSHARE_RESPONSE'
                AND NOT EXISTS(SELECT 1 FROM data_contract_requests owners
                    JOIN data_contract_collections kept ON kept.scope=owners.scope
                    WHERE owners.request_id=j.request_id AND kept.status<>'REJECTED')
                AND NOT EXISTS(SELECT 1 FROM data_contract_source_releases x
                    WHERE x.source_id=s.source_id)
                AND NOT EXISTS(SELECT 1 FROM data_sync_receipts r
                    WHERE r.source_hash=s.content_hash)
                AND NOT EXISTS(SELECT 1 FROM data_processing_attempts p
                    WHERE p.source_id=s.source_id)
                AND NOT EXISTS(SELECT 1 FROM data_sources child
                    WHERE child.upstream_source_id=s.source_id)
                AND NOT EXISTS(SELECT 1 FROM data_sync_jobs active
                    WHERE active.source_generation=s.source_id)
                AND NOT EXISTS(SELECT 1 FROM data_backups b,
                    jsonb_array_elements(b.source_references) p
                    WHERE p->>'content_hash'=s.content_hash)
                ORDER BY s.source_id LIMIT 16""")
                )
                .mappings()
                .all()
            )
            for item in candidates:
                c.execute(
                    text("""INSERT INTO data_contract_source_releases(source_id,scope,reason)
                    VALUES(:source_id,:scope,'合约已拒绝；无研究、固定版本或备份引用的原始响应')
                    ON CONFLICT DO NOTHING"""),
                    dict(item),
                )
        protected = {str(item["content_hash"]) for item in manifest(ownership)}
        removed = 0
        for item in candidates:
            if (
                item["content_hash"] not in protected
                and files.inspect(item["content_hash"], item["byte_count"]) != "MISSING"
            ):
                files.remove_verified(item["content_hash"], item["byte_count"])
                removed += 1
        return removed
