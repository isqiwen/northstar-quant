"""Bounded health delivery, used only by the independent read-only monitor."""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

import httpx2 as httpx


class DeliveryUnavailable(RuntimeError):
    """Retry the same notification identity; never expose receiver credentials."""


class Webhook:
    """A receiver acknowledges a JSON event with 2xx and deduplicates event_id."""

    def __init__(self, url: str, token: str = "") -> None:
        try:
            parsed = urlsplit(url)
            parsed.port
        except ValueError:
            raise ValueError("Live alert receiver URL is invalid") from None
        try:
            loopback = (
                parsed.hostname is not None and ipaddress.ip_address(parsed.hostname).is_loopback
            )
        except ValueError:
            loopback = False
        if (
            len(url) > 4096
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or not (parsed.scheme == "https" or (parsed.scheme == "http" and loopback))
        ):
            raise ValueError("Live alert receiver requires HTTPS (HTTP only on loopback)")
        if len(token) > 4096 or any(not 33 <= ord(character) <= 126 for character in token):
            raise ValueError("Live alert receiver token must be a bounded ASCII bearer token")
        self.url = url
        self.client = httpx.Client(
            timeout=httpx.Timeout(2.0),
            follow_redirects=False,
            trust_env=False,
            headers={"Authorization": f"Bearer {token}"} if token else {},
        )

    def __call__(self, event: dict[str, Any]) -> None:
        try:
            # Do not download arbitrary receiver response bodies or follow redirects.
            with self.client.stream(
                "POST", self.url, json=event, headers={"Idempotency-Key": event["event_id"]}
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise DeliveryUnavailable("Live health receiver did not acknowledge event")
        except httpx.HTTPError:
            raise DeliveryUnavailable("Live health receiver is unavailable") from None

    def close(self) -> None:
        self.client.close()
