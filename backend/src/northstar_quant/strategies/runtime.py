"""Bounded strategy lifecycle shared by historical and broker-driven sessions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from northstar_quant.factors.definition import Bar, Inputs
from northstar_quant.market_data import MarketBar
from northstar_quant.market_data.window import MarketWindow

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
        self._market = MarketWindow(config.history_bars, interval_seconds, history)
        self._state = state
        resolve(config.strategy_id).validate_state(state)

    @property
    def history(self) -> tuple[MarketBar, ...]:
        return self._market.bars

    @property
    def state(self) -> tuple[tuple[str, str | int], ...]:
        return self._state

    def accepts(self, bar: MarketBar) -> bool:
        return self._market.accepts(bar)

    def advance(self, bar: MarketBar, *, at: datetime | None = None) -> Step | None:
        if not self.accepts(bar):
            return None
        observed_at = bar.available_at if at is None else at
        market = self._market.append(bar, at=observed_at)
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
                    for item in market.bars
                ),
                observed_at,
                self.contract_id,
                self.interval_seconds,
                source_scope=self.source_scope,
            ),
            self._state,
        )
        self._market, self._state = market, result.decision.state
        return result
