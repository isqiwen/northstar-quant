"""Synthetic CTP snapshots exercise shadow causality, not broker acceptance."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from northstar_quant.broker.records import BrokerEvent
from northstar_quant.data.live import advance_market
from northstar_quant.research import ResearchConfig

CONTRACT = UUID("10000000-0000-0000-0000-000000000001")
CONFIG = ResearchConfig(threshold=Decimal("0.001"))
OPEN = datetime(2026, 9, 7, 1, 0, tzinfo=UTC)


def tick(sequence: int, at: datetime, *, price: str = "3100", volume: int = 100) -> BrokerEvent:
    local = at.astimezone(ZoneInfo("Asia/Shanghai"))
    return BrokerEvent(
        sequence,
        "MD",
        "OnRtnDepthMarketData",
        None,
        None,
        (at + timedelta(milliseconds=100)).isoformat().replace("+00:00", "Z"),
        0,
        {
            "InstrumentID": "rb2610",
            "ExchangeID": "SHFE",
            "TradingDay": local.strftime("%Y%m%d"),
            "ActionDay": local.strftime("%Y%m%d"),
            "UpdateTime": local.strftime("%H:%M:%S"),
            "UpdateMillisec": local.microsecond // 1000,
            "LastPrice": price,
            "Volume": volume,
        },
    )


def advance(state: dict[str, Any], event: BrokerEvent, **changes: Any) -> dict[str, Any]:
    parameters = {
        "instrument": "rb2610",
        "contract_id": CONTRACT,
        "price_tick": Decimal("1"),
        "config": CONFIG,
        "now": datetime.fromisoformat(event.received_at),
        **changes,
    }
    return advance_market(state, event, **parameters)


def test_only_confirmed_observed_minutes_generate_bounded_shadow_intents() -> None:
    state: dict[str, Any] = {}
    completed = []
    for index, seconds in enumerate(range(0, 181, 5), 1):
        event = tick(
            index,
            OPEN + timedelta(seconds=seconds),
            price="3100" if seconds < 120 else "3110" if seconds < 180 else "3200",
            volume=100 + 2 * index,
        )
        before = state
        state = advance(state, event)
        assert before is not state
        if state["completed_bar"]:
            completed.append(state["completed_bar"])
        if seconds < 180:
            assert state["intent"] is None
    assert len(completed) == 2  # The initial observed minute is partial, never warmed from.
    assert [bar["close"] for bar in completed] == ["3100", "3110"]
    assert completed[-1]["volume"] == 24
    assert completed[-1]["first_sequence"] == 25
    assert completed[-1]["last_sequence"] == 36
    assert completed[-1]["confirmed_by_sequence"] == 37
    assert completed[-1]["close_received_at"] == "2026-09-07T01:02:55.100000Z"
    assert state["status"] == "READY"
    intent = state["intent"]
    assert intent["target_fraction"] == "0.5"
    assert intent["contract_id"] == str(CONTRACT)
    assert intent["generated_at"] == "2026-09-07T01:03:00.100000Z"
    assert [bar["observation_id"] for bar in intent["used_bars"]] == [
        bar["observation_id"] for bar in completed
    ]
    assert len(state["recent_closes"]) == CONFIG.lookback + 1
    assert "fill" not in state and "risk_decision" not in state


def test_same_time_volume_advances_but_duplicate_and_unordered_revision_cannot() -> None:
    first = tick(1, OPEN + timedelta(seconds=1))
    state = advance({}, first)
    original = json.loads(json.dumps(state))
    duplicate = advance(state, replace(first, sequence=2))
    assert state == original
    assert duplicate["quality"] == "DUPLICATE" and duplicate["intent"] is None
    assert duplicate["current_minute"] == state["current_minute"]
    next_trade = tick(3, OPEN + timedelta(seconds=1), volume=102, price="3101")
    advanced = advance(duplicate, next_trade)
    assert advanced["status"] == "WARMING_UP"
    assert advanced["current_minute"]["close"] == "3101"
    revision = tick(4, OPEN + timedelta(seconds=1), volume=102, price="3102")
    halted = advance(advanced, revision)
    assert halted["status"] == "HALTED"
    assert halted["reason"] == "AMBIGUOUS_SAME_TIME_QUOTE"
    later = advance(halted, tick(5, OPEN + timedelta(seconds=2), volume=104))
    assert later["status"] == "HALTED" and later["recent_closes"] == []
    assert later["completed_bar"] is None and later["intent"] is None


@pytest.mark.parametrize(
    ("changes", "offset", "reason"),
    [
        ({"Volume": -1}, 2, "INVALID_CUMULATIVE_VOLUME"),
        ({"Volume": 99}, 2, "CUMULATIVE_VOLUME_DECREASED"),
        ({"LastPrice": "3100.5"}, 2, "PRICE_OFF_TICK"),
        ({"LastPrice": "1.7976931348623157e308"}, 2, "INVALID_PRICE"),
        ({"LastPrice": "3101", "Volume": 100}, 2, "PRICE_CHANGED_WITHOUT_VOLUME"),
        ({"ActionDay": ""}, 2, "SOURCE_DATES_NOT_CONFIRMED"),
        ({"TradingDay": "20260908"}, 2, "SOURCE_DATES_NOT_CONFIRMED"),
        ({"InstrumentID": "rb2701"}, 2, "INSTRUMENT_MISMATCH"),
        ({"ExchangeID": "DCE"}, 2, "EXCHANGE_MISMATCH"),
        ({}, 0, "LATE_OR_REVISED_QUOTE"),
        ({}, 7, "SOURCE_GAP"),
        ({}, 120, "SOURCE_GAP"),
    ],
)
def test_unusable_source_facts_halt_without_publishing_a_bar(
    changes: dict[str, object], offset: int, reason: str
) -> None:
    state = advance({}, tick(1, OPEN + timedelta(seconds=1)))
    incoming = tick(2, OPEN + timedelta(seconds=offset), volume=102)
    incoming = replace(
        incoming,
        received_at=(OPEN + timedelta(seconds=max(offset, 2), milliseconds=100))
        .isoformat()
        .replace("+00:00", "Z"),
        data={**(incoming.data or {}), **changes},
    )
    result = advance(state, incoming)
    assert result["status"] == "HALTED" and result["reason"] == reason
    assert result["completed_bar"] is None and result["intent"] is None
    assert result["recent_closes"] == []


def test_stale_or_future_quotes_never_warm_strategy() -> None:
    event = tick(1, OPEN)
    stale = advance({}, event, now=OPEN + timedelta(seconds=6))
    assert stale["reason"] == "STALE_QUOTE"
    future_receive = advance({}, event, now=OPEN - timedelta(seconds=2))
    assert future_receive["reason"] == "RECEIVE_TIME_IN_FUTURE"
    future_source = advance(
        {},
        replace(
            event, received_at=(OPEN - timedelta(seconds=2)).isoformat().replace("+00:00", "Z")
        ),
    )
    assert future_source["reason"] == "SOURCE_TIME_IN_FUTURE"
    for result in (stale, future_receive, future_source):
        assert result["status"] == "HALTED" and result["recent_closes"] == []


def test_one_second_clock_tolerance_never_completes_a_minute_early() -> None:
    state: dict[str, Any] = {}
    for sequence, seconds in enumerate(range(0, 120, 5), 1):
        state = advance(
            state, tick(sequence, OPEN + timedelta(seconds=seconds), volume=100 + sequence)
        )
    early = tick(25, OPEN + timedelta(seconds=120), volume=125)
    early = replace(early, received_at="2026-09-07T01:01:59.500000Z")
    state = advance(state, early)
    assert state["completed_bar"] is None and state["intent"] is None
    assert state["reason"] == "CLOCK_TOLERANCE_WAIT"
    confirmed = advance(state, tick(26, OPEN + timedelta(seconds=121), volume=126))
    assert confirmed["completed_bar"]["completed_at"] == "2026-09-07T01:02:00Z"
    assert confirmed["completed_bar"]["confirmed_by_sequence"] == 25
    assert confirmed["completed_bar"]["available_at"] == "2026-09-07T01:02:00Z"
    assert confirmed["pending_completed_bar"] is None


def test_scheduled_break_discards_tail_and_restarts_warmup_but_cannot_hide_a_gap() -> None:
    start = OPEN + timedelta(hours=1, minutes=11)
    state: dict[str, Any] = {}
    for sequence, seconds in enumerate(range(0, 240, 5), 1):
        state = advance(
            state, tick(sequence, start + timedelta(seconds=seconds), volume=100 + sequence)
        )
    assert state["status"] == "READY"
    resumed = advance(state, tick(49, OPEN + timedelta(hours=1, minutes=30), volume=150))
    assert resumed["reason"] == "SESSION_BREAK_REWARM"
    assert resumed["current_minute"]["partial"]
    assert resumed["recent_closes"] == [] and resumed["completed_bar"] is None
    lost = advance({}, tick(1, OPEN + timedelta(hours=1), volume=100))
    after_gap = advance(lost, tick(2, OPEN + timedelta(hours=1, minutes=30), volume=150))
    assert after_gap["status"] == "HALTED" and after_gap["reason"] == "SOURCE_GAP"


def test_session_end_disconnect_and_fixed_binding_cannot_publish_or_rearm() -> None:
    at = OPEN + timedelta(hours=5, minutes=59, seconds=59)
    state = advance({}, tick(1, at))
    final = advance(state, tick(2, at + timedelta(seconds=1)))
    assert final["reason"] == "OUTSIDE_SHFE_DAY" and final["completed_bar"] is None
    disconnected = BrokerEvent(
        2, "MD", "OnFrontDisconnected", None, None, state["last_quote"]["received_at"], 0, {}
    )
    stopped = advance(state, disconnected)
    assert stopped["reason"] == "DISCONNECTED" and stopped["current_minute"] is None
    with pytest.raises(ValueError, match="fixed binding"):
        advance(state, tick(2, at), config=ResearchConfig(lookback=2))
    restored = json.loads(json.dumps(state))
    assert advance(restored, disconnected) == stopped


def test_longer_warmup_stays_bounded_and_retry_does_not_repeat_last_signal() -> None:
    config = ResearchConfig(lookback=2, threshold=Decimal("0.001"))
    state: dict[str, Any] = {}
    emitted = []
    for sequence, seconds in enumerate(range(0, 361, 5), 1):
        event = tick(
            sequence,
            OPEN + timedelta(seconds=seconds),
            price=str(3100 + 10 * (seconds // 60)),
            volume=100 + sequence,
        )
        previous = json.loads(json.dumps(state))
        state = advance(state, event, config=config)
        assert advance(previous, event, config=config) == state
        if state["intent"]:
            emitted.append(state["intent"])
        assert len(state["recent_closes"]) <= 3
    assert len(emitted) == 3
    assert [bar["close"] for bar in emitted[-1]["used_bars"]] == ["3130", "3140", "3150"]
    duplicate = advance(state, event, config=config)
    assert duplicate["quality"] == "DUPLICATE"
    assert duplicate["intent"] is None and duplicate["completed_bar"] is None
    assert duplicate["recent_closes"] == state["recent_closes"]


def test_explicit_next_day_is_not_silently_rebound_to_existing_session() -> None:
    state = advance({}, tick(1, OPEN))
    changed = advance(state, tick(2, OPEN + timedelta(days=1), volume=101))
    assert changed["status"] == "HALTED" and changed["reason"] == "TRADING_DAY_CHANGED"
    assert changed["trading_day"] == "20260907"


@pytest.mark.parametrize(
    ("quote_offset", "now_offset", "reason"),
    [
        (0, 5, None),
        (0, 6, "QUOTE_STALE"),
        (75 * 60 - 5, 75 * 60, "SCHEDULED_BREAK"),
        (75 * 60 - 1, 90 * 60 - 1, "SCHEDULED_BREAK"),
        (75 * 60 - 6, 80 * 60, "QUOTE_STALE"),
        (75 * 60 - 1, 90 * 60, "QUOTE_STALE"),
        (150 * 60 - 5, 150 * 60, "SCHEDULED_BREAK"),
        (150 * 60 - 1, 270 * 60 - 1, "SCHEDULED_BREAK"),
        (150 * 60 - 6, 200 * 60, "QUOTE_STALE"),
        (150 * 60 - 1, 270 * 60, "QUOTE_STALE"),
        (360 * 60 - 5, 360 * 60, "DAY_SESSION_ENDED"),
        (360 * 60 - 1, 12 * 3600, "DAY_SESSION_ENDED"),
        (360 * 60 - 6, 360 * 60, "QUOTE_STALE"),
        (0, 360 * 60, "QUOTE_STALE"),
        (75 * 60 - 1, 150 * 60, "QUOTE_STALE"),
        (75 * 60 - 1, 24 * 3600 + 80 * 60, "QUOTE_STALE"),
        (360 * 60 - 1, 24 * 3600 + 360 * 60, "QUOTE_STALE"),
    ],
)
def test_idle_input_distinguishes_observed_session_tails_from_missing_quotes(
    quote_offset: int, now_offset: int, reason: str | None
) -> None:
    from northstar_quant.data.live import idle_reason

    state = advance({}, tick(1, OPEN + timedelta(seconds=quote_offset)))
    original = json.loads(json.dumps(state))
    assert idle_reason(state, now=OPEN + timedelta(seconds=now_offset)) == reason
    assert state == original


def test_idle_input_never_invents_trading_day_or_clears_known_failure() -> None:
    from northstar_quant.data.live import idle_reason

    now = OPEN + timedelta(hours=7)
    assert idle_reason(None, now=now) is None
    assert idle_reason({}, now=now) is None
    unknown = advance({}, replace(tick(1, OPEN), data={}))
    assert unknown["last_quote"] is None and unknown["trading_day"] is None
    assert idle_reason(unknown, now=now) is None
    state = advance({}, tick(1, OPEN + timedelta(hours=6, seconds=-1)))
    assert idle_reason({**state, "trading_day": None}, now=now) is None
    disconnected = BrokerEvent(
        2, "MD", "OnFrontDisconnected", None, None, state["last_quote"]["received_at"], 0, {}
    )
    halted = advance(state, disconnected)
    assert idle_reason(halted, now=now) is None
    assert halted["reason"] == "DISCONNECTED"
    with pytest.raises(ValueError, match="explicit UTC"):
        idle_reason(state, now=now.replace(tzinfo=None))
