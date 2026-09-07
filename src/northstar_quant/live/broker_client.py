"""Concrete HTTP access to saved account evidence and explicitly requested queries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from .client import LiveClient


class BrokerClient:
    def __init__(self, live: LiveClient) -> None:
        self._live = live

    def status(self) -> dict[str, Any]:
        return self._live.read("/broker/status")

    def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._live.read_list(f"/broker/queries?limit={limit}")

    def get(self, batch_id: UUID) -> dict[str, Any]:
        return self._live.read(f"/broker/queries/{batch_id}")

    def query(self, profile_name: str, instrument: str, *, request_id: UUID) -> dict[str, Any]:
        return self._live.mutate(
            "/broker/queries",
            {
                "profile_name": profile_name,
                "instrument": instrument,
            },
            request_id,
        )

    def baseline_context(self, query_batch_id: UUID) -> dict[str, Any]:
        return self._live.read(f"/broker/queries/{query_batch_id}/baseline")

    def ledger_context(self, query_batch_id: UUID) -> dict[str, Any]:
        return self._live.read(f"/broker/queries/{query_batch_id}/ledger")

    def funds_context(self, query_batch_id: UUID) -> dict[str, Any]:
        return self._live.read(f"/broker/queries/{query_batch_id}/funds")

    def establish_baseline(self, source_batch_id: UUID, *, request_id: UUID) -> dict[str, Any]:
        return self._live.mutate(
            "/broker/baselines",
            {
                "source_batch_id": str(source_batch_id),
            },
            request_id,
        )

    def compare_baseline(
        self, baseline_id: UUID, query_batch_id: UUID, *, request_id: UUID
    ) -> dict[str, Any]:
        return self._live.mutate(
            f"/broker/baselines/{baseline_id}/comparisons",
            {
                "query_batch_id": str(query_batch_id),
            },
            request_id,
        )

    def get_baseline_check(self, check_id: UUID) -> dict[str, Any]:
        return self._live.read(f"/broker/baseline-checks/{check_id}")

    def observe_funds(
        self, baseline_id: UUID, source_batch_id: UUID, *, request_id: UUID
    ) -> dict[str, Any]:
        return self._live.mutate(
            f"/broker/baselines/{baseline_id}/funds",
            {
                "source_batch_id": str(source_batch_id),
            },
            request_id,
        )

    def get_funds_entry(self, entry_id: UUID) -> dict[str, Any]:
        return self._live.read(f"/broker/funds-entries/{entry_id}")

    def ingest_stream_positions(
        self, baseline_id: UUID, stream_id: UUID, through_sequence: int, *, request_id: UUID
    ) -> dict[str, Any]:
        return self._live.mutate(
            f"/broker/baselines/{baseline_id}/stream-positions",
            {
                "stream_id": str(stream_id),
                "through_sequence": through_sequence,
            },
            request_id,
        )

    def ingest_positions(
        self, baseline_id: UUID, source_batch_id: UUID, *, request_id: UUID
    ) -> dict[str, Any]:
        return self._live.mutate(
            f"/broker/baselines/{baseline_id}/positions",
            {
                "source_batch_id": str(source_batch_id),
            },
            request_id,
        )

    def compare_positions(
        self, entry_id: UUID, query_batch_id: UUID, *, request_id: UUID
    ) -> dict[str, Any]:
        return self._live.mutate(
            f"/broker/position-entries/{entry_id}/comparisons",
            {
                "query_batch_id": str(query_batch_id),
            },
            request_id,
        )

    def get_position_entry(self, entry_id: UUID) -> dict[str, Any]:
        return self._live.read(f"/broker/position-entries/{entry_id}")

    def get_position_check(self, check_id: UUID) -> dict[str, Any]:
        return self._live.read(f"/broker/position-checks/{check_id}")

    def check_orders(self, position_check_id: UUID, *, request_id: UUID) -> dict[str, Any]:
        return self._live.mutate(
            f"/broker/position-checks/{position_check_id}/orders", {}, request_id
        )

    def get_order_check(self, check_id: UUID) -> dict[str, Any]:
        return self._live.read(f"/broker/order-checks/{check_id}")
