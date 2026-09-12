"""Retain one bounded, read-only CTP query without inventing an account ledger.

Persistence owns fixed source identity and integrity. Native receipt interpretation
is shared with recovery through query_projection; a complete query is not reconciliation.
This Module contains no network calls, order sender or simulated account importer.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Connection,
    Engine,
    MetaData,
    String,
    Table,
    Uuid,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from northstar_quant import code_revision
from northstar_quant.broker.events import (
    QueryCapture,
    capture_hash,
    parse_time,
    timestamp,
)
from northstar_quant.broker.query_projection import project_query
from northstar_quant.broker.settings import get_profile, validate_instrument
from northstar_quant.persistence.sql import UTCDateTime, write_transaction

_metadata = MetaData()
_batches = Table(
    "broker_query_batches",
    _metadata,
    Column("batch_id", Uuid(as_uuid=True), primary_key=True),
    Column("profile_name", String(32), nullable=False),
    Column("profile", JSON().with_variant(JSONB, "postgresql"), nullable=False),
    Column("account_id", String(12), nullable=False),
    Column("instrument", String(32), nullable=False),
    Column("query_scope", JSON().with_variant(JSONB, "postgresql"), nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("code_revision", String(64), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("binding_hash", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    Column("result", JSON().with_variant(JSONB, "postgresql")),
    Column("result_hash", String(64)),
    CheckConstraint("status IN ('PENDING', 'FAILED', 'INCOMPLETE', 'COMPLETE')"),
    CheckConstraint(
        "(status = 'PENDING' AND result IS NULL AND result_hash IS NULL) OR "
        "(status <> 'PENDING' AND result IS NOT NULL AND result_hash IS NOT NULL)"
    ),
)


def initialize_broker_records(connection: Connection) -> None:
    """Install the current read-only evidence table during atomic initialization."""

    _metadata.create_all(connection)
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS immutable_query_delete "
            "BEFORE DELETE ON broker_query_batches "
            "BEGIN SELECT RAISE(ABORT, 'Query evidence is immutable'); END"
        )
        fields = (
            "batch_id",
            "profile_name",
            "profile",
            "account_id",
            "instrument",
            "query_scope",
            "created_at",
            "code_revision",
            "request_hash",
            "binding_hash",
        )
        changed = " OR ".join(f"OLD.{field} IS NOT NEW.{field}" for field in fields)
        connection.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS immutable_query_update "
            "BEFORE UPDATE ON broker_query_batches "
            f"WHEN OLD.status <> 'PENDING' OR {changed} "
            "BEGIN SELECT RAISE(ABORT, 'Query identity and terminal evidence are immutable'); END"
        )
        return
    connection.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION broker_protect_query_evidence() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.status <> 'PENDING' THEN
                RAISE EXCEPTION 'Broker query evidence is immutable';
            END IF;
            IF (NEW.batch_id, NEW.profile_name, NEW.profile, NEW.account_id, NEW.instrument,
                NEW.query_scope, NEW.created_at, NEW.code_revision, NEW.request_hash,
                NEW.binding_hash)
                IS DISTINCT FROM
                (OLD.batch_id, OLD.profile_name, OLD.profile, OLD.account_id, OLD.instrument,
                OLD.query_scope, OLD.created_at, OLD.code_revision, OLD.request_hash,
                OLD.binding_hash) THEN
                RAISE EXCEPTION 'Broker query identity is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    connection.exec_driver_sql("DROP TRIGGER IF EXISTS immutable ON broker_query_batches")
    connection.exec_driver_sql("""
        CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON broker_query_batches
        FOR EACH ROW EXECUTE FUNCTION broker_protect_query_evidence()
    """)


class BrokerRecords:
    """Durable, environment-bound observations; never authority to send an order."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def begin(
        self, profile: dict[str, object], account_id: str, instrument: str, *, request_id: UUID
    ) -> dict[str, object]:
        """Bind one request before connection; repeating it cannot create another query."""

        if not isinstance(request_id, UUID):
            raise ValueError("broker query requires a UUID request identity")
        if (
            not isinstance(profile, dict)
            or profile != get_profile(cast(str, profile.get("name"))).identity()
        ):
            raise ValueError("broker query must bind an explicitly approved SimNow environment")
        _account_id(account_id)
        instrument = validate_instrument(instrument)
        profile = dict(profile)
        query_scope = {
            "account": {"currency": "CNY"},
            "positions": {"instruments": "ALL"},
            "orders": {"instruments": "ALL", "period": "BROKER_TRADING_DAY"},
            "trades": {"instruments": "ALL", "period": "BROKER_TRADING_DAY"},
            "instrument": {"instrument": instrument},
            "margin": {"instrument": instrument, "hedge_flag": "1"},
            "commission": {"instrument": instrument},
            "depth": {"instrument": instrument, "delivery": "BOUNDED_OPTIONAL_SNAPSHOT"},
        }
        request = {
            "profile": profile,
            "account_id": account_id,
            "instrument": instrument,
            "query_scope": query_scope,
        }
        created_at = datetime.now(UTC)
        binding = {
            **request,
            "batch_id": str(request_id),
            "created_at": timestamp(created_at),
            "code_revision": code_revision(),
        }
        request_hash = capture_hash(request)
        with write_transaction(self._engine) as connection:
            connection.execute(
                (sqlite_insert if connection.dialect.name == "sqlite" else pg_insert)(_batches)
                .values(
                    batch_id=request_id,
                    profile_name=profile["name"],
                    profile=profile,
                    account_id=account_id,
                    instrument=instrument,
                    query_scope=query_scope,
                    created_at=created_at,
                    code_revision=binding["code_revision"],
                    request_hash=request_hash,
                    binding_hash=capture_hash(binding),
                    status="PENDING",
                )
                .on_conflict_do_nothing(index_elements=[_batches.c.batch_id])
            )
            existing = connection.scalar(
                select(_batches.c.request_hash).where(_batches.c.batch_id == request_id)
            )
            if existing != request_hash:
                raise ValueError("broker request identity is already bound to different input")
        return self.get(request_id)

    def finish(self, batch_id: UUID, capture: QueryCapture) -> dict[str, object]:
        """Commit bounded evidence once, including failed or incomplete reception.

        An interrupted caller leaves PENDING: this Module neither reconnects nor
        claims to have durably streamed callbacks that were still in that process.
        """

        if not isinstance(batch_id, UUID) or not isinstance(capture, QueryCapture):
            raise ValueError("broker completion requires a batch UUID and QueryCapture")
        # Re-copy nested field dictionaries through the same safe Interface.
        capture = QueryCapture.from_dict(capture.to_dict())
        with write_transaction(self._engine) as connection:
            row = (
                connection.execute(
                    select(_batches).where(_batches.c.batch_id == batch_id).with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LookupError("broker query batch not found")
            binding = _binding(dict(row))
            if parse_time(capture.started_at) < cast(datetime, row["created_at"]):
                raise ValueError("broker capture predates its bound query request")
            if row["status"] != "PENDING":
                saved = _stored(dict(row))
                if saved["capture"] != capture.to_dict():
                    raise ValueError("broker query completion conflicts with saved evidence")
                return saved
            result = project_query(binding, capture)
            result_hash = capture_hash({"binding": binding, "result": result})
            connection.execute(
                update(_batches)
                .where(_batches.c.batch_id == batch_id)
                .values(
                    status=result["status"],
                    result=result,
                    result_hash=result_hash,
                )
            )
        return self.get(batch_id)

    def get(self, batch_id: UUID) -> dict[str, object]:
        if not isinstance(batch_id, UUID):
            raise ValueError("broker query requires a UUID identity")
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(_batches).where(_batches.c.batch_id == batch_id))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError("broker query batch not found")
        return _stored(dict(row))

    def list(
        self, *, profile_name: str | None = None, account_id: str | None = None, limit: int = 50
    ) -> list[dict[str, object]]:
        """List bound summaries; opening a batch verifies its complete evidence."""

        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("broker query list limit must be between 1 and 100")
        query = (
            select(*[column for column in _batches.c if column.name != "result"])
            .order_by(_batches.c.created_at.desc(), _batches.c.batch_id)
            .limit(limit)
        )
        if profile_name is not None:
            get_profile(profile_name)
            query = query.where(_batches.c.profile_name == profile_name)
        if account_id is not None:
            _account_id(account_id)
            query = query.where(_batches.c.account_id == account_id)
        with self._engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [
            {
                **_binding(dict(row)),
                "status": row["status"],
                "reconciliation": {"status": "UNRECONCILED", "local_ledger": "NOT_ESTABLISHED"},
                "execution": {"order_sending": False, "cancel_sending": False},
            }
            for row in rows
        ]


def _account_id(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,12}", value) is None:
        raise ValueError("broker investor identity must contain 1 to 12 ASCII digits")


def _binding(row: dict[str, object]) -> dict[str, object]:
    binding = {
        "batch_id": str(row["batch_id"]),
        "profile": row["profile"],
        "account_id": row["account_id"],
        "instrument": row["instrument"],
        "query_scope": row["query_scope"],
        "created_at": timestamp(cast(datetime, row["created_at"])),
        "code_revision": row["code_revision"],
    }
    request = {key: binding[key] for key in ("profile", "account_id", "instrument", "query_scope")}
    profile = binding["profile"]
    if (
        not isinstance(profile, dict)
        or profile.get("name") != row["profile_name"]
        or capture_hash(binding) != row["binding_hash"]
        or capture_hash(request) != row["request_hash"]
    ):
        raise ValueError("saved broker query identity no longer matches its evidence")
    return binding


def _stored(row: dict[str, object]) -> dict[str, object]:
    binding = _binding(row)
    if row["status"] == "PENDING":
        if row["result"] is not None or row["result_hash"] is not None:
            raise ValueError("unfinished broker query contains conflicting completion evidence")
        return {**binding, **project_query(binding, None)}
    result = row["result"]
    if (
        not isinstance(result, dict)
        or result.get("status") != row["status"]
        or capture_hash({"binding": binding, "result": result}) != row["result_hash"]
    ):
        raise ValueError("saved broker query result no longer matches its evidence")
    capture = QueryCapture.from_dict(result["capture"])
    if parse_time(capture.started_at) < parse_time(
        str(binding["created_at"])
    ) or result != project_query(binding, capture):
        raise ValueError("saved broker query projection differs from its retained callbacks")
    return {**binding, **result}
