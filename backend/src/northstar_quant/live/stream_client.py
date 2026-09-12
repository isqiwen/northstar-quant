"""Live Web/CLI access to Live-owned reception without local worker ownership."""

from __future__ import annotations

import builtins
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from .client import LiveClient


class StreamsClient:
    def __init__(self, live: LiveClient) -> None:
        self._live = live

    def list(self) -> list[dict[str, Any]]:
        return self._live.read_list("/streams")

    def get(self, identifier: UUID) -> dict[str, Any]:
        return self._live.read(f"/streams/{identifier}")

    def decision(self, identifier: UUID, sequence: int) -> dict[str, Any]:
        return self._live.read(f"/streams/{identifier}/decisions/{sequence}")

    def events(self, identifier: UUID, *, after: int = 0) -> builtins.list[dict[str, Any]]:
        return self._live.read_list(f"/streams/{identifier}/events?after={after}")

    def start(
        self,
        query_batch_id: UUID,
        configuration_id: str,
        *,
        request_id: UUID,
        duration_seconds: int,
        allow_retention: bool,
        use_basis: str,
        schedule: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._live.mutate(
            "/streams",
            {
                "query_batch_id": str(query_batch_id),
                "configuration_id": configuration_id,
                "duration_seconds": duration_seconds,
                "allow_retention": allow_retention,
                "use_basis": use_basis,
                **({"schedule": schedule} if schedule is not None else {}),
            },
            request_id,
        )

    def control(self, identifier: UUID, action: str, *, request_id: UUID) -> dict[str, Any]:
        return self._live.mutate(f"/streams/{identifier}/control", {"action": action}, request_id)

    def catchup_account(
        self,
        identifier: UUID,
        baseline_id: UUID,
        through_sequence: int,
        *,
        request_id: UUID | None = None,
    ) -> dict[str, Any]:
        return self._live.mutate(
            f"/streams/{identifier}/account-catchup",
            {
                "baseline_id": str(baseline_id),
                "through_sequence": through_sequence,
            },
            request_id or uuid4(),
        )

    def archive(
        self,
        identifier: UUID,
        *,
        through_sequence: int,
        session_open: str,
        session_close: str,
        request_id: UUID,
        allow_download: bool = False,
    ) -> dict[str, Any]:
        return self._live.mutate(
            f"/streams/{identifier}/archives",
            {
                "through_sequence": through_sequence,
                "session_open": session_open,
                "session_close": session_close,
                "allow_download": allow_download,
            },
            request_id,
        )
