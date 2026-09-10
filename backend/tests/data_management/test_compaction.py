"""Physical merges preserve fixed values/provenance, permissions and restore references."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from northstar_quant.data_management import compaction
from northstar_quant.data_management.exploration import catalog, rows
from northstar_quant.data_management.tushare import jobs, planning
from tests.data_management import test_exploration

published = test_exploration.published


def prepare(library, response):
    engine = library._engine
    with engine.begin() as c:
        planning.enqueue(
            c,
            "1min",
            "RB2610.SHF",
            {
                "ts_code": "RB2610.SHF",
                "start_date": "2026-09-01 00:00:00",
                "end_date": "2026-09-01 23:59:59",
            },
            "2026-09-01",
            "2026-09-01",
        )
        c.execute(text("UPDATE data_sync_settings SET next_request_at=now()"))
    response["data"]["items"] = response["data"]["items"][:3]
    assert jobs.process_next(library)["status"] == "VALIDATED"
    args = dict(dataset="1min", scope="RB2610.SHF", start="2026-09-01", end="2026-09-03")
    ids = [UUID(v["receipt_id"]) for v in catalog.versions(engine, **args)["rows"]]
    return {**args, "receipt_ids": ids}


def test_fixed_compaction_roundtrip_and_corruption(published):
    library, response = published
    engine = library._engine
    args = prepare(library, response)
    original = rows.read(engine, **args)
    identity = uuid4()
    plan = compaction.submit(engine, identity, **args)
    assert plan["status"] == "PENDING"
    assert compaction.submit(engine, identity, **args) == plan
    with pytest.raises(ValueError, match="不同固定输入"):
        compaction.submit(engine, identity, **{**args, "end": "2026-09-04"})
    finished = compaction.process_next(engine, library._files)
    assert finished["status"] == "SUCCEEDED", finished
    pages = [compaction.read(engine, str(identity), offset=i, limit=2) for i in (0, 2, 4)]
    assert [r for p in pages for r in p["rows"]] == original["rows"]
    assert all(p["view_id"] == original["view_id"] for p in pages)
    assert not pages[0]["export_allowed"]
    assert rows.read(engine, **args)["rows"] == original["rows"]
    assert compaction.process_next(engine, library._files) is None
    with engine.connect() as c:
        references = compaction.references(c)
        assert references == compaction.references(c)
    assert len(references) == 2
    for reference in references:
        assert library._files.read(reference["content_hash"], reference["byte_count"])
    with pytest.raises(Exception, match="immutable"):
        with engine.begin() as c:
            c.execute(text("DELETE FROM data_compactions"))
    from northstar_quant.data_management.tushare.publication import storage

    files = storage()
    ref = finished["result"]["files"][0]
    # Alter only the disposable test publication; hash verification must reject it.
    path = files.root / "objects" / ref["content_hash"][:2] / ref["content_hash"]
    assert path.exists()
    path.write_bytes(b"broken")
    with pytest.raises(ValueError):
        compaction.read(engine, str(identity))


def test_interrupted_and_conflicting_merges_keep_originals(published):
    library, response = published
    engine = library._engine
    args = prepare(library, response)
    identity = uuid4()
    compaction.submit(engine, identity, **args)
    with engine.begin() as c:
        c.execute(text("UPDATE data_compactions SET status='RUNNING'"))
    assert compaction.process_next(engine, library._files) is None
    assert compaction.get(engine, str(identity))["status"] == "FAILED"
    assert rows.read(engine, **args)["total"] == 6
    response["data"]["items"][0][2] = "3100.20"
    with engine.begin() as c:
        c.execute(text("UPDATE data_sync_jobs SET status='PENDING' WHERE start_at=end_at"))
        c.execute(text("UPDATE data_sync_settings SET next_request_at=now()"))
    assert jobs.process_next(library)["status"] == "VALIDATED"
    args["receipt_ids"] = [
        UUID(v["receipt_id"])
        for v in catalog.versions(engine, **{k: v for k, v in args.items() if k != "receipt_ids"})[
            "rows"
        ]
    ]
    another = uuid4()
    compaction.submit(engine, another, **args)
    failed = compaction.process_next(engine, library._files)
    assert failed["status"] == "FAILED"
    assert "冲突" in failed["error"]


def test_protocol_and_restored_physical_publication(published):
    from northstar_quant.apps.data_hub.application import create_app
    from northstar_quant.data_management.tushare import publication, retention
    from tests.apps.browser import ProtocolClient, login_response

    library, response = published
    args = prepare(library, response)
    identity = str(uuid4())
    document = {**args, "request_id": identity, "receipt_ids": list(map(str, args["receipt_ids"]))}
    with ProtocolClient(
        create_app(library._engine, library), base_url="http://127.0.0.1"
    ) as client:
        csrf = login_response(client).json()["csrf"]
        client.headers.update({"x-northstar-csrf": csrf, "origin": "http://127.0.0.1"})
        submitted = client.post("/api/explorer/compactions", json=document)
        assert submitted.status_code == 202, submitted.text
        assert submitted.json()["result"] is None
        assert compaction.process_next(library._engine, library._files)["status"] == "SUCCEEDED"
        base = f"/api/explorer/compactions/{identity}"
        before = client.post(base + "/query", json={"offset": 0, "limit": 2})
        assert before.status_code == 200, before.text
        assert client.post(base + "/export", json={"offset": 0, "limit": 2}).status_code == 403
        assert client.get(base).json()["status"] == "SUCCEEDED"
        with library._engine.connect() as c:
            references = compaction.references(c)
        for item in references:
            publication.storage()._path(item["content_hash"]).unlink()
        assert client.post(base + "/query", json={"offset": 0, "limit": 2}).status_code == 422
        retention.restore_publications(library._engine, library._files)
        assert client.post(base + "/query", json={"offset": 0, "limit": 2}).json() == before.json()
