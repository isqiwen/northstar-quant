"""Explicit saved-account reads and no-send broker operations owned by Live."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from northstar_quant.broker.settings import credential_status, validate_instrument

from .owner import LiveOwner


class QueryBroker(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_name: Literal["simnow_dev", "simnow_trading"]
    instrument: str = Field(min_length=1, max_length=32)


class SourceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_batch_id: UUID


class CompareObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_batch_id: UUID


class StreamPositions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stream_id: UUID
    through_sequence: StrictInt = Field(ge=1, le=100_000)


class OrderCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")


def routes(owner: LiveOwner) -> APIRouter:
    router = APIRouter()
    broker = owner.broker

    @router.get("/broker/status")
    def status() -> dict[str, Any]:
        return owner.read(broker.status())

    @router.get("/broker/queries")
    def queries(limit: int = 50) -> list[dict[str, Any]]:
        return broker.list(limit=limit)

    @router.get("/broker/queries/{batch_id}")
    def query(batch_id: UUID) -> dict[str, Any]:
        return broker.get(batch_id)

    @router.post("/broker/queries")
    def query_broker(request: Request, body: QueryBroker) -> dict[str, Any]:
        validate_instrument(body.instrument)
        if not credential_status()["configured"]:
            raise ValueError("Live broker credentials are not configured")
        return owner.execute(
            request,
            body.model_dump(mode="json"),
            lambda identifier: broker.query(
                body.profile_name, body.instrument, request_id=identifier
            ),
        )

    @router.get("/broker/queries/{batch_id}/baseline")
    def baseline_context(batch_id: UUID) -> dict[str, Any]:
        return broker.baseline_context(batch_id)

    @router.get("/broker/queries/{batch_id}/ledger")
    def ledger_context(batch_id: UUID) -> dict[str, Any]:
        return broker.ledger_context(batch_id)

    @router.get("/broker/queries/{batch_id}/funds")
    def funds_context(batch_id: UUID) -> dict[str, Any]:
        return broker.funds_context(batch_id)

    @router.post("/broker/baselines")
    def establish_baseline(request: Request, body: SourceObservation) -> dict[str, Any]:
        return owner.execute(
            request,
            body.model_dump(mode="json"),
            lambda identifier: broker.establish_baseline(
                body.source_batch_id, request_id=identifier
            ),
        )

    @router.post("/broker/baselines/{baseline_id}/comparisons")
    def compare_baseline(
        request: Request, baseline_id: UUID, body: CompareObservation
    ) -> dict[str, Any]:
        return owner.execute(
            request,
            body.model_dump(mode="json"),
            lambda identifier: broker.compare_baseline(
                baseline_id, body.query_batch_id, request_id=identifier
            ),
        )

    @router.get("/broker/baseline-checks/{check_id}")
    def baseline_check(check_id: UUID) -> dict[str, Any]:
        return broker.get_baseline_check(check_id)

    @router.post("/broker/baselines/{baseline_id}/positions")
    def ingest_positions(
        request: Request, baseline_id: UUID, body: SourceObservation
    ) -> dict[str, Any]:
        return owner.execute(
            request,
            body.model_dump(mode="json"),
            lambda identifier: broker.ingest_positions(
                baseline_id, body.source_batch_id, request_id=identifier
            ),
        )

    @router.post("/broker/baselines/{baseline_id}/stream-positions")
    def ingest_stream_positions(
        request: Request, baseline_id: UUID, body: StreamPositions
    ) -> dict[str, Any]:
        return owner.execute(
            request,
            body.model_dump(mode="json"),
            lambda identifier: broker.ingest_stream_positions(
                baseline_id, body.stream_id, body.through_sequence, request_id=identifier
            ),
        )

    @router.get("/broker/position-entries/{entry_id}")
    def position_entry(entry_id: UUID) -> dict[str, Any]:
        return broker.get_position_entry(entry_id)

    @router.post("/broker/position-entries/{entry_id}/comparisons")
    def compare_positions(
        request: Request, entry_id: UUID, body: CompareObservation
    ) -> dict[str, Any]:
        return owner.execute(
            request,
            body.model_dump(mode="json"),
            lambda identifier: broker.compare_positions(
                entry_id, body.query_batch_id, request_id=identifier
            ),
        )

    @router.get("/broker/position-checks/{check_id}")
    def position_check(check_id: UUID) -> dict[str, Any]:
        return broker.get_position_check(check_id)

    @router.post("/broker/position-checks/{check_id}/orders")
    def check_orders(request: Request, check_id: UUID, body: OrderCheck) -> dict[str, Any]:
        return owner.execute(
            request,
            body.model_dump(mode="json"),
            lambda identifier: broker.check_orders(check_id, request_id=identifier),
        )

    @router.get("/broker/order-checks/{check_id}")
    def order_check(check_id: UUID) -> dict[str, Any]:
        return broker.get_order_check(check_id)

    @router.post("/broker/baselines/{baseline_id}/funds")
    def observe_funds(
        request: Request, baseline_id: UUID, body: SourceObservation
    ) -> dict[str, Any]:
        return owner.execute(
            request,
            body.model_dump(mode="json"),
            lambda identifier: broker.observe_funds(
                baseline_id, body.source_batch_id, request_id=identifier
            ),
        )

    @router.get("/broker/funds-entries/{entry_id}")
    def funds_entry(entry_id: UUID) -> dict[str, Any]:
        return broker.get_funds_entry(entry_id)

    return router
