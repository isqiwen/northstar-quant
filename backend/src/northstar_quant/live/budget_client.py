"""Read and calculate fixed historical evidence through Live, without account writes here."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from .client import LiveClient


class OpeningBudgetsClient:
    def __init__(self, live: LiveClient) -> None:
        self._live = live

    def get(self, budget_id: UUID) -> dict[str, Any]:
        return self._live.read(f"/opening-budgets/{budget_id}")

    def context(self, stream_id: UUID) -> dict[str, Any]:
        return self._live.read(f"/streams/{stream_id}/opening-budgets")

    def create(
        self,
        stream_id: UUID,
        sequence: int,
        query_id: UUID,
        entry_id: UUID,
        *,
        limit_price: Decimal,
        request_id: UUID,
    ) -> dict[str, Any]:
        return self._live.mutate(
            "/opening-budgets",
            {
                "stream_id": str(stream_id),
                "sequence": sequence,
                "query_id": str(query_id),
                "entry_id": str(entry_id),
                "limit_price": str(limit_price),
            },
            request_id,
        )
