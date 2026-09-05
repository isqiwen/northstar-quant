"""Pure replay of explicitly synthetic receipts, never external market acceptance."""

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from test_live import CONTRACT, OPEN, tick

from northstar_quant.broker.records import BrokerEvent
from northstar_quant.broker.settings import get_profile
from northstar_quant.data.stream import reconstruct_stream

STREAM = UUID("20000000-0000-0000-0000-000000000001")


def encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def digest(value: object) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def prefix() -> tuple[dict[str, Any], dict[str, object]]:
    binding = {
        "profile": get_profile("simnow_dev").identity(),
        "account_id": "123456",
        "instrument": "rb2610",
        "contract_id": str(CONTRACT),
        "implementation_hash": "0" * 64,
        "terms": {
            "InstrumentID": "rb2610",
            "ExchangeID": "SHFE",
            "ProductClass": "1",
            "ProductID": "rb",
            "PriceTick": "1",
            "VolumeMultiple": 10,
            "DeliveryYear": 2026,
            "DeliveryMonth": 10,
            "ExpireDate": "20261015",
        },
        "request": {"allow_retention": True, "use_basis": "Synthetic engineering only"},
    }
    callbacks = [
        BrokerEvent(
            index,
            channel,
            "OnRspUserLogin",
            index,
            True,
            "2026-09-07T01:00:00Z",
            0,
            {"TradingDay": "20260907", "BrokerID": "9999", "UserID": "123456"},
        )
        for index, channel in enumerate(("TD", "MD"), 1)
    ]
    callbacks.extend(
        tick(
            sequence,
            OPEN + timedelta(seconds=seconds),
            price="3100" if seconds < 120 else "3110",
            volume=100 + sequence,
        )
        for sequence, seconds in enumerate(range(0, 181, 5), 3)
    )
    document = {
        "kind": "COPIED_CTP_CALLBACK_PREFIX",
        "stream_id": str(STREAM),
        "through_sequence": len(callbacks),
        "binding": binding,
        "binding_hash": digest(binding),
        "events": [
            {
                "event": event.to_dict(),
                "event_hash": digest(event.to_dict()),
                "committed_at": (
                    datetime.fromisoformat(event.received_at) + timedelta(milliseconds=250)
                )
                .isoformat()
                .replace("+00:00", "Z"),
            }
            for event in callbacks
        ],
    }
    parameters = {
        "stream_id": str(STREAM),
        "through_sequence": len(callbacks),
        "session_open": "2026-09-07T01:01:00Z",
        "session_close": "2026-09-07T01:03:00Z",
    }
    return document, parameters


def test_saved_receipt_prefix_reconstructs_explicit_minutes_with_exact_local_timing() -> None:
    document, parameters = prefix()
    original = deepcopy(document)
    result = reconstruct_stream(encoded(document), parameters)
    assert document == original
    assert [bar["close"] for bar in result.bars] == ["3100", "3110"]
    assert [bar["volume"] for bar in result.bars] == [12, 12]
    assert result.bars[0]["available_at"] == "2026-09-07T01:02:00.100000Z"
    provenance = result.provenance()
    evidence = provenance["bar_evidence"]
    assert evidence[0]["confirmation_received_at"] == "2026-09-07T01:02:00.100000Z"
    assert evidence[0]["confirmation_committed_at"] == "2026-09-07T01:02:00.350000Z"
    assert provenance["time_basis"] == "RECONSTRUCTED_FROM_LOCAL_RECEIPT_CLOCK"
    assert "account_id" not in provenance and "intent" not in result.bars[0]
    assert reconstruct_stream(encoded(document), parameters) == result


@pytest.mark.parametrize("change", ["partial", "tail", "break", "gap", "hash", "sequence"])
def test_unconfirmed_range_or_damaged_source_never_becomes_a_published_minute(change: str) -> None:
    document, parameters = prefix()
    if change == "partial":
        parameters["session_open"] = "2026-09-07T01:00:00Z"
    elif change == "tail":
        parameters["session_close"] = "2026-09-07T01:04:00Z"
    elif change == "break":
        parameters.update(session_open="2026-09-07T02:14:00Z", session_close="2026-09-07T02:31:00Z")
    elif change == "gap":
        changed = document["events"][20]
        changed["event"]["data"]["Volume"] = 1
        changed["event_hash"] = digest(changed["event"])
    elif change == "hash":
        document["events"][-1]["event_hash"] = "0" * 64
    else:
        document["events"].pop(10)
    with pytest.raises(ValueError):
        reconstruct_stream(encoded(document), parameters)


def test_later_disconnect_does_not_erase_previously_confirmed_requested_minutes() -> None:
    document, parameters = prefix()
    final = BrokerEvent(40, "MD", "OnFrontDisconnected", None, None, "2026-09-07T01:03:01Z", 0, {})
    document["events"].append(
        {
            "event": final.to_dict(),
            "event_hash": digest(final.to_dict()),
            "committed_at": "2026-09-07T01:03:01.250000Z",
        }
    )
    document["through_sequence"] = parameters["through_sequence"] = 40
    assert len(reconstruct_stream(encoded(document), parameters).bars) == 2


@pytest.mark.parametrize("after_minutes", [False, True])
@pytest.mark.parametrize(
    "conflict", ["terms", "td_login", "md_login", "account_callback", "context"]
)
def test_contradictory_identity_or_terms_anywhere_in_prefix_prevents_publication(
    after_minutes: bool, conflict: str
) -> None:
    document, parameters = prefix()
    at = "2026-09-07T01:03:01Z" if after_minutes else "2026-09-07T01:00:00Z"
    data: dict[str, object]
    channel = "TD"
    if conflict == "terms":
        callback = "OnRspQryInstrument"
        data = {**document["binding"]["terms"], "PriceTick": "10"}
    elif conflict in {"td_login", "md_login"}:
        callback = "OnRspUserLogin"
        channel = "MD" if conflict == "md_login" else "TD"
        data = {"BrokerID": "9999", "UserID": "654321", "TradingDay": "20260907"}
    elif conflict == "account_callback":
        callback = "OnRtnTrade"
        data = {"BrokerID": "9999", "InvestorID": "654321"}
    else:
        callback = "CaptureStarted"
        profile = document["binding"]["profile"]
        data = {
            "profile_name": profile["name"],
            "td_front": profile["td_front"],
            "md_front": profile["md_front"],
            "broker_id": "9999",
            "account_id": "654321",
            "instrument": "rb2610",
        }
    event = BrokerEvent(1, channel, callback, 1, True, at, 0, data)
    item = {"event": event.to_dict(), "event_hash": "", "committed_at": at}
    document["events"].insert(len(document["events"]) if after_minutes else 0, item)
    for sequence, entry in enumerate(document["events"], 1):
        entry["event"]["sequence"] = sequence
        entry["event_hash"] = digest(entry["event"])
    document["through_sequence"] = parameters["through_sequence"] = len(document["events"])
    with pytest.raises(
        ValueError, match="differs from its fixed binding|differ from its fixed binding"
    ):
        reconstruct_stream(encoded(document), parameters)
