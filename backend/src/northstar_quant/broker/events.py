"""Copied CTP events and bounded captures, independent of persistence and SDK objects."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

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
        "CloseProfit PositionProfit Balance Available WithdrawQuota Reserve BizType".split()
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
        "ExchangeID InvestUnitID".split()
    ),
    "OnRspQryInstrumentCommissionRate": tuple(
        "InstrumentID InvestorRange BrokerID InvestorID OpenRatioByMoney OpenRatioByVolume "
        "CloseRatioByMoney CloseRatioByVolume CloseTodayRatioByMoney CloseTodayRatioByVolume "
        "ExchangeID BizType InvestUnitID".split()
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
        parse_time(self.received_at)
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
        started, finished = parse_time(self.started_at), parse_time(self.finished_at)
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
            if not started <= parse_time(event.received_at) <= finished:
                raise ValueError("broker callback falls outside its declared capture interval")
        if len(canonical_bytes(self.to_dict())) > MAX_CAPTURE_BYTES:
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


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode()


def capture_hash(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def parse_time(value: str) -> datetime:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value) is None
    ):
        raise ValueError("broker timestamps must explicitly use UTC with a Z suffix")
    return datetime.fromisoformat(value).astimezone(UTC)


def timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
