"""Fixed futures charges and their causal applicability; no account mutation."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any
from uuid import UUID

from northstar_quant.execution.orders import Offset, Side

from .amounts import decimal_text


def _amount(value: Decimal, name: str, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be an exact nonnegative amount")
    exponent = value.as_tuple().exponent
    if (
        (positive and value == 0)
        or not isinstance(exponent, int)
        or exponent < -18
        or value.adjusted() > 33
        or len(value.as_tuple().digits) > 34
    ):
        raise ValueError(f"{name} exceeds the financial domain")


@dataclass(frozen=True, slots=True)
class ChargeRate:
    """Absolute money- and lot-based charges are additive, never relative rates."""

    by_money: Decimal
    by_volume: Decimal

    def __post_init__(self) -> None:
        _amount(self.by_money, "by_money")
        _amount(self.by_volume, "by_volume")

    def amount(self, notional: Decimal, lots: int) -> Decimal:
        if not isinstance(notional, Decimal) or not notional.is_finite() or notional < 0:
            raise ValueError("charge notional must be exact and nonnegative")
        if type(lots) is not int or not 0 <= lots <= 1_000_000_000:
            raise ValueError("charge lots must be bounded and nonnegative")
        with localcontext() as context:
            context.prec = 192
            context.rounding = ROUND_HALF_EVEN
            return notional * self.by_money + lots * self.by_volume

    def to_dict(self) -> dict[str, object]:
        return {"by_money": decimal_text(self.by_money), "by_volume": decimal_text(self.by_volume)}

    @classmethod
    def from_dict(cls, value: object) -> ChargeRate:
        if (
            not isinstance(value, dict)
            or set(value) != {"by_money", "by_volume"}
            or any(not isinstance(item, str) for item in value.values())
        ):
            raise ValueError("charge rates require exact money and volume strings")
        return cls(Decimal(value["by_money"]), Decimal(value["by_volume"]))


@dataclass(frozen=True, slots=True)
class FuturesTerms:
    """A selected revision valid on [effective_from, effective_until).

    Publication chooses one non-overlapping revision per interval. A source
    reference is evidence identity, not proof that the supplied terms are true.
    Fee rounding is explicit; margin uses conservative upward quantum rounding.
    No portfolio offsets or spread-margin benefits are assumed.
    """

    terms_id: str
    contract_id: UUID
    effective_from: datetime
    effective_until: datetime
    available_at: datetime
    source_reference: str
    open_fee: ChargeRate
    close_today_fee: ChargeRate
    close_yesterday_fee: ChargeRate
    long_margin: ChargeRate
    short_margin: ChargeRate
    lower_limit: Decimal
    upper_limit: Decimal
    money_quantum: Decimal
    fee_rounding: str

    def __post_init__(self) -> None:
        if not isinstance(self.contract_id, UUID) or any(
            not isinstance(value, str) or not 1 <= len(value) <= 256
            for value in (self.terms_id, self.source_reference)
        ):
            raise ValueError("terms require bounded identity and source reference")
        if (
            any(
                not isinstance(at, datetime) or at.utcoffset() != timedelta(0)
                for at in (self.effective_from, self.effective_until, self.available_at)
            )
            or self.effective_from >= self.effective_until
        ):
            raise ValueError("terms require an explicit UTC validity interval")
        for name in (
            "open_fee",
            "close_today_fee",
            "close_yesterday_fee",
            "long_margin",
            "short_margin",
        ):
            if not isinstance(getattr(self, name), ChargeRate):
                raise ValueError("terms require all absolute fee and margin rates")
        for name in ("lower_limit", "upper_limit", "money_quantum"):
            _amount(getattr(self, name), name, positive=True)
        if self.lower_limit > self.upper_limit or self.fee_rounding not in {
            "ROUND_HALF_EVEN",
            "ROUND_HALF_UP",
            "ROUND_CEILING",
        }:
            raise ValueError("terms price bounds or rounding are invalid")

    def require_available(self, at: datetime, *, start: datetime | None = None) -> None:
        begin = at if start is None else start
        if (
            at.utcoffset() != timedelta(0)
            or begin.utcoffset() != timedelta(0)
            or not self.effective_from <= begin <= at < self.effective_until
            or self.available_at > begin
        ):
            raise ValueError("terms are not available and effective for this event interval")

    def fee(self, offset: Offset, price: Decimal, multiplier: Decimal, lots: int) -> Decimal:
        rate = {
            Offset.OPEN: self.open_fee,
            Offset.CLOSE_TODAY: self.close_today_fee,
            Offset.CLOSE_YESTERDAY: self.close_yesterday_fee,
        }[offset]
        return self._charge(rate, price, multiplier, lots, self.fee_rounding)

    def margin(self, side: Side, price: Decimal, multiplier: Decimal, lots: int) -> Decimal:
        if not isinstance(side, Side):
            raise ValueError("margin direction must be explicit")
        rate = self.long_margin if side is Side.BUY else self.short_margin
        return self._charge(rate, price, multiplier, lots, ROUND_CEILING)

    def _charge(
        self, rate: ChargeRate, price: Decimal, multiplier: Decimal, lots: int, rounding: str
    ) -> Decimal:
        _amount(price, "price", positive=True)
        _amount(multiplier, "multiplier", positive=True)
        with localcontext() as context:
            context.prec = 192
            context.rounding = ROUND_HALF_EVEN
            value = rate.amount(price * multiplier * lots, lots)
            return (value / self.money_quantum).to_integral_value(
                rounding=rounding
            ) * self.money_quantum

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for field in fields(self):
            value = getattr(self, field.name)
            result[field.name] = (
                value.to_dict()
                if isinstance(value, ChargeRate)
                else decimal_text(value)
                if isinstance(value, Decimal)
                else value.isoformat()
                if isinstance(value, datetime)
                else str(value)
            )
        return result

    @classmethod
    def from_dict(cls, value: object) -> FuturesTerms:
        if not isinstance(value, dict) or set(value) != {field.name for field in fields(cls)}:
            raise ValueError("fixed terms require their complete canonical fields")
        try:
            rates = {
                "open_fee",
                "close_today_fee",
                "close_yesterday_fee",
                "long_margin",
                "short_margin",
            }
            converted: dict[str, Any] = {}
            for name, item in value.items():
                if name in rates:
                    converted[name] = ChargeRate.from_dict(item)
                elif not isinstance(item, str):
                    raise ValueError("fixed term values must be exact strings")
                elif name in {"effective_from", "effective_until", "available_at"}:
                    converted[name] = datetime.fromisoformat(item)
                elif name == "contract_id":
                    converted[name] = UUID(item)
                elif name in {"lower_limit", "upper_limit", "money_quantum"}:
                    converted[name] = Decimal(item)
                else:
                    converted[name] = item
            return cls(**converted)
        except (TypeError, ArithmeticError) as error:
            raise ValueError("invalid fixed futures terms") from error


def ordered_terms(values: tuple[FuturesTerms, ...]) -> tuple[FuturesTerms, ...]:
    if len(values) > 10000 or any(not isinstance(value, FuturesTerms) for value in values):
        raise ValueError("fixed terms must be a bounded list of revisions")
    ordered = tuple(sorted(values, key=lambda item: (str(item.contract_id), item.effective_from)))
    if len({item.terms_id for item in ordered}) != len(ordered):
        raise ValueError("fixed terms repeat an identity")
    for before, after in zip(ordered, ordered[1:]):
        if (
            before.contract_id == after.contract_id
            and before.effective_until > after.effective_from
        ):
            raise ValueError("fixed terms overlap; publication must select one revision")
    return ordered
