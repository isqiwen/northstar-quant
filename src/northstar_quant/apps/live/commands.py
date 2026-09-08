"""Bind management commands to the runtime actually observed by this page."""

from uuid import UUID

from fastapi import Request


def _runtime_header(request: Request) -> UUID:
    value = request.headers.get("x-live-runtime-id", "")
    try:
        identifier = UUID(value)
    except ValueError as error:
        raise ValueError("Live 操作必须绑定本页已观察的运行身份；请重新打开页面。") from error
    if str(identifier) != value:
        raise ValueError("Live 运行身份必须是规范 UUID。")
    return identifier
