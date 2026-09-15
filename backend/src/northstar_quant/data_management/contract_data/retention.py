"""Bounded rejected-source retention, with reference revalidation under the source gate."""

from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from ..files import SourceFiles
from ..library import manifest
from ..maintenance import try_freeze_sources
from .lifecycle import completed

_CANDIDATES = """SELECT s.source_id,s.content_hash,s.byte_count,w.scope
                FROM data_sources s JOIN data_sync_attempts a ON a.generation=s.source_id
                JOIN data_sync_jobs j USING(request_id)
                JOIN LATERAL (
                    SELECT cr.scope FROM data_contract_requests cr
                    JOIN data_contract_collections w ON w.scope=cr.scope
                    WHERE cr.request_id=j.request_id AND w.status='REJECTED'
                    AND cr.scope=ANY(CAST(:eligible AS text[]))
                    ORDER BY cr.scope LIMIT 1
                ) w ON true
                WHERE s.input_kind='TUSHARE_RESPONSE'
                AND (CAST(:ids AS uuid[]) IS NULL OR s.source_id=ANY(CAST(:ids AS uuid[])))
                AND w.scope=ANY(CAST(:eligible AS text[]))
                AND NOT EXISTS(SELECT 1 FROM data_contract_requests owners
                    JOIN data_contract_collections kept ON kept.scope=owners.scope
                    WHERE owners.request_id=j.request_id
                    AND (kept.status<>'REJECTED'
                        OR NOT kept.scope=ANY(CAST(:eligible AS text[]))))
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
                ORDER BY s.source_id LIMIT 16"""


def _candidates(c: Connection, ids: list[UUID] | None = None) -> list[dict[str, Any]]:
    c.execute(text("SET LOCAL max_parallel_workers_per_gather=0"))
    c.execute(text("SET LOCAL statement_timeout='2s'"))
    eligible = []
    for contract in c.execute(
        text("""SELECT d.* FROM data_sync_contracts d
        JOIN data_contract_collections w ON w.scope=d.ts_code
        WHERE w.status='REJECTED'""")
    ).mappings():
        try:
            completed(contract)
        except ValueError:
            continue
        eligible.append(contract["ts_code"])
    if not eligible:
        return []
    return [
        dict(r) for r in c.execute(text(_CANDIDATES), dict(eligible=eligible, ids=ids)).mappings()
    ]


def release_rejected(engine: Engine, files: SourceFiles) -> int:
    # Broad discovery never holds the admission gate. A changed reference or owner
    # between discovery and locking is checked again, not trusted from this snapshot.
    with engine.begin() as c:
        discovered = _candidates(c)
    if not discovered:
        return 0
    with engine.begin() as ownership:
        if not try_freeze_sources(ownership):
            return 0
        with engine.begin() as c:
            candidates = _candidates(c, [r["source_id"] for r in discovered])
            for item in candidates:
                c.execute(
                    text("""INSERT INTO data_contract_source_releases(source_id,scope,reason)
                    VALUES(:source_id,:scope,'合约已拒绝；无研究、固定版本或备份引用的原始响应')
                    ON CONFLICT DO NOTHING"""),
                    item,
                )
        if not candidates:
            return 0
        # Protect aliases and every existing fixed-artifact reference, but only
        # materialize references for these hashes rather than the complete catalog.
        ownership.execute(text("SET LOCAL statement_timeout='2s'"))
        protected = {
            str(r["content_hash"])
            for r in manifest(ownership, content_hashes=[r["content_hash"] for r in candidates])
        }
        removed = 0
        for item in candidates:
            if (
                item["content_hash"] not in protected
                and files.inspect(item["content_hash"], item["byte_count"]) != "MISSING"
            ):
                files.remove_verified(item["content_hash"], item["byte_count"])
                removed += 1
        return removed
