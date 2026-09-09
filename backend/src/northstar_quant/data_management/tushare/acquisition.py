"""Bounded authenticated historical HTTP download; never persist or echo credentials."""

import json
from time import monotonic

import httpx2 as httpx

from ..research import ImportSpec
from .request import ENDPOINT, FIELDS, parameters


def fetch(spec: ImportSpec, token: str, *, transport: httpx.BaseTransport | None = None) -> bytes:
    if (
        not isinstance(token, str)
        or not token.strip()
        or not 16 <= len(token) <= 512
        or not token.isascii()
    ):
        raise ValueError("NORTHSTAR_TUSHARE_TOKEN must be configured on the Data worker")
    try:
        with httpx.Client(
            timeout=10,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            headers={"Accept-Encoding": "identity"},
        ) as client:
            deadline = monotonic() + 30
            with client.stream(
                "POST",
                ENDPOINT,
                json={
                    "api_name": "ft_mins",
                    "token": token,
                    "params": parameters(spec),
                    "fields": FIELDS,
                },
            ) as response:
                if response.status_code != 200:
                    raise ValueError(f"Tushare HTTP {response.status_code}; no automatic retry")
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ValueError("Tushare returned unsupported compressed content")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 5242880 or monotonic() > deadline:
                        raise ValueError("Tushare response exceeded byte or elapsed-time limit")
                    chunks.append(chunk)
                content = b"".join(chunks)
    except httpx.HTTPError:
        raise ValueError(
            "Tushare download failed; inspect connectivity, not request bodies"
        ) from None
    # Reject reflected credentials rather than saving them as source/error evidence.
    if token.encode() in content or json.dumps(token)[1:-1].encode() in content:
        raise ValueError("Tushare response cannot be retained because it reflects credentials")
    try:
        document = json.loads(content)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("Tushare returned invalid JSON") from None
    if not isinstance(document, dict) or type(document.get("code")) is not int:
        raise ValueError("Tushare response is missing a valid result code")
    if document["code"] != 0:
        # Upstream msg may contain credentials; persist only a validated numerical code.
        raise ValueError(f"Tushare API code {document['code']}; check permission or quota")
    return content
