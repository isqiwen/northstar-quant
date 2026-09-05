"""Retain one bounded, read-only CTP query without inventing an account ledger.

Callback identity, field selection and request completion live beside the evidence
they explain. A complete query is not an atomic account snapshot or reconciliation.
This Module contains no network calls, order sender or simulated account importer.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Column,
    Connection,
    DateTime,
    Engine,
    MetaData,
    String,
    Table,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from northstar_quant.broker.settings import get_profile, validate_instrument
from northstar_quant.runtime import implementation_hash

MAX_EVENTS = 10000
MAX_CAPTURE_BYTES = 8 * 1024 * 1024

_ORDER_FIELDS = tuple(
    "BrokerID InvestorID InstrumentID OrderRef UserID OrderPriceType Direction CombOffsetFlag "
    "CombHedgeFlag LimitPrice VolumeTotalOriginal TimeCondition VolumeCondition MinVolume "
    "RequestID OrderLocalID ExchangeID ParticipantID ClientID OrderSubmitStatus TradingDay "
    "SettlementID OrderSysID OrderSource OrderStatus OrderType VolumeTraded VolumeTotal "
    "InsertDate InsertTime ActiveTime SuspendTime UpdateTime CancelTime SequenceNo FrontID "
    "SessionID".split()
)
_TRADE_FIELDS = tuple(
    "BrokerID InvestorID InstrumentID OrderRef UserID ExchangeID TradeID Direction OrderSysID "
    "ParticipantID ClientID TradingRole ExchangeInstID OffsetFlag HedgeFlag Price Volume "
    "TradeDate TradeTime TradeType PriceSource OrderLocalID TradingDay "
    "SettlementID SequenceNo".split()
)

# These are the exact CTP fields this read-only application retains. Native code
# copies these named attributes immediately; pointers, credentials, unrestricted
# error strings and machine-identification fields never cross the Interface.
CALLBACK_FIELDS: dict[str, tuple[str, ...]] = {
    "CaptureStarted": (
        "profile_name",
        "td_front",
        "md_front",
        "broker_id",
        "account_id",
        "instrument",
    ),
    "RequestSent": ("section", "method", "return_code"),
    "OnFrontConnected": (),
    "OnFrontDisconnected": ("Reason",),
    "OnHeartBeatWarning": ("TimeLapse",),
    "OnRspError": (),
    "OnRspAuthenticate": ("BrokerID", "UserID", "AppID", "AppType"),
    "OnRspUserLogin": ("TradingDay", "LoginTime", "BrokerID", "UserID", "FrontID", "SessionID"),
    "OnRspQryTradingAccount": tuple(
        "BrokerID AccountID CurrencyID TradingDay SettlementID PreBalance PreMargin Deposit "
        "Withdraw FrozenMargin FrozenCash FrozenCommission CurrMargin CashIn Commission "
        "CloseProfit PositionProfit Balance Available WithdrawQuota Reserve".split()
    ),
    "OnRspQryInvestorPosition": tuple(
        "InstrumentID BrokerID InvestorID PosiDirection HedgeFlag PositionDate YdPosition "
        "Position LongFrozen ShortFrozen OpenVolume CloseVolume OpenAmount CloseAmount "
        "PositionCost PreMargin UseMargin FrozenMargin FrozenCash FrozenCommission CashIn "
        "Commission CloseProfit PositionProfit PreSettlementPrice SettlementPrice TradingDay "
        "SettlementID OpenCost ExchangeMargin TodayPosition MarginRateByMoney MarginRateByVolume "
        "ExchangeID".split()
    ),
    "OnRspQryOrder": _ORDER_FIELDS,
    "OnRtnOrder": _ORDER_FIELDS,
    "OnRspQryTrade": _TRADE_FIELDS,
    "OnRtnTrade": _TRADE_FIELDS,
    "OnRspQryInstrument": tuple(
        "InstrumentID ExchangeID InstrumentName ExchangeInstID ProductID ProductClass DeliveryYear "
        "DeliveryMonth MaxMarketOrderVolume MinMarketOrderVolume MaxLimitOrderVolume "
        "MinLimitOrderVolume VolumeMultiple PriceTick CreateDate OpenDate ExpireDate "
        "StartDelivDate "
        "EndDelivDate InstLifePhase IsTrading PositionType PositionDateType LongMarginRatio "
        "ShortMarginRatio MaxMarginSideAlgorithm".split()
    ),
    "OnRspQryInstrumentMarginRate": tuple(
        "InstrumentID InvestorRange BrokerID InvestorID HedgeFlag LongMarginRatioByMoney "
        "LongMarginRatioByVolume ShortMarginRatioByMoney ShortMarginRatioByVolume IsRelative "
        "ExchangeID".split()
    ),
    "OnRspQryInstrumentCommissionRate": tuple(
        "InstrumentID InvestorRange BrokerID InvestorID OpenRatioByMoney OpenRatioByVolume "
        "CloseRatioByMoney CloseRatioByVolume CloseTodayRatioByMoney CloseTodayRatioByVolume "
        "ExchangeID".split()
    ),
    "OnRspSubMarketData": ("InstrumentID",),
    "OnRtnDepthMarketData": tuple(
        "TradingDay InstrumentID ExchangeID ExchangeInstID LastPrice PreSettlementPrice "
        "PreClosePrice PreOpenInterest OpenPrice HighestPrice LowestPrice Volume Turnover "
        "OpenInterest ClosePrice SettlementPrice UpperLimitPrice LowerLimitPrice PreDelta "
        "CurrDelta UpdateTime UpdateMillisec BidPrice1 BidVolume1 AskPrice1 AskVolume1 "
        "AveragePrice "
        "ActionDay".split()
    ),
}


@dataclass(frozen=True, slots=True)
class BrokerEvent:
    """A copied, credential-free callback in one local capture sequence."""

    sequence: int
    channel: str
    callback: str
    request_id: int | None
    is_last: bool | None
    received_at: str
    error_id: int
    data: dict[str, object] | None

    def __post_init__(self) -> None:
        # A continuous session persists callbacks individually; QueryCapture
        # separately retains its smaller 10000-event collection limit below.
        if type(self.sequence) is not int or not 1 <= self.sequence <= 100_000:
            raise ValueError("broker callback sequence exceeds the bounded session")
        if self.channel not in {"TD", "MD"} or self.callback not in CALLBACK_FIELDS:
            raise ValueError("unsupported read-only CTP callback")
        if self.request_id is not None and (
            type(self.request_id) is not int or not 0 <= self.request_id < 2**31
        ):
            raise ValueError("broker callback request identity is invalid")
        if self.is_last is not None and type(self.is_last) is not bool:
            raise ValueError("broker callback completion must be explicit or unknown")
        if type(self.error_id) is not int or not -(2**31) <= self.error_id < 2**31:
            raise ValueError("broker callback error identity is invalid")
        _time(self.received_at)
        if self.data is not None:
            if not isinstance(self.data, dict):
                raise ValueError("broker callback fields must be a copied object")
            safe: dict[str, object] = {}
            for name in CALLBACK_FIELDS[self.callback]:
                if name not in self.data:
                    continue
                value = self.data[name]
                if value is None or type(value) is bool:
                    safe[name] = value
                elif isinstance(value, str) and len(value) <= 256 and "\x00" not in value:
                    safe[name] = value
                elif type(value) is int and -(2**63) <= value < 2**63:
                    safe[name] = value
                else:
                    raise ValueError("broker callback contains an unsupported field value")
            object.__setattr__(self, "data", safe)

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "channel": self.channel,
            "callback": self.callback,
            "request_id": self.request_id,
            "is_last": self.is_last,
            "received_at": self.received_at,
            "error_id": self.error_id,
            "data": None
            if self.data is None
            else {
                key: self.data[key] for key in CALLBACK_FIELDS[self.callback] if key in self.data
            },
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> BrokerEvent:
        if not isinstance(value, dict) or set(value) != {
            "sequence",
            "channel",
            "callback",
            "request_id",
            "is_last",
            "received_at",
            "error_id",
            "data",
        }:
            raise ValueError("broker callback must contain exactly the current fields")
        return cls(
            sequence=cast(int, value["sequence"]),
            channel=cast(str, value["channel"]),
            callback=cast(str, value["callback"]),
            request_id=cast(int | None, value["request_id"]),
            is_last=cast(bool | None, value["is_last"]),
            received_at=cast(str, value["received_at"]),
            error_id=cast(int, value["error_id"]),
            data=cast(dict[str, object] | None, value["data"]),
        )


@dataclass(frozen=True, slots=True)
class QueryCapture:
    """One finite capture, not a continuous market feed or external account ledger."""

    started_at: str
    finished_at: str
    binding_name: str | None
    binding_version: str | None
    trader_api_version: str | None
    market_api_version: str | None
    events: tuple[BrokerEvent, ...]
    failure_code: str | None = None

    def __post_init__(self) -> None:
        started, finished = _time(self.started_at), _time(self.finished_at)
        if not started <= finished <= started + timedelta(minutes=10):
            raise ValueError("broker capture must have a bounded, ordered time interval")
        for version in (
            self.binding_name,
            self.binding_version,
            self.trader_api_version,
            self.market_api_version,
        ):
            if version is not None and (
                not isinstance(version, str)
                or not version
                or len(version) > 128
                or any(ord(character) < 32 for character in version)
            ):
                raise ValueError(
                    "broker implementation identity must be bounded or explicitly unknown"
                )
        if self.failure_code is not None and (
            not isinstance(self.failure_code, str)
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", self.failure_code) is None
        ):
            raise ValueError(
                "broker failure must be a bounded code, never an exception or credentials"
            )
        if not isinstance(self.events, tuple) or len(self.events) > MAX_EVENTS:
            raise ValueError("broker capture exceeds the callback limit")
        for sequence, event in enumerate(self.events, 1):
            if not isinstance(event, BrokerEvent) or event.sequence != sequence:
                raise ValueError("broker capture must preserve contiguous local callback order")
            if not started <= _time(event.received_at) <= finished:
                raise ValueError("broker callback falls outside its declared capture interval")
        if len(_canonical(self.to_dict())) > MAX_CAPTURE_BYTES:
            raise ValueError("broker capture exceeds the evidence byte limit")

    def to_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "binding_name": self.binding_name,
            "binding_version": self.binding_version,
            "trader_api_version": self.trader_api_version,
            "market_api_version": self.market_api_version,
            "events": [event.to_dict() for event in self.events],
            "failure_code": self.failure_code,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> QueryCapture:
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "started_at",
                "finished_at",
                "binding_name",
                "binding_version",
                "trader_api_version",
                "market_api_version",
                "events",
                "failure_code",
            }
            or not isinstance(value.get("events"), list)
        ):
            raise ValueError("broker capture must contain exactly the current fields")
        return cls(
            started_at=cast(str, value["started_at"]),
            finished_at=cast(str, value["finished_at"]),
            binding_name=cast(str | None, value["binding_name"]),
            binding_version=cast(str | None, value["binding_version"]),
            trader_api_version=cast(str | None, value["trader_api_version"]),
            market_api_version=cast(str | None, value["market_api_version"]),
            events=tuple(
                BrokerEvent.from_dict(item)
                for item in cast(list[dict[str, object]], value["events"])
            ),
            failure_code=cast(str | None, value["failure_code"]),
        )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode()


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _time(value: str) -> datetime:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value) is None
    ):
        raise ValueError("broker timestamps must explicitly use UTC with a Z suffix")
    return datetime.fromisoformat(value).astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


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
    Column("batch_id", PGUUID(as_uuid=True), primary_key=True),
    Column("profile_name", String(32), nullable=False),
    Column("profile", JSONB, nullable=False),
    Column("account_id", String(12), nullable=False),
    Column("instrument", String(32), nullable=False),
    Column("query_scope", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("implementation_hash", String(64), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("binding_hash", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    Column("result", JSONB),
    Column("result_hash", String(64)),
    CheckConstraint("status IN ('PENDING', 'FAILED', 'INCOMPLETE', 'COMPLETE')"),
    CheckConstraint(
        "(status = 'PENDING' AND result IS NULL AND result_hash IS NULL) OR "
        "(status <> 'PENDING' AND result IS NOT NULL AND result_hash IS NOT NULL)"
    ),
)


def initialize_broker_records(connection: Connection) -> None:
    """Install the current read-only evidence table during atomic initialization."""

    if connection.dialect.name != "postgresql":
        raise ValueError("broker query evidence requires PostgreSQL")
    _metadata.create_all(connection)
    connection.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION broker_protect_query_evidence() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.status <> 'PENDING' THEN
                RAISE EXCEPTION 'Broker query evidence is immutable';
            END IF;
            IF (NEW.batch_id, NEW.profile_name, NEW.profile, NEW.account_id, NEW.instrument,
                NEW.query_scope, NEW.created_at, NEW.implementation_hash, NEW.request_hash,
                NEW.binding_hash)
                IS DISTINCT FROM
                (OLD.batch_id, OLD.profile_name, OLD.profile, OLD.account_id, OLD.instrument,
                OLD.query_scope, OLD.created_at, OLD.implementation_hash, OLD.request_hash,
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
        if engine.dialect.name != "postgresql":
            raise ValueError("broker query evidence requires PostgreSQL")
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
            "created_at": _timestamp(created_at),
            "implementation_hash": implementation_hash(),
        }
        request_hash = _hash(request)
        with self._engine.begin() as connection:
            connection.execute(
                insert(_batches)
                .values(
                    batch_id=request_id,
                    profile_name=profile["name"],
                    profile=profile,
                    account_id=account_id,
                    instrument=instrument,
                    query_scope=query_scope,
                    created_at=created_at,
                    implementation_hash=binding["implementation_hash"],
                    request_hash=request_hash,
                    binding_hash=_hash(binding),
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
        with self._engine.begin() as connection:
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
            if _time(capture.started_at) < cast(datetime, row["created_at"]):
                raise ValueError("broker capture predates its bound query request")
            if row["status"] != "PENDING":
                saved = _stored(dict(row))
                if saved["capture"] != capture.to_dict():
                    raise ValueError("broker query completion conflicts with saved evidence")
                return saved
            result = _result(binding, capture)
            result_hash = _hash({"binding": binding, "result": result})
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
        "created_at": _timestamp(cast(datetime, row["created_at"])),
        "implementation_hash": row["implementation_hash"],
    }
    request = {key: binding[key] for key in ("profile", "account_id", "instrument", "query_scope")}
    profile = binding["profile"]
    if (
        not isinstance(profile, dict)
        or profile.get("name") != row["profile_name"]
        or _hash(binding) != row["binding_hash"]
        or _hash(request) != row["request_hash"]
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
        or _hash({"binding": binding, "result": result}) != row["result_hash"]
    ):
        raise ValueError("saved broker query result no longer matches its evidence")
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
