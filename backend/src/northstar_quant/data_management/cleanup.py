"""Bounded orphan cleanup; all retained publications and backup references are roots."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import Connection, Engine, text

from .files import SourceFiles
from .library import manifest
from .maintenance import freeze_sources
from .tushare.publication import storage


def content_id(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def initialize(c: Connection) -> None:
    c.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS data_cleanup_receipts (
            plan_id varchar(64) PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT now(),
            plan jsonb NOT NULL,
            status text NOT NULL CHECK (status IN ('STARTED','SUCCEEDED','FAILED')),
            result jsonb
        );
        CREATE OR REPLACE FUNCTION cleanup_transition() RETURNS trigger AS $$
        BEGIN
          IF TG_OP='DELETE' OR OLD.status<>'STARTED' OR NEW.status='STARTED'
             OR ROW(OLD.plan_id,OLD.created_at,OLD.plan)
                IS DISTINCT FROM ROW(NEW.plan_id,NEW.created_at,NEW.plan)
          THEN RAISE EXCEPTION 'cleanup receipts are immutable'; END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS immutable ON data_cleanup_receipts;
        CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON data_cleanup_receipts
          FOR EACH ROW EXECUTE FUNCTION cleanup_transition();
    """)


def _plan(c: Connection, stores: dict[str, SourceFiles]) -> dict[str, Any]:
    previous = [
        tuple(row)
        for row in c.execute(
            text("SELECT plan_id,status FROM data_cleanup_receipts ORDER BY plan_id")
        )
    ]
    references = manifest(c)
    for row in c.execute(text("SELECT source_references FROM data_backups")):
        references.extend(row[0])
    protected = {str(item["content_hash"]) for item in references}
    selected: list[dict[str, Any]] = []
    candidates = 0
    for name, files in stores.items():
        for item in files.inventory():
            if item.content_hash in protected:
                continue
            candidates += 1
            if len(selected) < 500:
                selected.append({"store": name, **item.to_dict()})
    return {
        "revision": "unreferenced-objects/1",
        "previous_operations": content_id(previous),
        "roots": {name: str(files.root) for name, files in stores.items()},
        "reference_identity": content_id(sorted(protected)),
        "protected_objects": len(protected),
        "orphan_count": candidates,
        "objects": selected,
        "bytes": sum(item["byte_count"] for item in selected),
        "note": "最多处理 500 个无引用对象；原始版本、研究输入及备份引用全部保留；不清理 staging。",
    }


def preview(engine: Engine, archive: SourceFiles) -> dict[str, Any]:
    if engine.dialect.name != "postgresql":
        raise ValueError("无引用对象清理只适用于 Data Hub")
    stores = {"source": archive, "published": storage()}
    with engine.begin() as c:
        freeze_sources(c)
        plan = _plan(c, stores)
    return {"plan_id": content_id(plan), **plan}


def execute(engine: Engine, archive: SourceFiles, plan_id: str) -> dict[str, Any]:
    if engine.dialect.name != "postgresql":
        raise ValueError("无引用对象清理只适用于 Data Hub")
    stores = {"source": archive, "published": storage()}
    with engine.begin() as ownership:
        freeze_sources(ownership)
        old = (
            ownership.execute(
                text("SELECT status,result FROM data_cleanup_receipts WHERE plan_id=:id"),
                {"id": plan_id},
            )
            .mappings()
            .first()
        )
        if old is not None:
            return {"plan_id": plan_id, **dict(old)}
        plan = _plan(ownership, stores)
        if content_id(plan) != plan_id:
            raise ValueError("清理清单或引用已变化；请重新预览，不执行删除")
        # Record the exact intent before the first unlink. A crash leaves STARTED;
        # repeating this identity returns that fact instead of blindly retrying.
        with engine.begin() as c:
            c.execute(
                text("""INSERT INTO data_cleanup_receipts(plan_id,plan,status)
                VALUES(:id,CAST(:plan AS jsonb),'STARTED')"""),
                {"id": plan_id, "plan": json.dumps(plan)},
            )
        removed: list[dict[str, Any]] = []
        error = None
        try:
            for item in plan["objects"]:
                stores[item["store"]].remove_verified(item["content_hash"], item["byte_count"])
                removed.append(item)
        except Exception as failure:
            error = str(failure)
        status = "FAILED" if error else "SUCCEEDED"
        result = {
            "removed": removed,
            "error": error,
            "bytes": sum(item["byte_count"] for item in removed),
        }
        with engine.begin() as c:
            c.execute(
                text("""UPDATE data_cleanup_receipts SET status=:status,
                result=CAST(:result AS jsonb) WHERE plan_id=:id"""),
                {"status": status, "result": json.dumps(result), "id": plan_id},
            )
        return {"plan_id": plan_id, "status": status, "result": result}
