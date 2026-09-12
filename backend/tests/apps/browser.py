"""Concrete HTTP session and permitted upload helpers shared by application tests."""

from __future__ import annotations

import base64
from uuid import uuid4

from fastapi.testclient import TestClient

WORKSPACE_PASSWORD = "synthetic-workspace-test-password"


def login_response(client: TestClient):
    from northstar_quant.web.auth_pb2 import LoginRequest

    endpoint = (
        "/api/setup"
        if client.get("/api/browser-session").json().get("setup_required")
        else "/api/login"
    )
    return client.post(
        endpoint,
        content=LoginRequest(username="owner", password=WORKSPACE_PASSWORD).SerializeToString(),
        headers={"Content-Type": "application/protobuf"},
    )


def _browser_session(client: TestClient) -> None:
    session = login_response(client)
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


class ProtocolClient(TestClient):
    """Exercise actual binary HTTP while retaining readable business assertions."""

    def request(self, method, url, **kwargs):
        import json
        import re

        from northstar_quant.web import common_pb2
        from northstar_quant.web.protobuf import decode

        bindings = getattr(self.app.state, "protobuf_methods", {})
        binding = next(
            (
                v
                for (verb, path), v in bindings.items()
                if verb == method.upper()
                and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", path), str(url).split("?")[0])
            ),
            None,
        )
        if binding is not None and kwargs.get("json") is not None:
            body = kwargs.pop("json")
            known = {k: v for k, v in body.items() if k in binding.input_type.fields_by_name}
            from northstar_quant.web.protobuf import pack

            try:
                content = pack(binding.input_type, known).SerializeToString()
            except ValueError:
                # Incomplete requests deliberately exercise server-side required fields.
                from google.protobuf import json_format, message_factory

                message = message_factory.GetMessageClass(binding.input_type)()
                json_format.ParseDict(known, message)
                content = message.SerializeToString()
            # Represent deliberately unknown test fields as an unknown protobuf tag.
            if set(body) - set(known):
                content += b"\xf8\x7f\x01"
            kwargs["content"] = content
            kwargs["headers"] = {
                **(kwargs.get("headers") or {}),
                "Content-Type": "application/protobuf",
            }
        response = super().request(method, url, **kwargs)
        if response.headers.get("content-type", "").startswith("application/protobuf"):
            descriptor = binding.output_type if response.is_success else common_pb2.Error.DESCRIPTOR
            value = decode(descriptor, response.content)
            response._content = json.dumps(value).encode()
            response.headers["content-type"] = "application/json"
        return response


def _seed_source(library, payload):
    """Synthetic test setup through the data owner, never a public file-upload route."""
    payload = dict(payload)
    content = base64.b64decode(payload.pop("content_base64"))
    return library.submit(content, **payload)
