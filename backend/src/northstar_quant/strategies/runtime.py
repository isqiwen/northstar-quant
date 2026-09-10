"""Bounded strategy lifecycle shared by historical and broker-driven sessions."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from northstar_quant.factors.definition import Bar, Inputs
from northstar_quant.market_data import MarketBar

from .configuration import StrategyConfig
from .definition import Step
from .evaluation import step
from .registry import resolve


class StrategyRuntime:
    """Own warmup and strategy state, with no account or execution authority.

    The candidate input window includes the current bar before evaluation. Only
    successful evaluation commits state. Checkpoint loading validates bounded
    accepted history without executing strategies or reconstructing an account.
    """

    def __init__(
        self,
        config: StrategyConfig,
        contract_id: UUID,
        interval_seconds: int = 60,
        *,
        source_scope: str = "",
        history: tuple[MarketBar, ...] = (),
        state: tuple[tuple[str, str | int], ...] = (),
    ) -> None:
        if (
            not isinstance(contract_id, UUID)
            or type(interval_seconds) is not int
            or interval_seconds <= 0
        ):
            raise ValueError("strategy runtime requires a canonical contract and interval")
        self.config = config
        self.contract_id = contract_id
        self.interval_seconds = interval_seconds
        self.source_scope = source_scope
        self._history: tuple[MarketBar, ...] = ()
        self._state = state
        resolve(config.strategy_id).validate_state(state)
        if len(history) > config.history_bars:
            raise ValueError("strategy checkpoint exceeds its bounded warmup")
        for bar in history:
            if not self.accepts(bar):
                raise ValueError("strategy checkpoint repeats an observation")
            self._history += (bar,)

    @property
    def history(self) -> tuple[MarketBar, ...]:
        return self._history

    @property
    def state(self) -> tuple[tuple[str, str | int], ...]:
        return self._state

    def accepts(self, bar: MarketBar) -> bool:
        bar.validate(interval_seconds=self.interval_seconds)
        for previous in self._history:
            if bar.observation_id == previous.observation_id:
                if previous != bar:
                    raise ValueError("observation identity was reused with different facts")
                return False
        if self._history and (
            bar.event_time <= self._history[-1].event_time
            or bar.available_at < self._history[-1].available_at
        ):
            raise ValueError("strategy runtime rejects late or revised bars")
        return True

    def advance(self, bar: MarketBar, *, at: datetime | None = None) -> Step | None:
        if not self.accepts(bar):
            return None
        history = (*self._history, bar)[-self.config.history_bars :]
        observed_at = bar.available_at if at is None else at
        if not isinstance(observed_at, datetime) or observed_at.utcoffset() != timedelta(0):
            raise ValueError("strategy clock must be aware UTC")
        if observed_at < bar.available_at:
            raise ValueError("strategy clock precedes input availability")
        result = step(
            self.config,
            Inputs(
                tuple(
                    Bar(
                        item.observation_id,
                        self.contract_id,
                        item.completed_at,
                        item.available_at,
                        item.close,
                    )
                    for item in history
                ),
                observed_at,
                self.contract_id,
                self.interval_seconds,
                source_scope=self.source_scope,
            ),
            self._state,
        )
        self._history, self._state = history, result.decision.state
        return result
