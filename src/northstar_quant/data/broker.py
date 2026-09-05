"""Resolve an observed SHFE futures contract in the existing Data catalog.

The caller supplies the exact, identity-confirmed Instrument query row. This
Module neither connects to a broker nor fabricates the product metadata absent
from that row. In particular, a product's physical quantity unit must already
have been registered; an Instrument response cannot establish it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from northstar_quant.data.catalog.models import Exchange, FuturesContract, FuturesProduct
from northstar_quant.data.catalog.services import CatalogCommands


@dataclass(frozen=True, slots=True)
class BrokerContract:
    contract_id: UUID
    exchange: str
    symbol: str


def _date_field(value: object, name: str, *, required: bool) -> date | None:
    if value is None or value == "":
        if required:
            raise ValueError(f"broker instrument requires {name}")
        return None
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{8}", value) is None:
        raise ValueError(f"broker instrument {name} must be a complete calendar date")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"broker instrument {name} is not a calendar date") from error


def resolve_broker_contract(engine: Engine, instrument: dict[str, object]) -> BrokerContract:
    """Reuse or register one observed contract, without changing existing facts.

    Required Instrument fields are ExchangeID, ProductClass, InstrumentID,
    ProductID, DeliveryYear, DeliveryMonth, ExpireDate, PriceTick and
    VolumeMultiple. OpenDate is optional; an unknown date never overwrites a
    known one. Symbols are checked against the explicitly reported product and
    delivery month, not used to guess those fields. Only SHFE futures backed by
    a registered CNY product are supported. Resolution grants no trading right.
    """
    return _contract(engine, instrument, expected_id=None)


def verify_broker_contract(
    engine: Engine, contract_id: UUID, instrument: dict[str, object]
) -> BrokerContract:
    """Read and verify a retained mapping; never register or repair missing facts.

    The same observed fields required by resolution must still agree with the
    existing Data identity. This read-only operation is safe for evidence reads
    and recovery verification: an absent or different UUID is an error.
    """
    if not isinstance(contract_id, UUID):
        raise ValueError("broker contract verification requires a canonical UUID")
    return _contract(engine, instrument, expected_id=contract_id)


def _contract(
    engine: Engine, instrument: dict[str, object], *, expected_id: UUID | None
) -> BrokerContract:
    if engine.dialect.name != "postgresql":
        raise ValueError("broker contract resolution requires PostgreSQL")
    if not isinstance(instrument, dict):
        raise ValueError("broker instrument must be a saved query row")
    if instrument.get("ExchangeID") != "SHFE" or instrument.get("ProductClass") != "1":
        raise ValueError("broker contract resolution supports only SHFE futures")
    raw_symbol, raw_product = instrument.get("InstrumentID"), instrument.get("ProductID")
    if (
        not isinstance(raw_symbol, str)
        or re.fullmatch(r"[A-Za-z]{1,3}[0-9]{4}", raw_symbol) is None
    ):
        raise ValueError("broker instrument requires an explicit SHFE futures InstrumentID")
    if not isinstance(raw_product, str) or re.fullmatch(r"[A-Za-z]{1,3}", raw_product) is None:
        raise ValueError("broker instrument requires an explicit ProductID")
    year, month = instrument.get("DeliveryYear"), instrument.get("DeliveryMonth")
    if (
        type(year) is not int
        or not 2000 <= year <= 2099
        or type(month) is not int
        or not 1 <= month <= 12
    ):
        raise ValueError("broker instrument requires an explicit supported delivery year and month")
    symbol, product_code = raw_symbol.upper(), raw_product.upper()
    if symbol != f"{product_code}{year % 100:02d}{month:02d}":
        raise ValueError("broker instrument identity conflicts with its product or delivery month")
    expires = _date_field(instrument.get("ExpireDate"), "ExpireDate", required=True)
    listed = _date_field(instrument.get("OpenDate"), "OpenDate", required=False)
    assert expires is not None
    if (expires.year, expires.month) != (year, month) or listed is not None and listed > expires:
        raise ValueError("broker instrument dates conflict with its delivery month")
    raw_tick, multiplier = instrument.get("PriceTick"), instrument.get("VolumeMultiple")
    if not isinstance(raw_tick, str) or not 1 <= len(raw_tick) <= 64:
        raise ValueError("broker instrument PriceTick must be an exact decimal string")
    try:
        tick = Decimal(raw_tick)
    except InvalidOperation as error:
        raise ValueError("broker instrument PriceTick must be an exact decimal string") from error
    if not tick.is_finite() or tick <= 0:
        raise ValueError("broker instrument PriceTick must be finite and positive")
    if type(multiplier) is not int or not 1 <= multiplier <= 1_000_000_000:
        raise ValueError("broker instrument VolumeMultiple must be a positive integer")

    with Session(engine, expire_on_commit=False) as session, session.begin():
        if expected_id is None:
            # The research importer uses this same Data-catalog registration lock.
            session.execute(text("SELECT pg_advisory_xact_lock(728401927)"))
        exchange = session.scalar(select(Exchange).where(Exchange.code == "SHFE"))
        if exchange is None:
            raise ValueError("broker contract requires a registered SHFE exchange and product")
        if exchange.timezone_name != "Asia/Shanghai" or exchange.country_code != "CN":
            raise ValueError("broker exchange conflicts with the registered SHFE identity")
        product = session.scalar(
            select(FuturesProduct).where(
                FuturesProduct.exchange_id == exchange.id,
                FuturesProduct.code == product_code,
            )
        )
        if product is None:
            raise ValueError(
                "broker contract requires registered product metadata, including its quantity unit"
            )
        if (
            product.price_tick != tick
            or product.contract_multiplier != Decimal(multiplier)
            or product.currency != "CNY"
            or not product.quantity_unit.strip()
        ):
            raise ValueError("broker instrument economics conflict with the registered product")
        contract = session.scalar(
            select(FuturesContract).where(
                FuturesContract.product_id == product.id,
                FuturesContract.contract_code == symbol,
            )
        )
        if contract is None:
            if expected_id is not None:
                raise ValueError("saved broker contract is missing from the Data catalog")
            contract = CatalogCommands.register_contract(
                session,
                product_id=product.id,
                contract_code=symbol,
                listed_on=listed,
                last_trade_date=expires,
            )
        elif expected_id is not None and contract.id != expected_id:
            raise ValueError("saved broker contract UUID differs from the Data identity")
        elif (
            contract.last_trade_date is not None
            and contract.last_trade_date != expires
            or listed is not None
            and contract.listed_on is not None
            and contract.listed_on != listed
        ):
            raise ValueError("broker instrument dates conflict with the registered contract")
        return BrokerContract(contract.id, exchange.code, contract.contract_code)
