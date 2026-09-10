"""Immutable order reviews of a fixed account comparison; no send/cancel authority."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, Column, Connection, Engine, MetaData, String, Table, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from northstar_quant import code_revision
from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.broker.records import EvidenceTimestamp
from northstar_quant.live.storage import write_transaction

_metadata = MetaData()
_order_checks = Table(
    "broker_order_checks",
    _metadata,
    Column("check_id", PGUUID(as_uuid=True), primary_key=True),
    Column("position_check_id", PGUUID(as_uuid=True), nullable=False, unique=True),
    Column("recorded_at", EvidenceTimestamp(), nullable=False),
    Column("document", JSON().with_variant(JSONB, "postgresql"), nullable=False),
    Column("sha256", String(64), nullable=False),
)

_EXECUTION = {"order_sending": False, "cancel_sending": False}


def initialize_order_reviews(connection: Connection) -> None:
    _metadata.create_all(connection)
    if connection.dialect.name == "sqlite":
        for table in ("broker_order_checks",):
            for action in ("UPDATE", "DELETE"):
                connection.exec_driver_sql(
                    f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} "
                    f"BEFORE {action} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'Confirmed facts are immutable'); END"
                )
        return
    connection.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION execution_preserve_order_review() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Order review evidence is immutable';
        END; $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS immutable ON broker_order_checks;
        CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON broker_order_checks
            FOR EACH ROW EXECUTE FUNCTION execution_preserve_order_review()
    """)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _time(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    if moment.utcoffset() != UTC.utcoffset(moment):
        raise ValueError("ledger time must use UTC")
    return moment


class OrderReviews:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._ledger = BrokerLedger(engine)

    def _raw(self, identifier: UUID) -> dict[str, Any]:
        if not isinstance(identifier, UUID):
            raise ValueError("order review commands require UUID identities")
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(_order_checks).where(_order_checks.c.check_id == identifier)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError("order review not found")
        document = row["document"]
        if (
            not isinstance(document, dict)
            or _hash(document) != row["sha256"]
            or document.get("check_id") != str(identifier)
            or _time(document["recorded_at"]) != row["recorded_at"]
            or document.get("position_check_id") != str(row["position_check_id"])
        ):
            raise ValueError("order review evidence is damaged")
        return document

    def get(self, check_id: UUID) -> dict[str, Any]:
        checked = self._raw(check_id)
        try:
            parent = self._ledger.get_check(UUID(checked["position_check_id"]))
        except LookupError as error:
            raise ValueError("order comparison parent evidence is missing") from error
        if (
            checked["position_check_hash"] != _hash(parent)
            or checked["entry_id"] != parent["entry_id"]
            or checked["query_batch_id"] != parent["query_batch_id"]
        ):
            raise ValueError("order comparison input evidence is damaged")
        return checked

    def check(self, position_check_id: UUID, *, request_id: UUID) -> dict[str, Any]:
        """Freeze an order/fill review of an existing independent quantity comparison.

        No new query, fill ingestion, catalog mutation, or reservation release.
        The parent fixes both the later query and the exact historical entry;
        newer entries must never change this comparison's expected fill set.
        """
        from sqlalchemy.dialects.postgresql import insert

        from northstar_quant.execution.observations import inspect_orders

        if not isinstance(position_check_id, UUID) or not isinstance(request_id, UUID):
            raise ValueError("order comparisons require UUID identities")
        try:
            saved = self.get(request_id)
        except LookupError:
            pass
        else:
            if saved["position_check_id"] != str(position_check_id):
                raise ValueError("order comparison is already bound to different input")
            return saved
        parent, sources, known = self._ledger.order_review_inputs(position_check_id)
        result = inspect_orders(parent, sources, known)
        now = _now()
        document = {
            "check_id": str(request_id),
            "position_check_id": str(position_check_id),
            "entry_id": parent["entry_id"],
            "query_batch_id": parent["query_batch_id"],
            "position_check_hash": _hash(parent),
            "recorded_at": now,
            "code_revision": code_revision(),
            **result,
            "scope": "ORDER_OBSERVATIONS_AND_RECORDED_FILLS",
            "reconciliation": "UNRECONCILED",
            "execution": dict(_EXECUTION),
            "limitations": [
                "OBSERVED_ORDERS_HAVE_NO_LOCAL_SEND_OR_CANCEL_AUTHORIZATION",
                "CUMULATIVE_QUANTITIES_DO_NOT_REPLACE_INDIVIDUAL_FILLS",
                "NO_RESERVATION_RELEASE_OR_ORDER_LIFECYCLE_RECOVERY",
                "NO_CONFIRMED_FEES_CASHFLOW_OR_SETTLEMENT_LEDGER",
                "QUERIES_ARE_NOT_ATOMIC_OR_CONTINUOUS_EVENT_COVERAGE",
            ],
        }
        with write_transaction(self._engine) as connection:
            connection.execute(
                insert(_order_checks)
                .values(
                    check_id=request_id,
                    position_check_id=position_check_id,
                    recorded_at=_time(now),
                    document=document,
                    sha256=_hash(document),
                )
                .on_conflict_do_nothing()
            )
        try:
            saved = self.get(request_id)
        except LookupError as error:
            raise ValueError(
                "position comparison already has an order review; read it instead"
            ) from error
        if saved["position_check_id"] != str(position_check_id):
            raise ValueError("order comparison is already bound to different input")
        return saved

    def context(self, query_batch_id: UUID) -> dict[str, Any]:
        positions = self._ledger.context(query_batch_id)
        baseline = positions["baseline_id"]
        parent_ids = [] if baseline is None else self._ledger.comparison_ids(UUID(baseline))
        current = positions["current_check"]
        with self._engine.connect() as connection:
            ids = list(
                connection.scalars(
                    select(_order_checks.c.check_id)
                    .where(_order_checks.c.position_check_id.in_(parent_ids))
                    .order_by(_order_checks.c.recorded_at.desc())
                    .limit(20)
                )
            )
            current_id = (
                None
                if current is None
                else connection.scalar(
                    select(_order_checks.c.check_id).where(
                        _order_checks.c.position_check_id == UUID(current["check_id"])
                    )
                )
            )
        return {
            "current_order_check": None if current_id is None else self.get(current_id),
            "order_checks": [self.get(identifier) for identifier in ids],
        }

    def verify_all(self) -> int:
        count = 0
        with self._engine.connect() as connection:
            for identifier in connection.scalars(select(_order_checks.c.check_id)).yield_per(100):
                self.get(identifier)
                count += 1
        return count
