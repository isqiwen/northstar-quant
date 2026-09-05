"""Completed-bar momentum produces account-neutral target exposure."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID


@dataclass(frozen=True, slots=True)
class StrategyIntent:
    """A fraction of the configured position limit, never an order."""

    observation_id: UUID
    contract_id: UUID
    generated_at: datetime
    valid_until: datetime
    momentum: Decimal
    target_fraction: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.observation_id, UUID) or not isinstance(self.contract_id, UUID):
            raise ValueError("strategy identity must use canonical UUIDs")
        if (
            self.generated_at.utcoffset() != timedelta(0)
            or self.valid_until.utcoffset() != timedelta(0)
            or self.valid_until <= self.generated_at
        ):
            raise ValueError("strategy intent requires a positive UTC lifetime")
        if (
            not self.momentum.is_finite()
            or not self.target_fraction.is_finite()
            or not Decimal(-1) <= self.target_fraction <= Decimal(1)
        ):
            raise ValueError("strategy target must be finite and in [-1, 1]")

    @property
    def intent_id(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "observation_id": str(self.observation_id),
                    "contract_id": str(self.contract_id),
                    "generated_at": self.generated_at.isoformat(),
                    "valid_until": self.valid_until.isoformat(),
                    "momentum": decimal_text(self.momentum),
                    "target_fraction": decimal_text(self.target_fraction),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


def validate_momentum_parameters(
    threshold: Decimal, target_fraction: Decimal, lifetime_seconds: int
) -> None:
    """Strategy owns the meaning of its threshold, target and intent lifetime."""

    if (
        not isinstance(threshold, Decimal)
        or not threshold.is_finite()
        or not Decimal(0) <= threshold <= Decimal(1)
        or not isinstance(target_fraction, Decimal)
        or not target_fraction.is_finite()
        or not Decimal(0) < target_fraction <= Decimal(1)
        or type(lifetime_seconds) is not int
        or lifetime_seconds <= 0
    ):
        raise ValueError("momentum requires threshold [0, 1], target (0, 1] and positive lifetime")


def momentum_intent(
    *,
    observation_id: UUID,
    contract_id: UUID,
    at: datetime,
    previous_close: Decimal,
    close: Decimal,
    threshold: Decimal,
    target_fraction: Decimal,
    lifetime_seconds: int,
) -> StrategyIntent:
    """Map a thresholded return's sign to an explicit target fraction."""

    validate_momentum_parameters(threshold, target_fraction, lifetime_seconds)
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        momentum = close / previous_close - Decimal(1)
        target = (
            target_fraction
            if momentum > threshold
            else -target_fraction
            if momentum < -threshold
            else Decimal(0)
        )
    return StrategyIntent(
        observation_id, contract_id, at, at + timedelta(seconds=lifetime_seconds), momentum, target
    )


def decimal_text(value: Decimal) -> str:
    """Canonical financial text for the application's persisted run evidence."""

    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text
