"""A concrete synchronous HTTP client; lost acknowledgements never cause a resend."""

from __future__ import annotations

import ipaddress
import os
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID

import httpx2 as httpx

from .auth import LiveAuth

# Bump for changes to the current request/response or command semantics.
PROTOCOL_VERSION = "4"


class RuntimeUnavailable(RuntimeError):
    """The current Live observation is unavailable; old facts are not a live heartbeat."""


class CommandUnknown(RuntimeError):
    """Keep this fixed identity and inquire; do not repeat an indeterminate effect."""

    def __init__(self, request_id: UUID, runtime_id: UUID | None) -> None:
        self.request_id = request_id
        self.runtime_id = runtime_id
        super().__init__(f"Live command {request_id} outcome unknown; query this identity")


class LiveClient:
    def __init__(
        self,
        base_url: str,
        auth: LiveAuth,
        *,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        expected_runtime_id: UUID | None = None,
        expected_instance_id: str | None = None,
        operator: str = "maintenance",
    ) -> None:
        if operator not in {"owner", "maintenance"}:
            raise ValueError("Unknown Live operator")
        self._operator = operator
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "NORTHSTAR_LIVE_URL must name a fixed HTTP(S) origin without credentials"
            )
        if parsed.scheme == "http":
            try:
                private = ipaddress.ip_address(parsed.hostname).is_private
            except ValueError:
                private = "." not in parsed.hostname and ":" not in parsed.hostname
            if not private:
                raise ValueError("Unencrypted Live HTTP is limited to a private deployment network")
        self._auth = auth
        self._base_url = base_url
        self._expected_runtime_id = expected_runtime_id
        self._expected_instance_id = expected_instance_id
        self._owns_http = True
        self._http = client or httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(10.0, connect=2.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self.last_observation: dict[str, Any] | None = None
        # These concrete adapters contain serialization only, never account rules.
        from .broker_client import BrokerClient
        from .budget_client import OpeningBudgetsClient
        from .stream_client import StreamsClient

        self.broker = BrokerClient(self)
        self.streams = StreamsClient(self)
        self.opening_budgets = OpeningBudgetsClient(self)

    @classmethod
    def from_environment(cls) -> LiveClient:
        return cls(
            os.environ.get("NORTHSTAR_LIVE_URL", "http://127.0.0.1:18081"),
            LiveAuth.from_environment(),
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        command_headers: dict[str, str] | None = None,
    ) -> Any:
        control = method != "GET"
        token = self._auth.control_token if control else self._auth.read_token
        if token is None:
            raise ValueError("This Live Web has read permission only")
        headers = {
            "Authorization": "Bearer " + token,
            "X-Northstar-Protocol": PROTOCOL_VERSION,
            "X-Northstar-Operator": self._operator,
        }
        if self._expected_instance_id:
            headers["X-Live-Instance-ID"] = self._expected_instance_id
        headers.update(command_headers or {})
        try:
            response = self._http.request(method, path, headers=headers, json=body)
        except httpx.HTTPError as error:
            raise RuntimeUnavailable(
                "Live is unavailable; last saved observation may be stale"
            ) from error
        if response.status_code == 404:
            raise LookupError("Live record was not found")
        if response.status_code in {400, 403, 405, 409, 413, 422}:
            raise ValueError("Live rejected permission, current protocol, target or command input")
        if response.status_code != 200:
            raise RuntimeUnavailable("Live did not confirm the operation")
        if (
            self._expected_instance_id
            and response.headers.get("x-live-instance-id") != self._expected_instance_id
        ):
            raise RuntimeUnavailable("Live endpoint belongs to a different instance")
        if response.headers.get("x-northstar-protocol") != PROTOCOL_VERSION:
            raise RuntimeUnavailable("Live does not match the current protocol")
        try:
            value = response.json()
            runtime_id = UUID(response.headers["x-live-runtime-id"])
            observed_at = datetime.fromisoformat(response.headers["x-live-observed-at"])
            if observed_at.tzinfo is None:
                raise ValueError("missing observation timezone")
            age = (datetime.now(UTC) - observed_at).total_seconds()
            if not -1 <= age <= 10:
                raise ValueError("stale or future Live observation")
        except (ValueError, KeyError) as error:
            raise RuntimeUnavailable("Live returned an invalid observation") from error
        self.last_observation = {
            "runtime_id": str(runtime_id),
            "observed_at": observed_at.isoformat(),
            "received_at": datetime.now(UTC).isoformat(),
        }
        return value

    def read(self, path: str) -> dict[str, Any]:
        value = self._request("GET", path)
        if not isinstance(value, dict):
            raise RuntimeUnavailable("Live returned an invalid record")
        return cast(dict[str, Any], value)

    def read_list(self, path: str) -> list[dict[str, Any]]:
        value = self._request("GET", path)
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise RuntimeUnavailable("Live returned an invalid record list")
        return cast(list[dict[str, Any]], value)

    def status(self) -> dict[str, Any]:
        return {**self.read("/runtime"), "control_available": self._auth.control_token is not None}

    def diagnostics(self) -> dict[str, Any]:
        return self.read("/diagnostics")

    def for_runtime(self, runtime_id: UUID) -> LiveClient:
        """Fix the owner observed by one page; restart cannot retarget its commands."""
        if not isinstance(runtime_id, UUID):
            raise ValueError("A Live page must bind a runtime UUID")
        bound = LiveClient(
            self._base_url,
            self._auth,
            client=self._http,
            expected_runtime_id=runtime_id,
            expected_instance_id=self._expected_instance_id,
            operator=self._operator,
        )
        bound._owns_http = False
        return bound

    def for_operator(self, operator: str) -> LiveClient:
        """Bind the API's authenticated identity without mutating the shared connection pool."""
        bound = LiveClient(
            self._base_url,
            self._auth,
            client=self._http,
            expected_runtime_id=self._expected_runtime_id,
            expected_instance_id=self._expected_instance_id,
            operator=operator,
        )
        bound._owns_http = False
        return bound

    def command(self, request_id: UUID) -> dict[str, Any]:
        return self.read(f"/commands/{request_id}")

    @staticmethod
    def _result(receipt: dict[str, Any], request_id: UUID) -> dict[str, Any]:
        if receipt["status"] == "REJECTED":
            raise ValueError(f"Live command {request_id} rejected: {receipt['error_code']}")
        if receipt["status"] != "COMPLETED" or not isinstance(receipt["result"], dict):
            raise CommandUnknown(request_id, UUID(receipt["runtime_id"]))
        return cast(dict[str, Any], receipt["result"])

    def mutate(self, path: str, body: dict[str, Any], request_id: UUID) -> dict[str, Any]:
        if self._auth.control_token is None:
            raise ValueError("This Live Web has read permission only")
        if not isinstance(request_id, UUID):
            raise ValueError("Live commands require a fixed UUID")
        # Includes a new Live Web process after response loss: inspect the durable
        # identity before allocating any new deadline or observing a new owner.
        try:
            receipt = self.command(request_id)
        except LookupError:
            receipt = None
        if receipt is not None:
            if (
                receipt["path"] != path
                or receipt["input"] != body
                or receipt["operator"] != self._operator
            ):
                raise ValueError("Live command identity is bound to different input")
            return self._result(receipt, request_id)
        runtime = self.status()
        owner = UUID(runtime["runtime_id"])
        if self._expected_runtime_id is not None and owner != self._expected_runtime_id:
            raise RuntimeUnavailable("Live runtime changed; reopen the page before a new command")
        headers = {
            "X-Live-Command-ID": str(request_id),
            "X-Live-Runtime-ID": str(owner),
            "X-Live-Expires-At": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        }
        try:
            receipt = self._request("POST", path, body=body, command_headers=headers)
        except RuntimeUnavailable as error:
            raise CommandUnknown(request_id, owner) from error
        if not isinstance(receipt, dict):
            raise CommandUnknown(request_id, owner)
        return self._result(receipt, request_id)

    def close(self) -> None:
        """Close this caller's HTTP connections only; never stop the Live owner."""
        if self._owns_http:
            self._http.close()
