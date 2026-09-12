"""Retain one bounded, read-only CTP query without inventing an account ledger.

Callback identity, field selection and request completion live beside the evidence
they explain. A complete query is not an atomic account snapshot or reconciliation.
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
from northstar_quant.broker.settings import get_profile, validate_instrument
from northstar_quant.persistence.sql import UTCDateTime, write_transaction

_QUERIES = {
    "account": ("ReqQryTradingAccount", "OnRspQryTradingAccount"),
    "positions": ("ReqQryInvestorPosition", "OnRspQryInvestorPosition"),
    "orders": ("ReqQryOrder", "OnRspQryOrder"),
    "trades": ("ReqQryTrade", "OnRspQryTrade"),
    "instrument": ("ReqQryInstrument", "OnRspQryInstrument"),
    "margin": ("ReqQryInstrumentMarginRate", "OnRspQryInstrumentMarginRate"),
    "commission": ("ReqQryInstrumentCommissionRate", "OnRspQryInstrumentCommissionRate"),
}
_ACCOUNT_ROWS = {"account", "positions", "orders", "trades"}
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
            result = _result(binding, capture)
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
        return {**binding, **_result(binding, None)}
    result = row["result"]
    if (
        not isinstance(result, dict)
        or result.get("status") != row["status"]
        or capture_hash({"binding": binding, "result": result}) != row["result_hash"]
    ):
        raise ValueError("saved broker query result no longer matches its evidence")
    capture = QueryCapture.from_dict(result["capture"])
    if (
        parse_time(capture.started_at) < parse_time(str(binding["created_at"]))
        or result != _result(binding, capture)
    ):
        raise ValueError("saved broker query projection differs from its retained callbacks")
    return {**binding, **result}


def _result(binding: dict[str, object], capture: QueryCapture | None) -> dict[str, object]:
    sections: dict[str, dict[str, object]] = {
        name: {
            "status": "NOT_OBSERVED",
            "request_id": None,
            "rows": None,
            "first_received_at": None,
            "last_received_at": None,
            "error_ids": [],
        }
        for name in _QUERIES
    }
    sections["instrument"]["identity"] = "UNKNOWN"
    reasons: set[str] = set()
    unknown: set[str] = {
        "TRADING_SESSION_TIMES_NOT_VERIFIED",
        "LOCAL_LEDGER_NOT_ESTABLISHED",
        "QUERY_WINDOW_IS_NOT_AN_ATOMIC_ACCOUNT_SNAPSHOT",
    }
    # This is the TD account identity. MD may omit account fields entirely;
    # observing that public market session cannot undo verified account facts.
    identity = "UNKNOWN"
    trading_day: str | None = None
    market: dict[str, object] = {
        "status": "NOT_OBSERVED",
        "login": None,
        "login_identity": "UNKNOWN",
        "depth": None,
        "continuous_feed": False,
    }
    fatal = False
    if capture is not None:
        requests: dict[tuple[str, int], tuple[str, int]] = {}
        terminated: set[tuple[str, int]] = set()
        profile = cast(dict[str, object], binding["profile"])
        account_id, instrument = binding["account_id"], binding["instrument"]
        td_connected = False
        context_seen = False
        for event in capture.events:
            data = event.data
            key = None if event.request_id is None else (event.channel, event.request_id)
            if event.error_id:
                reasons.add("BROKER_REPORTED_ERROR")
                fatal = True
            if event.callback == "CaptureStarted":
                expected_context = {
                    "profile_name": profile["name"],
                    "td_front": profile["td_front"],
                    "md_front": profile["md_front"],
                    "broker_id": profile["broker_id"],
                    "account_id": account_id,
                    "instrument": instrument,
                }
                if (
                    context_seen
                    or event.sequence != 1
                    or event.channel != "TD"
                    or data != expected_context
                ):
                    reasons.add("CAPTURE_ENVIRONMENT_BINDING_MISMATCH")
                    fatal = True
                else:
                    context_seen = True
            if event.callback == "OnFrontConnected" and event.channel == "TD":
                td_connected = True
            if event.callback == "OnFrontDisconnected":
                reasons.add(f"{event.channel}_DISCONNECTED_DURING_CAPTURE")
                fatal = True
            if event.callback == "OnHeartBeatWarning":
                reasons.add(f"{event.channel}_HEARTBEAT_WARNING")
            if event.callback == "RequestSent":
                if data is None or type(data.get("return_code")) is not int:
                    reasons.add("REQUEST_EVIDENCE_MISSING")
                    continue
                section, method = data.get("section"), data.get("method")
                if event.channel == "MD" and section == "depth" and method == "SubscribeMarketData":
                    market["subscription_requested_at"] = event.received_at
                    market["subscription_return_code"] = data["return_code"]
                    if data["return_code"] != 0:
                        reasons.add("SDK_REJECTED_MARKET_SUBSCRIPTION")
                        fatal = True
                    continue
                if key is None:
                    reasons.add("REQUEST_EVIDENCE_MISSING")
                    continue
                if key in requests:
                    reasons.add("REQUEST_ID_REUSED_WITHIN_CAPTURE")
                    continue
                requests[key] = (str(section), event.sequence)
                if data["return_code"] != 0:
                    reasons.add("SDK_REJECTED_REQUEST")
                    fatal = True
                if event.channel == "TD" and section in _QUERIES:
                    if identity != "CONFIRMED":
                        reasons.add("QUERY_SENT_BEFORE_CONFIRMED_LOGIN")
                    item = sections[str(section)]
                    if item["status"] != "NOT_OBSERVED":
                        reasons.add("QUERY_SECTION_REQUESTED_MORE_THAN_ONCE")
                    if method != _QUERIES[str(section)][0]:
                        reasons.add("QUERY_METHOD_SCOPE_MISMATCH")
                    item.update(
                        status="WAITING" if data["return_code"] == 0 else "ERROR",
                        request_id=event.request_id,
                        rows=[],
                    )
                continue
            if event.callback == "OnRspUserLogin":
                request = None if key is None else requests.get(key)
                login_complete = (
                    request is not None and request[0] == "login" and event.is_last is True
                )
                if not login_complete:
                    reasons.add("LOGIN_REQUEST_OR_COMPLETION_NOT_CONFIRMED")
                if data is not None and not event.error_id:
                    matching = (
                        data.get("BrokerID") == profile["broker_id"]
                        and data.get("UserID") == account_id
                    )
                    if event.channel == "MD":
                        market["login"] = dict(data)
                        market["status"] = "LOGIN_OBSERVED"
                        if trading_day is None or data.get("TradingDay") != trading_day:
                            reasons.add("MARKET_LOGIN_TRADING_DAY_MISMATCH")
                            fatal = True
                        if any(
                            data.get(field) not in {None, "", expected}
                            for field, expected in (
                                ("BrokerID", profile["broker_id"]),
                                ("UserID", account_id),
                            )
                        ):
                            market["login_identity"] = "MISMATCH"
                            reasons.add("MARKET_LOGIN_IDENTITY_MISMATCH")
                            fatal = True
                        elif matching and login_complete and market["login_identity"] != "MISMATCH":
                            market["login_identity"] = "CONFIRMED"
                        else:
                            unknown.add("MARKET_LOGIN_IDENTITY_UNKNOWN")
                    elif not matching:
                        identity = "MISMATCH"
                        reasons.add("LOGIN_ACCOUNT_IDENTITY_MISMATCH")
                        fatal = True
                    else:
                        if login_complete and identity != "MISMATCH":
                            identity = "CONFIRMED"
                        day = data.get("TradingDay")
                        if isinstance(day, str) and re.fullmatch(r"[0-9]{8}", day):
                            trading_day = day
                        else:
                            reasons.add("BROKER_TRADING_DAY_UNKNOWN")
                else:
                    reasons.add("LOGIN_RESPONSE_MISSING_OR_FAILED")
            if event.callback == "OnRspAuthenticate" and data is not None:
                request = None if key is None else requests.get(key)
                if request is None or request[0] != "authenticate" or event.is_last is not True:
                    reasons.add("AUTHENTICATION_REQUEST_OR_COMPLETION_NOT_CONFIRMED")
                if data.get("BrokerID") != profile["broker_id"] or data.get("UserID") != account_id:
                    reasons.add("AUTHENTICATION_ACCOUNT_IDENTITY_MISMATCH")
                    identity = "MISMATCH"
                    fatal = True
            section = next(
                (name for name, (_, callback) in _QUERIES.items() if callback == event.callback),
                None,
            )
            if section is not None:
                item = sections[section]
                request = None if key is None else requests.get(key)
                if (
                    event.channel != "TD"
                    or request is None
                    or request[0] != section
                    or key in terminated
                ):
                    reasons.add("UNMATCHED_OR_LATE_QUERY_RESPONSE")
                    continue
                if item["request_id"] != event.request_id:
                    reasons.add("QUERY_RESPONSE_REQUEST_ID_MISMATCH")
                    continue
                if item["first_received_at"] is None:
                    item["first_received_at"] = event.received_at
                item["last_received_at"] = event.received_at
                if event.error_id:
                    cast(list[int], item["error_ids"]).append(event.error_id)
                    item["status"] = "ERROR"
                # CTP's InstrumentID query can return prefix matches, including
                # options. Preserve every callback in capture, while this
                # selected-contract projection accepts exact equality only.
                if data is not None and (
                    section != "instrument" or data.get("InstrumentID") == instrument
                ):
                    cast(list[dict[str, object]], item["rows"]).append(dict(data))
                    if section in _ACCOUNT_ROWS:
                        investor_key = "AccountID" if section == "account" else "InvestorID"
                        if (
                            data.get("BrokerID") != profile["broker_id"]
                            or data.get(investor_key) != account_id
                        ):
                            reasons.add("QUERY_ACCOUNT_IDENTITY_MISMATCH")
                            identity = "MISMATCH"
                            fatal = True
                        if data.get("TradingDay") != trading_day or trading_day is None:
                            reasons.add("QUERY_TRADING_DAY_UNCONFIRMED")
                        if section == "account" and data.get("CurrencyID") != "CNY":
                            reasons.add("ACCOUNT_CURRENCY_MISMATCH")
                            fatal = True
                    elif data.get("InstrumentID") != instrument:
                        reasons.add("INSTRUMENT_QUERY_IDENTITY_MISMATCH")
                        fatal = True
                    if section in {"margin", "commission"}:
                        if data.get("BrokerID") not in {None, "", profile["broker_id"]} or data.get(
                            "InvestorID"
                        ) not in {None, "", account_id}:
                            reasons.add("TERMS_ACCOUNT_IDENTITY_MISMATCH")
                            fatal = True
                if event.is_last is True:
                    assert key is not None
                    terminated.add(key)
                    if item["status"] != "ERROR":
                        item["status"] = "COMPLETE"
                elif event.is_last is None:
                    reasons.add("QUERY_COMPLETION_FLAG_UNKNOWN")
            if event.callback in {"OnRtnOrder", "OnRtnTrade"}:
                unknown.add("ACCOUNT_EVENTS_ARRIVED_DURING_QUERY")
                if data is not None and (
                    data.get("BrokerID") != profile["broker_id"]
                    or data.get("InvestorID") != account_id
                ):
                    reasons.add("ACCOUNT_CALLBACK_IDENTITY_MISMATCH")
                    fatal = True
            if event.callback == "OnRtnDepthMarketData":
                if event.channel != "MD" or data is None or data.get("InstrumentID") != instrument:
                    reasons.add("MARKET_SNAPSHOT_IDENTITY_MISMATCH")
                else:
                    market.update(
                        status="SNAPSHOT_OBSERVED",
                        depth={
                            "received_at": event.received_at,
                            "data": dict(data),
                        },
                    )
            if event.callback == "OnRspSubMarketData":
                if (
                    event.channel != "MD"
                    or data is None
                    or data.get("InstrumentID") != instrument
                    or "subscription_requested_at" not in market
                ):
                    reasons.add("MARKET_SUBSCRIPTION_CONTEXT_NOT_CONFIRMED")
                else:
                    market["subscription"] = {
                        "received_at": event.received_at,
                        "request_id": event.request_id,
                        "is_last": event.is_last,
                        "error_id": event.error_id,
                        "data": dict(data),
                    }
        if not td_connected:
            reasons.add("TD_CONNECTION_NOT_OBSERVED")
        if not context_seen:
            reasons.add("LOCAL_CONNECTION_CONTEXT_NOT_CONFIRMED")
        if identity != "CONFIRMED":
            reasons.add("BROKER_ACCOUNT_IDENTITY_NOT_CONFIRMED")
        if trading_day is None:
            reasons.add("BROKER_TRADING_DAY_UNKNOWN")
        for name, section in sections.items():
            if section["status"] != "COMPLETE":
                reasons.add(f"{name.upper()}_QUERY_NOT_COMPLETE")
            if name == "instrument" and section["status"] == "COMPLETE":
                count = len(cast(list[dict[str, object]], section["rows"]))
                if count == 1:
                    section["identity"] = "CONFIRMED"
                else:
                    reasons.add(
                        "EXACT_INSTRUMENT_NOT_FOUND"
                        if count == 0
                        else "EXACT_INSTRUMENT_NOT_UNIQUE"
                    )
            if name in {"account", "instrument", "margin", "commission"} and not section["rows"]:
                unknown.add(f"{name.upper()}_FACTS_NOT_RETURNED")
        if capture.failure_code is not None:
            reasons.add(capture.failure_code)
            fatal = True
        if capture.trader_api_version is None:
            unknown.add("TRADER_API_VERSION_UNKNOWN")
        if market["login_identity"] == "UNKNOWN":
            unknown.add("MARKET_LOGIN_IDENTITY_UNKNOWN")
    else:
        reasons.add("QUERY_NOT_FINISHED_OR_CALLER_INTERRUPTED")
    status = (
        "PENDING"
        if capture is None
        else "FAILED"
        if fatal
        else "INCOMPLETE"
        if reasons
        else "COMPLETE"
    )
    return {
        "status": status,
        "capture": None if capture is None else capture.to_dict(),
        "completeness": {
            "status": "COMPLETE" if status == "COMPLETE" else "INCOMPLETE",
            "identity": identity,
            "trading_day": trading_day,
            "sections": sections,
            "reasons": sorted(reasons),
        },
        "market": market,
        "reconciliation": {
            "status": "UNRECONCILED",
            "local_ledger": "NOT_ESTABLISHED",
            "differences": None,
            "reasons": sorted(unknown | reasons),
        },
        "execution": {"order_sending": False, "cancel_sending": False},
    }
