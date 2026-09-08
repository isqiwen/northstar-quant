"""Bind management commands to the runtime actually observed by this page."""

from typing import Annotated
from uuid import UUID

from fastapi import Header


def _runtime_header(
    value: Annotated[
        str,
        Header(
            alias="X-Live-Runtime-Id",
            description="Observed runtime identity; commands never rebind automatically.",
        ),
    ],
) -> UUID:
    try:
        identifier = UUID(value)
    except ValueError as error:
        raise ValueError("Live 操作必须绑定本页已观察的运行身份；请重新打开页面。") from error
    if str(identifier) != value:
        raise ValueError("Live 运行身份必须是规范 UUID。")
    return identifier
