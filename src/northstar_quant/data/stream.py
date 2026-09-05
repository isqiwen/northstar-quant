"""Reconstruct one explicitly selected DAY window from retained CTP callbacks.

The JSON prefix is the input artifact, not a vendor wire response. Reconstruction
uses the recorded local receipt clock independently of historical shadow controls;
it neither reproduces nor writes any historical shadow decision.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from northstar_quant.broker.records import BrokerEvent
from northstar_quant.broker.settings import get_profile
from northstar_quant.data.live import advance_market
from northstar_quant.research import ResearchConfig


@dataclass(frozen=True)
class StreamMinutes:
    stream_id: UUID
    through_sequence: int
    binding: dict[str, Any]
    binding_hash: str
    session_open: datetime
    session_close: datetime
    bars: tuple[dict[str, Any], ...]

    def provenance(self) -> dict[str, object]:
        return {
            "stream_id": str(self.stream_id),
            "through_sequence": self.through_sequence,
            "binding_hash": self.binding_hash,
            "source_implementation_hash": self.binding["implementation_hash"],
            "price_basis": "OBSERVED_CTP_LAST_PRICE",
            "volume_basis": "CUMULATIVE_DELTA_AT_SNAPSHOT_TIME",
            "time_basis": "RECONSTRUCTED_FROM_LOCAL_RECEIPT_CLOCK",
            "bar_evidence": [
                {
                    key: bar[key]
                    for key in (
                        "observation_id",
                        "start_at",
                        "completed_at",
                        "available_at",
                        "first_sequence",
                        "last_sequence",
                        "confirmed_by_sequence",
                        "close_received_at",
                        "confirmation_received_at",
                        "confirmation_committed_at",
                    )
                }
                for bar in self.bars
            ],
        }


def reconstruct_stream(content: bytes, parameters: dict[str, object]) -> StreamMinutes:
    """Verify exact retained bytes and require every minute in the requested range.

    One prefix is at most 5 MiB. Date/range, hashes, sequence and identity errors
    fail closed. A preceding interruption cannot be repaired by skipping inputs;
    a requested range already completed before a later interruption remains usable.
    Contradictory account identity or fixed contract terms invalidate the prefix,
    including contradictions observed after the requested minutes were completed.
    No account callbacks or secrets are included in the returned provenance.
    """
    if not isinstance(content, bytes) or not 1 <= len(content) <= 5 * 1024 * 1024:
        raise ValueError("CTP callback prefix must be nonempty and at most 5 MiB")
    if set(parameters) != {"stream_id", "through_sequence", "session_open", "session_close"}:
        raise ValueError("CTP processing requires a fixed prefix and explicit minute range")
    try:
        document = json.loads(content)
        if (
            not isinstance(document, dict)
            or set(document)
            != {"kind", "stream_id", "through_sequence", "binding", "binding_hash", "events"}
            or document["kind"] != "COPIED_CTP_CALLBACK_PREFIX"
        ):
            raise ValueError("unsupported CTP callback artifact")
        identifier = UUID(document["stream_id"])
        through = document["through_sequence"]
        binding = document["binding"]
        if (
            str(identifier) != parameters["stream_id"]
            or through != parameters["through_sequence"]
            or type(through) is not int
            or not 1 <= through <= 100000
            or not isinstance(binding, dict)
            or digest(binding) != document["binding_hash"]
        ):
            raise ValueError("CTP prefix identity or binding differs")
        profile = binding["profile"]
        if profile != get_profile(profile["name"]).identity():
            raise ValueError("CTP prefix must retain an approved SimNow profile")
        if (
            binding["terms"]["ExchangeID"] != "SHFE"
            or binding["terms"]["ProductClass"] != "1"
            or binding["terms"]["InstrumentID"] != binding["instrument"]
        ):
            raise ValueError("CTP market publication supports only confirmed SHFE futures")
        if binding["request"]["allow_retention"] is not True:
            raise ValueError("CTP prefix has no fixed retention permission")
        opened, closed = _utc(parameters["session_open"]), _utc(parameters["session_close"])
        local_open, local_close = (
            opened.astimezone(ZoneInfo("Asia/Shanghai")),
            closed.astimezone(ZoneInfo("Asia/Shanghai")),
        )
        if (
            opened >= closed
            or opened.second
            or opened.microsecond
            or closed.second
            or closed.microsecond
            or local_open.date() != local_close.date()
            or not any(
                start <= local_open.time() < local_close.time() <= end
                for start, end in (
                    (time(9), time(10, 15)),
                    (time(10, 30), time(11, 30)),
                    (time(13, 30), time(15)),
                )
            )
        ):
            raise ValueError("CTP publication requires one explicit minute-aligned SHFE DAY range")
        events = document["events"]
        if not isinstance(events, list) or len(events) != through:
            raise ValueError("CTP prefix must retain every callback through its fixed sequence")
        state: dict[str, Any] = {}
        bars: list[dict[str, Any]] = []
        receipts: dict[int, tuple[str, str]] = {}
        td_day = md_day = None
        last_received: datetime | None = None
        invalid = False
        for sequence, item in enumerate(events, 1):
            if not isinstance(item, dict) or set(item) != {"event", "event_hash", "committed_at"}:
                raise ValueError("CTP prefix callback evidence is incomplete")
            event = BrokerEvent.from_dict(item["event"])
            if (
                event.sequence != sequence
                or event.to_dict() != item["event"]
                or digest(item["event"]) != item["event_hash"]
            ):
                raise ValueError("CTP prefix callback sequence or hash differs")
            received, committed = _utc(event.received_at), _utc(item["committed_at"])
            if last_received is not None and received < last_received:
                raise ValueError("CTP prefix receipt clock regressed")
            last_received = received
            receipts[sequence] = (event.received_at, committed.isoformat().replace("+00:00", "Z"))
            data = event.data or {}
            _verify_fixed_identity(event, binding)
            if event.error_id or event.callback in {"OnFrontDisconnected", "OnHeartBeatWarning"}:
                invalid = True
            if event.callback == "OnRspUserLogin":
                if not event.is_last or event.error_id:
                    invalid = True
                if event.channel == "TD":
                    td_day = data.get("TradingDay")
                else:
                    md_day = data.get("TradingDay")
            if event.callback != "OnRtnDepthMarketData" or invalid:
                continue
            if not td_day or td_day != md_day or td_day != data.get("TradingDay"):
                invalid = True
                continue
            state = advance_market(
                state,
                event,
                instrument=binding["instrument"],
                contract_id=UUID(binding["contract_id"]),
                price_tick=Decimal(binding["terms"]["PriceTick"]),
                config=ResearchConfig(),
                now=received,
            )
            if state["status"] == "HALTED":
                invalid = True
            bar = state["completed_bar"]
            if bar is not None and opened <= _utc(bar["start_at"]) < closed:
                if bar["trading_day"] != local_open.strftime("%Y%m%d"):
                    raise ValueError("CTP observed TradingDay differs from the requested range")
                confirmation = receipts[bar["confirmed_by_sequence"]]
                bar.update(
                    confirmation_received_at=confirmation[0],
                    confirmation_committed_at=confirmation[1],
                )
                bars.append(bar)
        expected = [
            opened + timedelta(minutes=offset)
            for offset in range(int((closed - opened).total_seconds()) // 60)
        ]
        if [_utc(bar["start_at"]) for bar in bars] != expected:
            raise ValueError(
                "CTP requested range has missing, partial or unconfirmed minutes; no gap filling"
            )
        return StreamMinutes(
            identifier, through, binding, document["binding_hash"], opened, closed, tuple(bars)
        )
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("CTP callback prefix is malformed or missing required evidence") from error


def _verify_fixed_identity(event: BrokerEvent, binding: dict[str, Any]) -> None:
    data, profile = event.data or {}, binding["profile"]
    if event.callback == "CaptureStarted" and data != {
        "profile_name": profile["name"],
        "td_front": profile["td_front"],
        "md_front": profile["md_front"],
        "broker_id": profile["broker_id"],
        "account_id": binding["account_id"],
        "instrument": binding["instrument"],
    }:
        raise ValueError("CTP callback capture context differs from its fixed binding")
    if event.callback in {"OnRspAuthenticate", "OnRspUserLogin"} and not event.error_id:
        if event.channel == "MD":
            matches = data.get("BrokerID") in (None, "", profile["broker_id"]) and data.get(
                "UserID"
            ) in (None, "", binding["account_id"])
        else:
            matches = (data.get("BrokerID"), data.get("UserID")) == (
                profile["broker_id"],
                binding["account_id"],
            )
        if not matches:
            raise ValueError("CTP callback account identity differs from its fixed binding")
    account_field = {
        "OnRspQryTradingAccount": "AccountID",
        "OnRspQryInvestorPosition": "InvestorID",
        "OnRspQryOrder": "InvestorID",
        "OnRtnOrder": "InvestorID",
        "OnRspQryTrade": "InvestorID",
        "OnRtnTrade": "InvestorID",
    }.get(event.callback)
    if (
        data
        and account_field is not None
        and (
            data.get("BrokerID") != profile["broker_id"]
            or data.get(account_field) != binding["account_id"]
        )
    ):
        raise ValueError("CTP account callback identity differs from its fixed binding")
    if (
        event.callback == "OnRspQryInstrument"
        and data.get("InstrumentID") == binding["instrument"]
        and any(
            data.get(key) != binding["terms"].get(key)
            for key in (
                "ExchangeID",
                "ProductClass",
                "ProductID",
                "PriceTick",
                "VolumeMultiple",
                "DeliveryYear",
                "DeliveryMonth",
                "ExpireDate",
            )
        )
    ):
        raise ValueError("CTP callback contract terms differ from its fixed binding")


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("CTP artifact time must be explicit UTC text")
    at = datetime.fromisoformat(value)
    if at.utcoffset() != timedelta(0):
        raise ValueError("CTP artifact time must be explicit UTC")
    return at.astimezone(UTC)
