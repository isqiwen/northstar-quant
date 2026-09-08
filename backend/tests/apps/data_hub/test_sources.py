"""Data Hub source retention, processing failures and download permissions."""

from __future__ import annotations

import hashlib
import tomllib
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine

from northstar_quant.apps.data_hub import create_app as data_app
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from tests.apps.browser import ProtocolClient as TestClient
from tests.apps.browser import _browser_session, _upload_request


def test_archived_bytes_failures_reprocessing_and_download_permissions(
    postgres_engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    del clean_database
    example = Path(__file__).resolve().parents[4] / "examples" / "intraday.toml"
    specification = dict(tomllib.loads(example.read_text())["source"])
    content = (example.parent / specification.pop("file")).read_bytes()
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "archive"))
    application = data_app(postgres_engine, library)
    with TestClient(application, base_url="http://127.0.0.1") as client:
        _browser_session(client)
        assert client.get("/api/sources").json() == []
        assert client.get("/api/attempts").json() == []

        # Even a decode failure retains exactly the received bytes, including BOM.
        broken_bytes = b"\xef\xbb\xbfevent_time,close\r\n\xff\x00\r\n"
        broken = client.post("/api/import", json=_upload_request(broken_bytes, specification))
        assert broken.status_code == 200, broken.text
        failed = broken.json()
        assert failed["status"] == "FAILED"
        assert failed["snapshot_id"] is None
        assert failed["error"]
        source_id = failed["source_id"]
        source = client.get(f"/api/sources/{source_id}").json()
        assert source["content_hash"] == hashlib.sha256(broken_bytes).hexdigest()
        assert source["byte_count"] == len(broken_bytes)
        assert client.get(f"/api/sources/{source_id}/download").content == broken_bytes
        assert client.get(f"/attempts/{failed['attempt_id']}").status_code == 404
        assert client.get("/api/datasets").json() == []
        with closing(TestClient(application, base_url="http://127.0.0.1")) as unauthenticated:
            assert unauthenticated.get(f"/api/sources/{source_id}/download").status_code == 403

        forbidden_payload = _upload_request(b"\xffnot-permitted-to-download", specification)
        forbidden_payload["allow_download"] = False
        private = client.post("/api/import", json=forbidden_payload)
        assert private.status_code == 200, private.text
        private_source = private.json()["source_id"]
        assert client.get(f"/api/sources/{private_source}/download").status_code == 403
        forbidden_payload = _upload_request(b"not-permitted-to-retain", specification)
        forbidden_payload["allow_retention"] = False
        source_count = len(client.get("/api/sources").json())
        refused = client.post("/api/import", json=forbidden_payload)
        assert refused.status_code == 422, refused.text
        assert refused.json()["rejection_id"]
        assert len(client.get("/api/sources").json()) == source_count
        assert refused.json()["rejection_id"] in {
            r["rejection_id"] for r in client.get("/api/rejections").json()
        }

        # Valid bytes with incorrect processing parameters are repairable without re-upload.
        invalid_spec = {**specification, "price_tick": "0"}
        received = client.post("/api/import", json=_upload_request(content, invalid_spec))
        assert received.status_code == 200, received.text
        rejected = received.json()
        assert rejected["status"] == "FAILED"
        assert rejected["snapshot_id"] is None
        source_id = rejected["source_id"]
        assert client.get("/api/datasets").json() == []
        corrected_request = {"spec": specification, "request_id": str(uuid4())}
        endpoint = f"/api/sources/{source_id}/reprocess"
        assert (
            client.post(endpoint, json={**corrected_request, "path": "/etc/passwd"}).status_code
            == 422
        )
        repaired = client.post(endpoint, json=corrected_request)
        assert repaired.status_code == 200, repaired.text
        published = repaired.json()
        assert published["status"] == "PUBLISHED"
        assert published["source_id"] == source_id
        assert published["attempt_id"] != rejected["attempt_id"]
        assert (
            client.post(endpoint, json=corrected_request).json()["attempt_id"]
            == published["attempt_id"]
        )
        assert client.get(f"/api/attempts/{rejected['attempt_id']}").json()["status"] == "FAILED"
        assert len(client.get("/api/datasets").json()) == 1
        assert client.get(f"/api/sources/{source_id}/download").content == content
        source = client.get(f"/api/sources/{source_id}").json()
        assert len(source["attempts"]) == 2
        assert source["content_hash"] == hashlib.sha256(content).hexdigest()
        lineage = client.get(f"/api/datasets/{published['snapshot_id']}/lineage").json()
        assert source_id in {item["source_id"] for item in lineage["sources"]}
        assert client.get(f"/attempts/{published['attempt_id']}").status_code == 404
        assert client.get(f"/datasets/{published['snapshot_id']}").status_code == 404
