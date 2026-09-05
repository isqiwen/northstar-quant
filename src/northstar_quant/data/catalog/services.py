"""Application services for catalog mutation and read-only API queries."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from northstar_quant.data.catalog.models import (
    CalendarTradingDay,
    DataSeries,
    Exchange,
    FuturesContract,
    FuturesProduct,
    TradingCalendar,
    TradingSession,
)

_IDENTIFIER = re.compile(r"[A-Z0-9][A-Z0-9._-]*")


class CatalogInvariantError(ValueError):
    """Raised before a command would violate a Data Hub catalog invariant."""


def _canonical_identifier(value: str, field_name: str) -> str:
    canonical = value.strip().upper()
    if not _IDENTIFIER.fullmatch(canonical):
        raise CatalogInvariantError(
            f"{field_name} must contain only uppercase letters, digits, '.', '_' or '-'"
        )
    return canonical


def _validate_timezone(timezone_name: str) -> str:
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise CatalogInvariantError(f"unknown IANA timezone: {timezone_name}") from error
    return timezone_name


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CatalogInvariantError(f"{field_name} must include a timezone offset")


def _is_representable_at_scale(value: Decimal, scale: int) -> bool:
    if not value.is_finite():
        return False
    with localcontext() as context:
        context.prec = max(25, len(value.as_tuple().digits) + 1)
        try:
            return value.quantize(Decimal(1).scaleb(-scale)) == value
        except InvalidOperation:
            return False


class CatalogCommands:
    """Transactional catalog mutations used by the bootstrap entry point.

    These methods intentionally do not commit.  The caller chooses the surrounding
    atomic unit of work, and the public HTTP surface remains read-only until an
    authenticated administration boundary is introduced.
    """

    @staticmethod
    def register_exchange(
        session: Session,
        *,
        code: str,
        name: str,
        timezone_name: str,
        country_code: str = "CN",
    ) -> Exchange:
        exchange = Exchange(
            code=_canonical_identifier(code, "exchange code"),
            name=name.strip(),
            timezone_name=_validate_timezone(timezone_name),
            country_code=country_code.strip().upper(),
        )
        session.add(exchange)
        session.flush()
        return exchange

    @staticmethod
    def register_product(
        session: Session,
        *,
        exchange_id: uuid.UUID,
        code: str,
        name: str,
        price_tick: Decimal,
        contract_multiplier: Decimal,
        quantity_unit: str,
        currency: str = "CNY",
    ) -> FuturesProduct:
        if (
            not price_tick.is_finite()
            or not contract_multiplier.is_finite()
            or price_tick <= 0
            or contract_multiplier <= 0
        ):
            raise CatalogInvariantError("price_tick and contract_multiplier must be positive")
        if not _is_representable_at_scale(price_tick, 12):
            raise CatalogInvariantError("price_tick exceeds the catalog decimal scale")
        product = FuturesProduct(
            exchange_id=exchange_id,
            code=_canonical_identifier(code, "product code"),
            name=name.strip(),
            price_tick=price_tick,
            contract_multiplier=contract_multiplier,
            quantity_unit=quantity_unit.strip(),
            currency=currency.strip().upper(),
        )
        session.add(product)
        session.flush()
        return product

    @staticmethod
    def register_contract(
        session: Session,
        *,
        product_id: uuid.UUID,
        contract_code: str,
        listed_on: date | None = None,
        last_trade_date: date | None = None,
        status: str = "LISTED",
    ) -> FuturesContract:
        if listed_on is not None and last_trade_date is not None and last_trade_date < listed_on:
            raise CatalogInvariantError("last_trade_date must not precede listed_on")
        contract = FuturesContract(
            product_id=product_id,
            contract_code=_canonical_identifier(contract_code, "contract code"),
            listed_on=listed_on,
            last_trade_date=last_trade_date,
            status=status.strip().upper(),
        )
        session.add(contract)
        session.flush()
        return contract

    @staticmethod
    def register_calendar(
        session: Session,
        *,
        exchange_id: uuid.UUID,
        code: str,
        revision: int,
        timezone_name: str,
    ) -> TradingCalendar:
        if revision <= 0:
            raise CatalogInvariantError("calendar revision must be positive")
        exchange = session.get(Exchange, exchange_id)
        if exchange is None:
            raise CatalogInvariantError("calendar exchange must exist")
        normalized_timezone = _validate_timezone(timezone_name)
        if normalized_timezone != exchange.timezone_name:
            raise CatalogInvariantError(
                "calendar timezone must match the canonical exchange timezone"
            )
        calendar = TradingCalendar(
            exchange_id=exchange_id,
            code=_canonical_identifier(code, "calendar code"),
            revision=revision,
            timezone_name=normalized_timezone,
        )
        session.add(calendar)
        session.flush()
        return calendar

    @staticmethod
    def register_trading_day(
        session: Session,
        *,
        calendar_id: uuid.UUID,
        trading_day: date,
        status: str,
        note: str | None = None,
    ) -> CalendarTradingDay:
        normalized_status = status.strip().upper()
        if normalized_status not in {"OPEN", "CLOSED"}:
            raise CatalogInvariantError("trading-day status must be OPEN or CLOSED")
        record = CalendarTradingDay(
            calendar_id=calendar_id,
            trading_day=trading_day,
            status=normalized_status,
            note=note,
        )
        session.add(record)
        session.flush()
        return record

    @staticmethod
    def register_session(
        session: Session,
        *,
        calendar_id: uuid.UUID,
        trading_day: date,
        sequence: int,
        kind: str,
        opens_at: datetime,
        closes_at: datetime,
    ) -> TradingSession:
        _require_aware(opens_at, "opens_at")
        _require_aware(closes_at, "closes_at")
        if opens_at >= closes_at:
            raise CatalogInvariantError("session opens_at must be before closes_at")
        if sequence < 0:
            raise CatalogInvariantError("session sequence must be nonnegative")
        normalized_kind = kind.strip().upper()
        if normalized_kind not in {"NIGHT", "DAY", "AUCTION"}:
            raise CatalogInvariantError("session kind must be NIGHT, DAY, or AUCTION")

        calendar_day = session.get(
            CalendarTradingDay,
            {"calendar_id": calendar_id, "trading_day": trading_day},
        )
        if calendar_day is None or calendar_day.status != "OPEN":
            raise CatalogInvariantError(
                "a session requires an explicit OPEN trading day in the pinned calendar"
            )

        overlap = session.scalar(
            select(TradingSession.id)
            .where(
                TradingSession.calendar_id == calendar_id,
                TradingSession.opens_at < closes_at,
                TradingSession.closes_at > opens_at,
            )
            .limit(1)
        )
        if overlap is not None:
            raise CatalogInvariantError("session overlaps an existing session")

        trading_session = TradingSession(
            calendar_id=calendar_id,
            trading_day=trading_day,
            sequence=sequence,
            kind=normalized_kind,
            opens_at=opens_at,
            closes_at=closes_at,
        )
        session.add(trading_session)
        session.flush()
        return trading_session

    @staticmethod
    def register_data_series(
        session: Session,
        *,
        contract_id: uuid.UUID,
        calendar_id: uuid.UUID,
        interval: str,
        price_scale: int,
        quantity_scale: int,
        volume_unit: str = "LOT",
        turnover_currency: str | None = None,
    ) -> DataSeries:
        if interval not in {"1m", "1d"}:
            raise CatalogInvariantError("interval must be 1m or 1d")
        if not 0 <= price_scale <= 12 or not 0 <= quantity_scale <= 12:
            raise CatalogInvariantError("series scales must be between 0 and 12")

        contract = session.get(FuturesContract, contract_id)
        calendar = session.get(TradingCalendar, calendar_id)
        if contract is None or calendar is None:
            raise CatalogInvariantError("contract and calendar must exist")
        if contract.product.exchange_id != calendar.exchange_id:
            raise CatalogInvariantError(
                "a data series must pin a calendar from the contract's exchange"
            )
        if not _is_representable_at_scale(contract.product.price_tick, price_scale):
            raise CatalogInvariantError(
                "product price_tick must be exactly representable at the series price_scale"
            )

        normalized_volume_unit = _canonical_identifier(volume_unit, "volume unit")
        normalized_turnover_currency = (
            contract.product.currency
            if turnover_currency is None
            else turnover_currency.strip().upper()
        )
        if len(normalized_turnover_currency) != 3:
            raise CatalogInvariantError("turnover currency must be a three-letter code")

        series = DataSeries(
            contract_id=contract_id,
            calendar_id=calendar_id,
            interval=interval,
            price_scale=price_scale,
            quantity_scale=quantity_scale,
            volume_unit=normalized_volume_unit,
            turnover_currency=normalized_turnover_currency,
        )
        session.add(series)
        session.flush()
        return series
