"""Account-neutral targets, explicit decision kinds and per-instance strategy state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from northstar_quant.accounting.amounts import decimal_text
from northstar_quant.factors.definition import Inputs, Parameter, Result, content_id


@dataclass(frozen=True)
class StrategyIntent:
    observation_id: UUID
    contract_id: UUID
    generated_at: datetime
    valid_until: datetime
    target_fraction: Decimal
    strategy_id: str = ""
    evidence: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.observation_id, UUID) or not isinstance(self.contract_id, UUID):
            raise ValueError("strategy identity must use canonical UUIDs")
        if (
            self.generated_at.utcoffset() != timedelta(0)
            or self.valid_until.utcoffset() != timedelta(0)
            or self.valid_until <= self.generated_at
        ):
            raise ValueError("strategy intent requires a positive UTC lifetime")
        if not self.target_fraction.is_finite() or not Decimal(
            -1
        ) <= self.target_fraction <= Decimal(1):
            raise ValueError("strategy target must be finite and in [-1, 1]")

    def to_dict(self) -> dict[str, object]:
        return {
            "observation_id": str(self.observation_id),
            "contract_id": str(self.contract_id),
            "generated_at": self.generated_at.isoformat().replace("+00:00", "Z"),
            "valid_until": self.valid_until.isoformat().replace("+00:00", "Z"),
            "target_fraction": decimal_text(self.target_fraction),
            "target_unit": "POSITION_LIMIT_FRACTION",
            "strategy_id": self.strategy_id,
            "evidence": dict(self.evidence),
        }

    @property
    def intent_id(self) -> str:
        return content_id(self.to_dict())


class DecisionKind(StrEnum):
    SET_TARGET = "SET_TARGET"
    NO_NEW_TARGET = "NO_NEW_TARGET"
    INPUT_UNAVAILABLE = "INPUT_UNAVAILABLE"


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    target: Decimal | None
    reason: str
    state: tuple[tuple[str, str | int], ...] = ()


class Strategy(Protocol):
    strategy_id: str
    revision: str
    parameters: tuple[Parameter, ...]
    factor_slots: tuple[tuple[str, str], ...]

    def validate_state(self, state: tuple[tuple[str, str | int], ...]) -> None: ...

    def decide(
        self,
        factors: dict[str, Result],
        values: dict[str, str | int],
        state: tuple[tuple[str, str | int], ...],
    ) -> Decision: ...


@dataclass(frozen=True)
class Step:
    decision: Decision
    intent: StrategyIntent | None
    factors: tuple[tuple[str, Result], ...]


def intent_from(
    decision: Decision,
    inputs: Inputs,
    strategy_id: str,
    values: dict[str, str | int],
    factors: dict[str, Result],
) -> StrategyIntent | None:
    if decision.kind != DecisionKind.SET_TARGET:
        if decision.target is not None:
            raise ValueError("non-target decisions cannot carry exposure")
        return None
    if decision.target is None or not inputs.bars:
        raise ValueError("target decision requires an explicit exposure and input")
    return StrategyIntent(
        inputs.bars[-1].observation_id,
        inputs.contract_id,
        inputs.at,
        inputs.at + timedelta(seconds=int(values["order_lifetime_seconds"])),
        decision.target,
        strategy_id,
        tuple((alias, content_id(result.to_dict())) for alias, result in sorted(factors.items())),
    )
