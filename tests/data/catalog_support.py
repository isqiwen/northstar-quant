"""Synthetic SHFE-like catalogs for behavior tests; no proprietary data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from northstar_quant.data.catalog.models import (
    DataSeries,
    FuturesContract,
    TradingCalendar,
    TradingSession,
)
from northstar_quant.data.catalog.services import CatalogCommands

ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class SyntheticCatalog:
    contract: FuturesContract
    calendar: TradingCalendar
    minute_series: DataSeries
    daily_series: DataSeries
    sessions: tuple[TradingSession, ...]


def at_local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ASIA_SHANGHAI)


def seed_synthetic_catalog(session: Session) -> SyntheticCatalog:
    """Build one explicit, non-continuous RB contract and its calendar version."""

    commands = CatalogCommands()
    exchange = commands.register_exchange(
        session,
        code="SHFE",
        name="Synthetic Shanghai Futures Exchange",
        timezone_name="Asia/Shanghai",
    )
    product = commands.register_product(
        session,
        exchange_id=exchange.id,
        code="RB",
        name="Synthetic Rebar",
        price_tick=Decimal("1.00000000"),
        contract_multiplier=Decimal("10.00000000"),
        quantity_unit="TON",
    )
    contract = commands.register_contract(
        session,
        product_id=product.id,
        contract_code="RB2605",
        listed_on=date(2025, 11, 1),
        last_trade_date=date(2026, 5, 15),
    )
    calendar = commands.register_calendar(
        session,
        exchange_id=exchange.id,
        code="SHFE-RB",
        revision=1,
        timezone_name="Asia/Shanghai",
    )
    commands.register_trading_day(
        session,
        calendar_id=calendar.id,
        trading_day=date(2026, 1, 7),
        status="OPEN",
    )
    commands.register_trading_day(
        session,
        calendar_id=calendar.id,
        trading_day=date(2026, 1, 12),
        status="OPEN",
        note="Synthetic Monday after Friday night session",
    )
    sessions = (
        commands.register_session(
            session,
            calendar_id=calendar.id,
            trading_day=date(2026, 1, 7),
            sequence=0,
            kind="NIGHT",
            opens_at=at_local(2026, 1, 6, 21),
            closes_at=at_local(2026, 1, 7, 2, 30),
        ),
        commands.register_session(
            session,
            calendar_id=calendar.id,
            trading_day=date(2026, 1, 7),
            sequence=1,
            kind="DAY",
            opens_at=at_local(2026, 1, 7, 9),
            closes_at=at_local(2026, 1, 7, 11, 30),
        ),
        commands.register_session(
            session,
            calendar_id=calendar.id,
            trading_day=date(2026, 1, 7),
            sequence=2,
            kind="DAY",
            opens_at=at_local(2026, 1, 7, 13, 30),
            closes_at=at_local(2026, 1, 7, 15),
        ),
    )
    minute_series = commands.register_data_series(
        session,
        contract_id=contract.id,
        calendar_id=calendar.id,
        interval="1m",
        price_scale=2,
        quantity_scale=0,
    )
    daily_series = commands.register_data_series(
        session,
        contract_id=contract.id,
        calendar_id=calendar.id,
        interval="1d",
        price_scale=2,
        quantity_scale=0,
    )
    session.commit()
    return SyntheticCatalog(
        contract=contract,
        calendar=calendar,
        minute_series=minute_series,
        daily_series=daily_series,
        sessions=sessions,
    )
