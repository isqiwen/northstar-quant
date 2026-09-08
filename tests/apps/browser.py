"""Concrete HTTP session and permitted upload helpers shared by application tests."""

from __future__ import annotations

import base64
from uuid import uuid4

from fastapi.testclient import TestClient


def _browser_session(client: TestClient) -> None:
    session = client.get("/api/browser-session")
    assert session.status_code == 200
    client.headers["X-Northstar-CSRF"] = session.json()["csrf"]


def _upload_request(content: bytes, specification: dict[str, object]) -> dict[str, object]:
    return {
        "content_base64": base64.b64encode(content).decode("ascii"),
        "filename": "minutes.csv",
        "source_name": specification["source_name"],
        "spec": specification,
        "request_id": str(uuid4()),
        "input_kind": "RECEIVED_CSV",
        "upstream_source_id": None,
        "transformation_note": None,
        "use_basis": "Generated HTTP workflow data, permitted for local research and retention.",
        "allow_retention": True,
        "allow_download": True,
    }
