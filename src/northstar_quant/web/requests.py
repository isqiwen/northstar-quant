from __future__ import annotations

import json
from typing import cast
from uuid import UUID

from fastapi import HTTPException, Request

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


async def _read_object(request: Request) -> dict[str, object]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise HTTPException(status_code=415, detail="请使用 application/json。")
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > _MAX_BODY:
            raise HTTPException(
                status_code=413, detail="编码后请求不得超过 8 MiB；原文件最多 5 MiB。"
            )
    try:
        payload: object = json.loads(content, object_pairs_hook=_unique_fields)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("请求必须是有效的 UTF-8 JSON。") from error
    return _object(payload)


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("请求不能包含重复字段。")
        result[name] = value
    return result


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("需要 JSON 对象。")
    return cast(dict[str, object], value)
