"""Broker facts resolve one Data identity without inventing product metadata."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from northstar_quant.data.broker import resolve_broker_contract, verify_broker_contract
from northstar_quant.data.catalog.models import Exchange, FuturesContract, FuturesProduct
from northstar_quant.data.catalog.services import CatalogCommands


def _instrument(**changes: object) -> dict[str, object]:
    return {
        "ExchangeID": "SHFE",
        "ProductClass": "1",
        "InstrumentID": "rb2610",
        "ProductID": "rb",
        "DeliveryYear": 2026,
        "DeliveryMonth": 10,
        "OpenDate": "20251016",
        "ExpireDate": "20261015",
        "PriceTick": "1.0",
        "VolumeMultiple": 10,
        **changes,
    }


def _product(engine: Engine) -> UUID:
    with Session(engine) as session, session.begin():
        exchange = CatalogCommands.register_exchange(
            session, code="SHFE", name="Synthetic exchange", timezone_name="Asia/Shanghai"
        )
        product = CatalogCommands.register_product(
            session,
            exchange_id=exchange.id,
            code="RB",
            name="Synthetic product",
            price_tick=Decimal("1"),
            contract_multiplier=Decimal("10"),
            quantity_unit="TON",
        )
        return product.id


def test_concurrent_registration_reuses_one_canonical_contract(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    _product(postgres_engine)
    with ThreadPoolExecutor(max_workers=4) as executor:
        identities = list(
            executor.map(
                lambda _: resolve_broker_contract(postgres_engine, _instrument()), range(8)
            )
        )
    assert all(item == identities[0] for item in identities)
    assert identities[0].exchange == "SHFE" and identities[0].symbol == "RB2610"
    with Session(postgres_engine) as session:
        contract = session.get(FuturesContract, identities[0].contract_id)
        assert contract is not None
        assert contract.listed_on == date(2025, 10, 16)
        assert contract.last_trade_date == date(2026, 10, 15)
        assert session.scalar(select(func.count()).select_from(FuturesContract)) == 1


def test_existing_identity_is_reused_without_filling_unknown_catalog_dates(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    product_id = _product(postgres_engine)
    with Session(postgres_engine) as session, session.begin():
        existing = CatalogCommands.register_contract(
            session, product_id=product_id, contract_code="RB2610"
        )
        expected_id = existing.id
    resolved = resolve_broker_contract(postgres_engine, _instrument())
    assert resolved.contract_id == expected_id
    with Session(postgres_engine) as session:
        existing = session.get(FuturesContract, expected_id)
        assert existing is not None
        assert existing.listed_on is None and existing.last_trade_date is None


def test_conflicting_observations_never_replace_existing_product_or_contract(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    product_id = _product(postgres_engine)
    original = resolve_broker_contract(postgres_engine, _instrument())
    for changes in (
        {"PriceTick": "0.5"},
        {"VolumeMultiple": 11},
        {"OpenDate": "20251017"},
        {"ExpireDate": "20261016"},
    ):
        with pytest.raises(ValueError, match="conflict"):
            resolve_broker_contract(postgres_engine, _instrument(**changes))
    assert resolve_broker_contract(postgres_engine, _instrument(OpenDate=None)) == original
    with Session(postgres_engine) as session:
        product = session.get(FuturesProduct, product_id)
        assert product is not None
        assert product.price_tick == 1 and product.contract_multiplier == 10
        contract = session.get(FuturesContract, original.contract_id)
        assert contract is not None
        assert contract.listed_on == date(2025, 10, 16)
        assert contract.last_trade_date == date(2026, 10, 15)


def test_missing_product_metadata_and_unsupported_or_incomplete_instruments_are_rejected(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    with pytest.raises(ValueError, match="registered SHFE"):
        resolve_broker_contract(postgres_engine, _instrument())
    _product(postgres_engine)
    with pytest.raises(ValueError, match="quantity unit"):
        resolve_broker_contract(postgres_engine, _instrument(InstrumentID="cu2610", ProductID="cu"))
    for changes in (
        {"ExchangeID": "DCE"},
        {"ProductClass": "2"},
        {"ProductID": None},
        {"DeliveryYear": None},
        {"DeliveryMonth": 11},
        {"ExpireDate": None},
        {"ExpireDate": "20260230"},
        {"PriceTick": None},
        {"PriceTick": "NaN"},
        {"PriceTick": 1.0},
        {"VolumeMultiple": None},
    ):
        with pytest.raises(ValueError):
            resolve_broker_contract(postgres_engine, _instrument(**changes))
    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(FuturesContract)) == 0
        assert session.scalar(select(func.count()).select_from(FuturesProduct)) == 1
        assert session.scalar(select(func.count()).select_from(Exchange)) == 1


def test_read_verification_never_registers_missing_or_different_contracts(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    _product(postgres_engine)
    with pytest.raises(ValueError, match="missing"):
        verify_broker_contract(postgres_engine, uuid4(), _instrument())
    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(FuturesContract)) == 0
    original = resolve_broker_contract(postgres_engine, _instrument())
    assert verify_broker_contract(postgres_engine, original.contract_id, _instrument()) == original
    with pytest.raises(ValueError, match="UUID"):
        verify_broker_contract(postgres_engine, uuid4(), _instrument())
    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(FuturesContract)) == 1


def test_read_verification_rejects_catalog_drift_without_repair(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    product_id = _product(postgres_engine)
    original = resolve_broker_contract(postgres_engine, _instrument())
    with Session(postgres_engine) as session, session.begin():
        product = session.get(FuturesProduct, product_id)
        assert product is not None
        product.price_tick = Decimal("2")
    with pytest.raises(ValueError, match="economics conflict"):
        verify_broker_contract(postgres_engine, original.contract_id, _instrument())
    with Session(postgres_engine) as session, session.begin():
        product = session.get(FuturesProduct, product_id)
        assert product is not None and product.price_tick == 2
        product.price_tick = Decimal("1")
        contract = session.get(FuturesContract, original.contract_id)
        assert contract is not None
        contract.last_trade_date = date(2026, 10, 16)
    with pytest.raises(ValueError, match="dates conflict"):
        verify_broker_contract(postgres_engine, original.contract_id, _instrument())
    with Session(postgres_engine) as session, session.begin():
        contract = session.get(FuturesContract, original.contract_id)
        assert contract is not None and contract.last_trade_date == date(2026, 10, 16)
        contract.contract_code = "RB2611"
    with pytest.raises(ValueError, match="missing"):
        verify_broker_contract(postgres_engine, original.contract_id, _instrument())
    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(FuturesContract)) == 1
        contract = session.get(FuturesContract, original.contract_id)
        assert contract is not None and contract.contract_code == "RB2611"
