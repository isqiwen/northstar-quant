"""Fixed strategy instances share causal subscriptions and commit each ingress together."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from threading import get_ident

from northstar_quant.factors.definition import Bar, Inputs, content_id
from northstar_quant.market_data import MarketBar
from northstar_quant.market_data.engine import BarStream, DataEngine

from .configuration import StrategyConfig
from .definition import Step
from .evaluation import step
from .registry import resolve


@dataclass(frozen=True, slots=True)
class StrategyBinding:
    instance_id: str
    config: StrategyConfig
    stream: BarStream

    def __post_init__(self) -> None:
        if (
            not isinstance(self.instance_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", self.instance_id) is None
            or not isinstance(self.config, StrategyConfig)
            or not isinstance(self.stream, BarStream)
        ):
            raise ValueError(
                "strategy instance requires a fixed identity, configuration and stream"
            )

    @property
    def binding_id(self) -> str:
        return content_id(
            dict(
                instance_id=self.instance_id,
                config=self.config.to_dict(),
                contract_id=str(self.stream.contract_id),
                interval_seconds=self.stream.interval_seconds,
                source_scope=self.stream.source_scope,
            )
        )


class Trader:
    """One core-thread owner for strategy state, with a shared DataEngine.

    Stable instance order makes fanout reproducible. An exception before commit
    leaves every strategy and stream unchanged; a caller's wider account transaction
    can stage this owner with a shallow copy. There are no account mutations here.
    """

    def __init__(
        self,
        bindings: tuple[StrategyBinding, ...],
        *,
        history: Mapping[BarStream, tuple[MarketBar, ...]] | None = None,
        states: Mapping[str, tuple[tuple[str, str | int], ...]] | None = None,
    ) -> None:
        if not bindings or any(not isinstance(b, StrategyBinding) for b in bindings):
            raise ValueError("Trader requires fixed strategy instances")
        if len({b.instance_id for b in bindings}) != len(bindings):
            raise ValueError("strategy instance identity is duplicated")
        self._bindings = tuple(sorted(bindings, key=lambda b: b.instance_id))
        self._binding_ids = {b.instance_id: b.binding_id for b in self._bindings}
        self._owner = (os.getpid(), get_ident())
        self._closed = False
        self._advancing = False
        histories, state = dict(history or {}), dict(states or {})
        if set(state) - {b.instance_id for b in bindings} or set(histories) - {
            b.stream for b in bindings
        }:
            raise ValueError("restored strategy state or history has no fixed binding")
        self._states = {b.instance_id: tuple(state.get(b.instance_id, ())) for b in bindings}
        capacities: dict[BarStream, int] = {}
        for binding in self._bindings:
            resolve(binding.config.strategy_id).validate_state(self._states[binding.instance_id])
            capacities[binding.stream] = max(
                capacities.get(binding.stream, 0), binding.config.history_bars
            )
        data = DataEngine()
        for stream, capacity in capacities.items():
            data = data.subscribe(stream, capacity, history=histories.get(stream, ()))
        self._data = data

    def _require_owner(self) -> None:
        if (os.getpid(), get_ident()) != self._owner:
            raise RuntimeError("Trader belongs to its core thread")
        if self._closed:
            raise RuntimeError("Trader is closed")

    def _binding(self, instance_id: str) -> StrategyBinding:
        self._require_owner()
        for binding in self._bindings:
            if binding.instance_id == instance_id:
                return binding
        raise ValueError("unknown strategy instance")

    def history(self, instance_id: str) -> tuple[MarketBar, ...]:
        binding = self._binding(instance_id)
        return self._data.window(binding.stream).bars[-binding.config.history_bars :]

    def state(self, instance_id: str) -> tuple[tuple[str, str | int], ...]:
        self._binding(instance_id)
        return self._states[instance_id]

    def accepts(self, stream: BarStream, bar: MarketBar) -> bool:
        self._require_owner()
        return self._data.accepts(stream, bar)

    def advance(
        self, stream: BarStream, bar: MarketBar, *, at: datetime | None = None
    ) -> dict[str, Step]:
        self._require_owner()
        if self._advancing:
            raise RuntimeError("Trader ingress cannot be reentrant")
        self._advancing = True
        try:
            return self._advance(stream, bar, at=at)
        finally:
            self._advancing = False

    def _advance(
        self, stream: BarStream, bar: MarketBar, *, at: datetime | None
    ) -> dict[str, Step]:
        observed_at = bar.available_at if at is None else at
        data, frame = self._data.advance(stream, bar, at=observed_at)
        if frame is None:
            return {}
        states = dict(self._states)
        results = {}
        for binding in self._bindings:
            if binding.stream != stream:
                continue
            result = step(
                binding.config,
                Inputs(
                    tuple(
                        Bar(
                            item.observation_id,
                            stream.contract_id,
                            item.completed_at,
                            item.available_at,
                            item.close,
                        )
                        for item in frame.bars[-binding.config.history_bars :]
                    ),
                    observed_at,
                    stream.contract_id,
                    stream.interval_seconds,
                    source_scope=stream.source_scope,
                ),
                states[binding.instance_id],
            )
            if result.intent is not None:
                result = replace(
                    result,
                    intent=replace(
                        result.intent,
                        evidence=(
                            *result.intent.evidence,
                            ("strategy_binding", self._binding_ids[binding.instance_id]),
                        ),
                    ),
                )
            states[binding.instance_id] = result.decision.state
            results[binding.instance_id] = result
        self._data, self._states = data, states
        return results

    def close(self) -> None:
        if (os.getpid(), get_ident()) != self._owner:
            raise RuntimeError("Trader belongs to its core thread")
        if self._advancing:
            raise RuntimeError("Trader cannot close during ingress")
        self._closed = True
