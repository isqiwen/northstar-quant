"""Bounded HTTPS requests; provider messages and credentials never enter errors."""

import json
import time
from decimal import Decimal
from typing import Any, cast

import httpx2

from .credentials import validate

ENDPOINT = "https://api.tushare.pro"


class DownloadError(ValueError):
    def __init__(self, reason: str, *, retry: bool = False) -> None:
        super().__init__(reason)
        self.retry = retry


class ResponseLimit(DownloadError):
    pass


def fetch(
    api: str,
    parameters: dict[str, object],
    token: str,
    *,
    transport: httpx2.BaseTransport | None = None,
) -> bytes:
    validate(token)
    started = time.monotonic()
    try:
        with httpx2.Client(
            transport=transport,
            timeout=10,
            follow_redirects=False,
            trust_env=False,
            headers={"Accept-Encoding": "identity"},
        ) as client:
            with client.stream(
                "POST",
                ENDPOINT,
                json={"api_name": api, "params": parameters, "token": token, "fields": ""},
            ) as response:
                if response.status_code != 200:
                    raise DownloadError(
                        f"Tushare HTTP {response.status_code}",
                        retry=response.status_code == 429 or response.status_code >= 500,
                    )
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise DownloadError("Tushare 返回了不支持的压缩响应")
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 5 * 1024**2:
                        raise ResponseLimit("响应超过 5 MiB；需要更小区间")
                    if time.monotonic() - started > 30:
                        raise DownloadError("Tushare 请求超时", retry=True)
    except httpx2.HTTPError:
        raise DownloadError("Tushare 网络请求失败", retry=True) from None
    if token.encode() in content or json.dumps(token)[1:-1].encode() in content:
        raise DownloadError("Tushare 响应包含凭据，已拒绝保存")
    decode(bytes(content))
    return bytes(content)


def decode(content: bytes) -> dict[str, Any]:
    try:
        document = json.loads(
            content,
            parse_float=Decimal,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(document, dict) or type(document.get("code")) is not int:
            raise ValueError()
        if document["code"]:
            # Classify only locally; never propagate the provider's raw message.
            msg = str(document.get("msg", ""))
            rate = any(word in msg for word in ("频次", "每分钟", "每小时", "每秒"))
            permission = any(
                word in msg
                for word in ("没有访问该接口的权限", "没有权限", "无权限", "权限不足", "权限已过期")
            )
            raise DownloadError(
                "Tushare 限频，等待退避重试"
                if rate
                else f"Tushare 权限不足（代码 {document['code']}），请核对该接口授权"
                if permission
                else f"Tushare 请求异常（代码 {document['code']}），原因未确认；仅暂停此分片",
                retry=rate,
            )
        data = document["data"]
        fields, items = data["fields"], data["items"]
        if (
            not isinstance(fields, list)
            or (not fields and items != [])
            or len(fields) != len(set(fields))
            or not all(isinstance(f, str) for f in fields)
            or not isinstance(items, list)
            or any(not isinstance(row, list) or len(row) != len(fields) for row in items)
        ):
            raise ValueError()
        return cast(dict[str, Any], data)
    except DownloadError:
        raise
    except (ValueError, KeyError, TypeError):
        raise DownloadError("Tushare 响应结构异常，暂停此类数据") from None
