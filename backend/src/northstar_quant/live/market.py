"""Coordinate accepted realtime samples and fixed strategy warmup, without account effects."""

from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from northstar_quant.broker.market import DAY, FRESH, SHANGHAI
from northstar_quant.broker.records import BrokerEvent
from northstar_quant.data_management.sampling import sample_market
from northstar_quant.factors.definition import Bar, Inputs
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.strategies.evaluation import step


def advance_market(
    state: dict[str, Any],
    event: BrokerEvent,
    *,
    instrument: str,
    contract_id: UUID,
    price_tick: Decimal,
    config: ResearchConfig,
    now: datetime,
) -> dict[str, Any]:
    strategy = config.strategy.to_dict()
    if state and state["binding"].get("strategy") != strategy:
        raise ValueError("live market checkpoint cannot change its fixed binding")
    sampling = deepcopy(state)
    sampling.pop("recent_closes", None)
    sampling.pop("intent", None)
    sampling.pop("strategy_state", None)
    sampling.pop("strategy_decision", None)
    if sampling:
        sampling["binding"].pop("strategy", None)
    result = sample_market(
        sampling,
        event,
        instrument=instrument,
        contract_id=contract_id,
        price_tick=price_tick,
        now=now,
    )
    result["binding"]["strategy"] = strategy
    reset = result["status"] == "HALTED" or result["reason"] in {
        "INITIAL_PARTIAL_MINUTE",
        "SESSION_BREAK_REWARM",
    }
    recent = [] if reset else deepcopy(state.get("recent_closes", []))
    strategy_state = () if reset else tuple(state.get("strategy_state", {}).items())
    intent = None
    completed = result["completed_bar"]
    decision = {"kind": "INPUT_UNAVAILABLE", "reason": "NO_COMPLETED_BAR", "factors": {}}
    if completed is not None:
        recent.append(dict(completed))
        recent = recent[-config.strategy.history_bars :]
        inputs = Inputs(
            tuple(
                Bar(
                    UUID(item["observation_id"]),
                    contract_id,
                    datetime.fromisoformat(item["completed_at"]),
                    datetime.fromisoformat(item["available_at"]),
                    Decimal(item["close"]),
                )
                for item in recent
            ),
            now,
            contract_id,
        )
        signal = step(config.strategy, inputs, strategy_state)
        strategy_state = signal.decision.state
        decision = {
            "kind": signal.decision.kind.value,
            "reason": signal.decision.reason,
            "factors": {alias: value.to_dict() for alias, value in signal.factors},
        }
        if signal.intent is not None:
            intent = {
                **signal.intent.to_dict(),
                "intent_id": signal.intent.intent_id,
                "factors": decision["factors"],
                "used_bars": deepcopy(recent),
            }
    result.update(
        recent_closes=recent,
        intent=intent,
        strategy_state=dict(strategy_state),
        strategy_decision=decision,
    )
    if result["reason"] in {"CLOCK_TOLERANCE_WAIT", "OBSERVING_MINUTE"}:
        ready = len(recent) == (config.strategy.history_bars - 1) + 1
        result.update(
            status="READY" if ready else "WARMING_UP",
            reason="CLOCK_TOLERANCE_WAIT"
            if result["pending_completed_bar"] is not None
            else "SHADOW_INTENT"
            if intent is not None
            else "OBSERVING_MINUTE"
            if ready
            else "WARMING_COMPLETED_MINUTES",
        )
    return result


def idle_reason(state: dict[str, Any] | None, *, now: datetime) -> str | None:
    """Classify absent SHFE DAY input without changing its verified checkpoint.

    No accepted quote means no inferred TradingDay or session status. A planned
    break/end requires the previous segment's final five seconds to have been
    observed on that same explicitly reported TradingDay; earlier gaps stay stale.
    This does not clear an existing halt, complete a bar or authorize execution.
    """
    if now.utcoffset() != timedelta(0):
        raise ValueError("live processing time must be explicit UTC")
    if not state or state.get("status") == "HALTED":
        return None
    quote, trading_day = state.get("last_quote"), state.get("trading_day")
    if (
        not isinstance(quote, dict)
        or not isinstance(trading_day, str)
        or quote.get("trading_day") != trading_day
    ):
        return None
    day = date.fromisoformat(trading_day)
    observed, received = _at(quote["event_time"]), _at(quote["received_at"])
    local_now = now.astimezone(SHANGHAI)
    segment = quote["segment"]
    end = datetime.combine(day, DAY[segment][1], SHANGHAI)
    if local_now.date() == day and timedelta(0) < end - observed <= FRESH:
        if segment == len(DAY) - 1 and local_now >= end:
            return "DAY_SESSION_ENDED"
        if segment < len(DAY) - 1:
            next_start = datetime.combine(day, DAY[segment + 1][0], SHANGHAI)
            if end <= local_now < next_start:
                return "SCHEDULED_BREAK"
    if now - observed > FRESH or now - received > FRESH:
        return "QUOTE_STALE"
    return None


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value)
