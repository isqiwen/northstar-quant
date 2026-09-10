from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, localcontext
from uuid import UUID

import pytest

from northstar_quant.accounting.terms import ChargeRate, FuturesTerms, ordered_terms
from northstar_quant.execution.orders import Offset, Side


def terms() -> FuturesTerms:
    start = datetime(2026, 1, 5, tzinfo=UTC)
    return FuturesTerms(
        "synthetic-terms",
        UUID(int=1),
        start,
        start + timedelta(days=1),
        start,
        "synthetic arithmetic evidence",
        ChargeRate(Decimal("0.0001"), Decimal(1)),
        ChargeRate(Decimal("0.0002"), Decimal(3)),
        ChargeRate(Decimal(0), Decimal(2)),
        ChargeRate(Decimal("0.1"), Decimal(4)),
        ChargeRate(Decimal("0.2"), Decimal(5)),
        Decimal(80),
        Decimal(120),
        Decimal("0.01"),
        "ROUND_HALF_UP",
    )


def test_charges_preserve_direction_offset_rounding_and_decimal_context() -> None:
    value = terms()
    with localcontext() as context:
        context.prec = 4
        context.rounding = ROUND_DOWN
        assert value.fee(Offset.OPEN, Decimal(101), Decimal(10), 3) == Decimal("3.30")
        assert value.fee(Offset.CLOSE_TODAY, Decimal(101), Decimal(10), 3) == Decimal("9.61")
        assert value.fee(Offset.CLOSE_YESTERDAY, Decimal(101), Decimal(10), 3) == Decimal(6)
        assert value.margin(Side.BUY, Decimal(101), Decimal(10), 3) == Decimal(315)
        assert value.margin(Side.SELL, Decimal(101), Decimal(10), 3) == Decimal(621)
        assert value.margin(Side.BUY, Decimal(101), Decimal(10), 0) == 0
    assert FuturesTerms.from_dict(value.to_dict()) == value
    quantum = replace(value, money_quantum=Decimal("0.05"))
    assert quantum.fee(Offset.CLOSE_TODAY, Decimal(101), Decimal(10), 3) == Decimal("9.60")


def test_term_gaps_late_availability_and_overlapping_revisions_are_not_latest_wins() -> None:
    value = terms()
    value.require_available(value.effective_from)
    with pytest.raises(ValueError, match="available and effective"):
        value.require_available(value.effective_until)
    with pytest.raises(ValueError, match="available and effective"):
        replace(value, available_at=value.effective_from + timedelta(seconds=1)).require_available(
            value.effective_from + timedelta(minutes=1), start=value.effective_from
        )
    with pytest.raises(ValueError, match="overlap"):
        ordered_terms((value, replace(value, terms_id="revision")))
    with pytest.raises(ValueError, match="complete canonical"):
        FuturesTerms.from_dict({**value.to_dict(), "override_fee": "0"})
    following = replace(
        value,
        terms_id="next-day",
        effective_from=value.effective_until,
        effective_until=value.effective_until + timedelta(days=1),
    )
    assert ordered_terms((following, value)) == (value, following)
