"""Allowlisted network client for the first official-SHFE daily adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import httpx2 as httpx

from northstar_quant.data import __version__
from northstar_quant.data.ingestion.provider_commands import ProviderFetchError
from northstar_quant.data.ingestion.providers.shfe import SHFE_DAILY_ENDPOINT


@dataclass(frozen=True)
class ProviderHttpResponse:
    """Non-secret response metadata and transient body bytes."""

    content: bytes
    content_type: str | None
    etag: str | None
    last_modified: str | None
    provider_request_id: str | None


class ShfeDailyHttpClient:
    """Fetch one fixed official endpoint without redirects or arbitrary URLs."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_bytes: int,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_bytes = max_bytes
        self._transport = transport

    def fetch(self, trading_day: date) -> ProviderHttpResponse:
        url = SHFE_DAILY_ENDPOINT.format(trading_day=trading_day.strftime("%Y%m%d"))
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self._timeout_seconds),
                follow_redirects=False,
                transport=self._transport,
                headers={
                    "Accept": "application/json",
                    "User-Agent": f"quant-data-hub/{__version__}",
                },
            ) as client:
                with client.stream("GET", url) as response:
                    if response.status_code != httpx.codes.OK:
                        persisted_status = (
                            response.status_code if 100 <= response.status_code <= 599 else None
                        )
                        raise ProviderFetchError(
                            "PROVIDER_HTTP_STATUS",
                            f"the SHFE endpoint returned HTTP {response.status_code}",
                            retryable=response.status_code in {408, 429, 500, 502, 503, 504},
                            # The authoritative evidence record accepts HTTP's
                            # defined three-digit range only.  Preserve a
                            # malformed/mock status in the bounded error text,
                            # but never let it strand the outer reservation by
                            # violating the response-status check constraint.
                            http_status=persisted_status,
                        )
                    chunks: list[bytes] = []
                    byte_count = 0
                    for chunk in response.iter_bytes():
                        byte_count += len(chunk)
                        if byte_count > self._max_bytes:
                            raise ProviderFetchError(
                                "PROVIDER_RESPONSE_TOO_LARGE",
                                "the SHFE response exceeds the configured byte limit",
                                retryable=False,
                            )
                        chunks.append(chunk)
                    return ProviderHttpResponse(
                        content=b"".join(chunks),
                        content_type=response.headers.get("content-type"),
                        etag=response.headers.get("etag"),
                        last_modified=response.headers.get("last-modified"),
                        provider_request_id=response.headers.get("x-request-id"),
                    )
        except ProviderFetchError:
            raise
        except httpx.TimeoutException as error:
            raise ProviderFetchError(
                "PROVIDER_TIMEOUT",
                "the SHFE endpoint did not respond before the deadline",
                retryable=True,
            ) from error
        except httpx.HTTPError as error:
            raise ProviderFetchError(
                "PROVIDER_NETWORK_ERROR", "the SHFE endpoint could not be reached", retryable=True
            ) from error
