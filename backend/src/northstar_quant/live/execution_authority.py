"""Fixed operator consent; current account/risk admission remains mandatory at execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine

from northstar_quant.broker.stream_records import read_stream_source, text
from northstar_quant.execution.orders import OrderBudget, PendingOrder
from northstar_quant.persistence.sql import write_transaction


def initialize(connection: Connection) -> None:
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS live_execution_authorizations (
            authorization_id TEXT PRIMARY KEY, runtime_id TEXT NOT NULL,
            stream_id TEXT NOT NULL, document TEXT NOT NULL, content_hash TEXT NOT NULL)
    """)
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS live_execution_revocations (
            authorization_id TEXT PRIMARY KEY, command_id TEXT UNIQUE NOT NULL,
            operator TEXT NOT NULL CHECK(operator='owner'), recorded_at TEXT NOT NULL,
            FOREIGN KEY(authorization_id)
              REFERENCES live_execution_authorizations(authorization_id))
    """)
    for table in ("live_execution_authorizations", "live_execution_revocations"):
        for action in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} "
                f"BEFORE {action} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'Execution consent is immutable'); END"
            )


@dataclass(frozen=True)
class ExecutionLimits:
    max_order_lots: int
    max_total_lots: int
    max_order_budget: OrderBudget

    def __post_init__(self) -> None:
        if (
            type(self.max_order_lots) is not int
            or type(self.max_total_lots) is not int
            or not 1 <= self.max_order_lots <= self.max_total_lots <= 1_000_000_000
            or not isinstance(self.max_order_budget, OrderBudget)
        ):
            raise ValueError("execution consent requires explicit bounded lots and money")

    def to_dict(self) -> dict[str, Any]:
        return dict(
            max_order_lots=self.max_order_lots,
            max_total_lots=self.max_total_lots,
            max_order_budget=self.max_order_budget.to_dict(),
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExecutionLimits:
        if set(value) != {"max_order_lots", "max_total_lots", "max_order_budget"}:
            raise ValueError("execution limits differ from the current model")
        return cls(
            value["max_order_lots"],
            value["max_total_lots"],
            OrderBudget.from_dict(value["max_order_budget"]),
        )


def _encode(value: dict[str, Any]) -> tuple[str, str]:
    content = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return content, hashlib.sha256(content.encode()).hexdigest()


def _get(connection: Connection, identifier: UUID) -> dict[str, Any]:
    row = (
        connection.exec_driver_sql(
            "SELECT * FROM live_execution_authorizations WHERE authorization_id=?",
            (str(identifier),),
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("execution consent not found")
    document = json.loads(row["document"])
    if (
        not isinstance(document, dict)
        or _encode(document) != (row["document"], row["content_hash"])
        or document["authorization_id"] != row["authorization_id"]
        or document["runtime_id"] != row["runtime_id"]
        or document["stream_id"] != row["stream_id"]
    ):
        raise ValueError("execution consent identity is damaged")
    return document


def _source(connection: Connection, stream_id: UUID) -> dict[str, Any]:
    source = read_stream_source(connection, stream_id)
    binding = source["binding"]
    assert isinstance(binding, dict)
    return dict(
        runtime_id=binding["runtime_id"],
        binding_hash=source["binding_hash"],
        environment=binding["environment"],
        profile=binding["profile"],
        account_id=binding["account_id"],
        contract_id=binding["contract_id"],
        configuration=binding["configuration"],
        code_revision=binding["code_revision"],
    )


class ExecutionAuthority:
    def __init__(self, engine: Engine, runtime_id: UUID, check_ownership: Callable[[], None]):
        self.engine, self.runtime_id, self.check_ownership = engine, runtime_id, check_ownership

    def grant(
        self,
        stream_id: UUID,
        limits: ExecutionLimits,
        expires_at: datetime,
        *,
        request_id: UUID,
        operator: str,
    ) -> dict[str, Any]:
        if operator != "owner":
            raise PermissionError("only the owner may consent to investment execution")
        if not isinstance(expires_at, datetime) or expires_at.utcoffset() != timedelta(0):
            raise ValueError("execution consent deadline must be explicit UTC")
        request = dict(
            stream_id=str(stream_id), limits=limits.to_dict(), expires_at=expires_at.isoformat()
        )
        with write_transaction(self.engine) as connection:
            try:
                saved = _get(connection, request_id)
            except LookupError:
                saved = None
            if saved is not None:
                if saved["request"] != request or saved["runtime_id"] != str(self.runtime_id):
                    raise ValueError("execution consent identity already has different inputs")
                return self._view(connection, saved)
            self.check_ownership()
            source = _source(connection, stream_id)
            if source["runtime_id"] != str(self.runtime_id):
                raise ValueError("execution consent requires this runtime's receiver")
            if source["environment"] != "SANDBOX":
                raise ValueError("production execution is not admitted")
            instance = (
                connection.exec_driver_sql("SELECT * FROM live_instance_binding").mappings().one()
            )
            if (
                instance["environment"],
                instance["broker_profile"],
                instance["broker_id"],
                instance["account_id"],
            ) != (
                source["environment"],
                source["profile"]["name"],
                source["profile"]["broker_id"],
                source["account_id"],
            ):
                raise ValueError("execution consent differs from the owned instance")
            stream = (
                connection.execute(
                    text(
                        "SELECT status, created_at, binding FROM broker_streams WHERE stream_id=:id"
                    ),
                    {"id": stream_id},
                )
                .mappings()
                .one()
            )
            now = datetime.now(UTC)
            end = stream["created_at"] + timedelta(
                seconds=stream["binding"]["request"]["duration_seconds"]
            )
            if stream["status"] != "RECEIVING" or not now < expires_at <= end:
                raise ValueError("execution consent must fit the active receiver lifetime")
            document = dict(
                authorization_id=str(request_id),
                runtime_id=str(self.runtime_id),
                stream_id=str(stream_id),
                instance_id=instance["instance_id"],
                operator=operator,
                created_at=now.isoformat(),
                request=request,
                source=source,
            )
            content, digest = _encode(document)
            connection.exec_driver_sql(
                "INSERT INTO live_execution_authorizations VALUES (?, ?, ?, ?, ?)",
                (str(request_id), str(self.runtime_id), str(stream_id), content, digest),
            )
            return self._view(connection, document)

    def revoke(self, identifier: UUID, *, request_id: UUID, operator: str) -> dict[str, Any]:
        if operator != "owner":
            raise PermissionError("only the owner may revoke execution consent")
        with write_transaction(self.engine) as connection:
            document = _get(connection, identifier)
            previous = connection.exec_driver_sql(
                "SELECT command_id FROM live_execution_revocations WHERE authorization_id=?",
                (str(identifier),),
            ).scalar_one_or_none()
            if previous is None:
                self.check_ownership()
                connection.exec_driver_sql(
                    "INSERT INTO live_execution_revocations VALUES (?, ?, ?, ?)",
                    (str(identifier), str(request_id), operator, datetime.now(UTC).isoformat()),
                )
            elif previous != str(request_id):
                raise ValueError("execution consent already has a fixed revocation")
            return self._view(connection, document)

    def get(self, identifier: UUID) -> dict[str, Any]:
        with self.engine.connect() as connection:
            return self._view(connection, _get(connection, identifier))

    def list(self, stream_id: UUID, *, before: int | None = None) -> dict[str, Any]:
        if before is not None and (type(before) is not int or before < 1):
            raise ValueError("invalid execution consent cursor")
        with self.engine.connect() as connection:
            rows = connection.exec_driver_sql(
                "SELECT rowid, authorization_id FROM live_execution_authorizations "
                "WHERE stream_id=? AND (? IS NULL OR rowid<?) ORDER BY rowid DESC LIMIT 101",
                (str(stream_id), before, before),
            ).all()
            return dict(
                authorizations=[
                    self._view(connection, _get(connection, UUID(row[1]))) for row in rows[:100]
                ],
                next_before=rows[99][0] if len(rows) > 100 else None,
            )

    def _view(self, connection: Connection, document: dict[str, Any]) -> dict[str, Any]:
        expired = datetime.now(UTC) >= datetime.fromisoformat(document["request"]["expires_at"])
        revoked = (
            connection.exec_driver_sql(
                "SELECT 1 FROM live_execution_revocations WHERE authorization_id=?",
                (document["authorization_id"],),
            ).first()
            is not None
        )
        status = (
            "REVOKED"
            if revoked
            else "PREVIOUS_RUNTIME"
            if document["runtime_id"] != str(self.runtime_id)
            else "EXPIRED"
            if expired
            else "CONSENTED"
        )
        return {**document, "status": status, "requires_current_admission": True}

    def admit(
        self,
        connection: Connection,
        identifier: UUID,
        stream_id: UUID,
        order: PendingOrder,
        *,
        check_current_account: Callable[[Connection], None],
    ) -> None:
        """Run inside the order/reservation transaction, after core readiness validation."""
        self.check_ownership()
        document = _get(connection, identifier)
        if self._view(connection, document)["status"] != "CONSENTED":
            raise ValueError("execution consent is not current")
        if document["stream_id"] != str(stream_id) or document["source"] != _source(
            connection, stream_id
        ):
            raise ValueError("execution consent source binding differs")
        if document["source"]["contract_id"] != str(order.contract_id):
            raise ValueError("execution consent does not cover this contract")
        stream = (
            connection.execute(
                text("SELECT status, paused FROM broker_streams WHERE stream_id=:id"),
                {"id": stream_id},
            )
            .mappings()
            .one()
        )
        if stream["status"] != "RECEIVING" or stream["paused"]:
            raise ValueError("execution receiver is stopped or paused")
        if order.expires_at > datetime.fromisoformat(document["request"]["expires_at"]):
            raise ValueError("order outlives execution consent")
        limits = ExecutionLimits.from_dict(document["request"]["limits"])
        used = connection.exec_driver_sql(
            "SELECT COALESCE(SUM(json_extract(request, '$.quantity_lots')), 0) "
            "FROM execution_orders WHERE authorization_id=?",
            (str(identifier),),
        ).scalar_one()
        if (
            order.quantity_lots > limits.max_order_lots
            or used + order.quantity_lots > limits.max_total_lots
        ):
            raise ValueError("execution consent lot budget exceeded")
        with localcontext() as context:
            context.prec = 192
            if any(
                getattr(order.budget, field) * Decimal(order.quantity_lots)
                > getattr(limits.max_order_budget, field)
                for field in ("fee", "margin", "gross", "loss")
            ):
                raise ValueError("execution consent money budget exceeded")
        check_current_account(connection)  # Consent is not an account/risk certificate.

    def verify_all(self) -> int:
        count = 0
        with self.engine.connect() as connection:
            for identifier in connection.exec_driver_sql(
                "SELECT authorization_id FROM live_execution_authorizations"
            ).scalars():
                document = _get(connection, UUID(identifier))
                ExecutionLimits.from_dict(document["request"]["limits"])
                if document["source"] != _source(connection, UUID(document["stream_id"])):
                    raise ValueError("execution consent source evidence changed")
                count += 1
        return count
