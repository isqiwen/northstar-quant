from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from starlette.responses import Response

_MAX_BODY = 8 * 1024 * 1024


def _uuid_field(payload: dict[str, object], name: str) -> UUID:
    value = payload[name]
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须是规范的 UUID。")
    identifier = UUID(value)
    if str(identifier) != value:
        raise ValueError(f"{name} 必须是规范的 UUID。")
    return identifier


def _string_field(payload: dict[str, object], name: str) -> str:
    value = payload[name]
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须是字符串。")
    return value


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("需要 JSON 对象。")
    return cast(dict[str, object], value)


class ApiModel(BaseModel):
    """HTTP values: reject unknown fields and implicit coercion at the boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ProtobufRoute(APIRoute):
    """Decode authorized, bounded Protobuf before business input validation."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def checked(request: Request) -> Response:
            if request.method == "POST" and self.body_field is not None:
                if self.path != "/api/login":
                    request.app.state.workspace_access.protect(request)
                from northstar_quant.web.protobuf import MEDIA_TYPE, decode

                if request.headers.get("content-type", "").split(";", 1)[0] != MEDIA_TYPE:
                    raise HTTPException(415, "请使用 application/protobuf。")
                content = bytearray()
                async for chunk in request.stream():
                    content.extend(chunk)
                    if len(content) > _MAX_BODY:
                        raise HTTPException(413, "请求不得超过 8 MiB。")
                method = request.app.state.protobuf_methods[(request.method, self.path)]
                payload = decode(method.input_type, bytes(content))
                internal_body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()

                async def receive() -> dict[str, Any]:
                    return {"type": "http.request", "body": internal_body, "more_body": False}

                scope = dict(request.scope)
                scope["headers"] = [
                    (k, v)
                    for k, v in scope["headers"]
                    if k.lower() not in {b"content-type", b"content-length"}
                ] + [(b"content-type", b"application/json")]
                request = Request(scope, receive)
            return await handler(request)

        return checked


class EvidenceRecord(ApiModel):
    """Named envelope with JSON evidence supplied by its business owner.

    Extensions retain raw source/SDK or versioned evidence; they are JSON values,
    never arbitrary Python objects. Fixed request models remain extra=forbid.
    """

    model_config = ConfigDict(extra="allow", strict=True)
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)


UUIDText = Annotated[
    str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
]
