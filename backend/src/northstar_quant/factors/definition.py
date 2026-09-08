"""Immutable, point-in-time factor inputs, parameters and available results."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from northstar_quant.accounting.amounts import decimal_text


@dataclass(frozen=True)
class Parameter:
    name: str
    label: str
    default: str | int
    minimum: str | int
    maximum: str | int
    unit: str

    def parse(self, value: object) -> str | int:
        if type(self.default) is int:
            if type(value) is not int or not int(self.minimum) <= value <= int(self.maximum):
                raise ValueError(
                    f"{self.name} must be an integer in [{self.minimum}, {self.maximum}]"
                )
            return value
        if (
            not isinstance(value, str)
            or re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,18})?", value) is None
        ):
            raise ValueError(f"{self.name} must be a plain decimal string")
        number = Decimal(value)
        if not Decimal(self.minimum) <= number <= Decimal(self.maximum):
            raise ValueError(f"{self.name} must be in [{self.minimum}, {self.maximum}]")
        return decimal_text(number)


def parameters(
    definition: tuple[Parameter, ...], supplied: dict[str, object]
) -> tuple[tuple[str, str | int], ...]:
    if set(supplied) - {item.name for item in definition}:
        raise ValueError("unknown algorithm parameters")
    return tuple(
        (item.name, item.parse(supplied.get(item.name, item.default))) for item in definition
    )


def content_id(value: object) -> str:
    """Identify fixed factor/strategy computation material, excluding display labels."""
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


class Status(StrEnum):
    READY = "READY"
    WARMING_UP = "WARMING_UP"
    MISSING_INPUT = "MISSING_INPUT"
    STALE_INPUT = "STALE_INPUT"
    INVALID_INPUT = "INVALID_INPUT"


@dataclass(frozen=True)
class Bar:
    observation_id: UUID
    contract_id: UUID
    completed_at: datetime
    available_at: datetime
    close: Decimal | None


@dataclass(frozen=True)
class Inputs:
    bars: tuple[Bar, ...]
    at: datetime
    contract_id: UUID
    interval_seconds: int = 60
    price_basis: str = "REAL_CONTRACT"
    source_scope: str = ""


@dataclass(frozen=True)
class Requirements:
    history_bars: int
    interval_seconds: int = 60
    max_age_seconds: int = 60
    price_basis: str = "REAL_CONTRACT"
    fields: tuple[str, ...] = ("close",)


@dataclass(frozen=True)
class Result:
    binding_id: str
    status: Status
    value: Decimal | None
    available_at: datetime | None
    observations: tuple[UUID, ...]
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "binding_id": self.binding_id,
            "status": self.status.value,
            "value": None if self.value is None else decimal_text(self.value),
            "available_at": None if self.available_at is None else self.available_at.isoformat(),
            "observations": [str(item) for item in self.observations],
            "reason": self.reason,
        }


class Factor(Protocol):
    factor_id: str
    revision: str
    parameters: tuple[Parameter, ...]

    def requirements(self, values: dict[str, str | int]) -> Requirements: ...
    def compute(self, closes: tuple[Decimal, ...], values: dict[str, str | int]) -> Decimal: ...
