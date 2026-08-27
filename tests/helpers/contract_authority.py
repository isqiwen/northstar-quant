"""Typed, synthetic futures Contract Authority facts for PostgreSQL-focused tests.

These fixtures deliberately build the same immutable values used in production;
they never restore the removed YAML mapping path or make a filesystem fallback.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256

from sqlalchemy.orm import Session

from northstar_quant.application.contract_authority import FuturesContractAuthority
from northstar_quant.data.contracts.contract_master import (
    Commodity,
    Contract,
    ContractFeeSchedule,
    ContractMaster,
    ContractRuleSnapshot,
    ContractTradingSession,
    ContinuousResearchSeries,
    DeliveryRestriction,
    Exchange,
    Instrument,
    ListingState,
    RuleQualityStatus,
)
from northstar_quant.data.contracts.postgresql_contract_authority import (
    ContractMasterPublication,
    PostgresContractMasterPublicationRepository,
)
from northstar_quant.trading_execution.broker.ctp_contract_mapping import (
    CtpContractMapping,
    CtpContractRegistry,
    CtpContractRegistryPublication,
)
from northstar_quant.trading_execution.broker.postgresql_contract_registry import (
    PostgresCtpContractRegistryPublicationRepository,
)


TEST_CONTRACT_AUTHORITY_TIME = datetime(2026, 8, 27, 12, tzinfo=UTC)

_CONTRACTS = (
    ("RB", "RB2610", "SHFE", 10, 1.0, "rebar", "螺纹钢"),
    ("CU", "CU2609", "SHFE", 5, 10.0, "copper", "铜"),
    ("TA", "TA2609", "CZCE", 5, 2.0, "pta", "PTA"),
    ("SA", "SA2609", "CZCE", 20, 1.0, "soda_ash", "纯碱"),
    ("SC", "SC2609", "INE", 1000, 0.1, "crude_oil", "原油"),
)


def _sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def build_test_futures_contract_authority(
    *,
    decision_at: datetime = TEST_CONTRACT_AUTHORITY_TIME,
    authority_id: str = "cn_futures_ctp_sim",
    broker: str = "ctp_sim",
    publication_id: str = "test-contract-master-v1",
    registry_publication_id: str = "test-ctp-registry-v1",
    available_at: datetime | None = None,
    effective_from: datetime | None = None,
    enabled_symbols: tuple[str, ...] | None = None,
) -> FuturesContractAuthority:
    """Build a complete immutable master/registry pair visible at ``decision_at``."""

    decision_at = decision_at.astimezone(UTC)
    observed_at = decision_at - timedelta(minutes=2)
    available_at = (available_at or decision_at - timedelta(minutes=1)).astimezone(UTC)
    effective_from = (effective_from or decision_at - timedelta(minutes=1)).astimezone(UTC)
    configured_enabled_symbols = (
        enabled_symbols
        if enabled_symbols is not None
        else tuple(item[1] for item in _CONTRACTS)
    )
    enabled = {
        symbol.strip().upper()
        for symbol in configured_enabled_symbols
    }
    commodities = tuple(Commodity(item[5], item[6]) for item in _CONTRACTS)
    exchanges = tuple(
        Exchange(exchange_id, f"{exchange_id} test exchange", "CN", "Asia/Shanghai")
        for exchange_id in sorted({item[2] for item in _CONTRACTS})
    )
    instruments = tuple(
        Instrument(
            f"{exchange.lower()}.{product.lower()}",
            commodity_id,
            exchange,
            product,
        )
        for product, _symbol, exchange, _multiple, _tick, commodity_id, _name in _CONTRACTS
    )
    continuous_series = tuple(
        ContinuousResearchSeries(
            f"{exchange.lower()}.{product.lower()}.cont",
            f"{exchange.lower()}.{product.lower()}",
            f"{product}_CONT",
        )
        for product, _symbol, exchange, _multiple, _tick, _commodity_id, _name in _CONTRACTS
    )
    contracts = tuple(
        Contract(
            f"{exchange.lower()}.{symbol.lower()}",
            f"{exchange.lower()}.{product.lower()}",
            symbol,
            date(2025, 1, 1),
            date(2027, 12, 31),
        )
        for product, symbol, exchange, _multiple, _tick, _commodity_id, _name in _CONTRACTS
    )
    snapshots = tuple(
        ContractRuleSnapshot.create(
            snapshot_id=f"{exchange.lower()}.{symbol.lower()}.rule-v1",
            contract_id=f"{exchange.lower()}.{symbol.lower()}",
            observed_at=observed_at,
            available_at=available_at,
            effective_from=effective_from,
            effective_until=None,
            listing_state=ListingState.LISTED,
            expires_on=date(2027, 12, 31),
            multiplier=float(multiple),
            tick_size=tick,
            initial_margin_rate=0.1,
            fees=ContractFeeSchedule(
                open_per_lot=1.0,
                open_rate=0.0,
                close_per_lot=1.0,
                close_rate=0.0,
                close_today_per_lot=1.0,
                close_today_rate=0.0,
            ),
            lower_price_limit=1.0,
            upper_price_limit=1_000_000.0,
            sessions=(
                ContractTradingSession("night", time(21), time(2, 30)),
                ContractTradingSession("day", time(9), time(15)),
            ),
            delivery_restriction=DeliveryRestriction.NONE,
            source_artifact_hash=_sha256(f"rule-source:{symbol}"),
            source_authority="test_exchange_notice",
            quality_status=RuleQualityStatus.PASS,
            execution_eligible=True,
        )
        for product, symbol, exchange, multiple, tick, _commodity_id, _name in _CONTRACTS
    )
    master = ContractMaster(
        master_id="cn-futures-test",
        version="test-v1",
        commodities=commodities,
        exchanges=exchanges,
        instruments=instruments,
        continuous_series=continuous_series,
        contracts=contracts,
        rule_snapshots=snapshots,
    )
    master_publication = ContractMasterPublication(
        authority_id=authority_id,
        publication_id=publication_id,
        observed_at=observed_at,
        available_at=available_at,
        source_artifact_hash=_sha256(f"master-source:{publication_id}"),
        source_authority="test_contract_authority",
        master=master,
    )
    registry = CtpContractRegistry(
        version=1,
        broker=broker,
        contracts=tuple(
            CtpContractMapping(
                continuous_symbol=f"{product}_CONT",
                data_symbol=symbol,
                instrument_id=symbol.lower(),
                exchange_id=exchange,
                product_id=product.lower(),
                volume_multiple=multiple,
                price_tick=tick,
                trading_enabled=symbol in enabled,
            )
            for product, symbol, exchange, multiple, tick, _commodity_id, _name in _CONTRACTS
        ),
    )
    registry_publication = CtpContractRegistryPublication(
        authority_id=authority_id,
        publication_id=registry_publication_id,
        master_publication_hash=master_publication.publication_hash,
        observed_at=observed_at,
        available_at=available_at,
        effective_from=effective_from,
        effective_until=None,
        source_artifact_hash=_sha256(f"registry-source:{registry_publication_id}"),
        source_authority="test_ctp_registry_authority",
        registry=registry,
    )
    return FuturesContractAuthority(
        decision_at=decision_at,
        master_publication=master_publication,
        registry_publication=registry_publication,
    )


def publish_test_futures_contract_authority(
    session: Session,
    authority: FuturesContractAuthority,
) -> FuturesContractAuthority:
    """Append a test authority pair atomically through production repositories."""

    PostgresContractMasterPublicationRepository(session).publish(
        authority.master_publication,
        commit=False,
    )
    PostgresCtpContractRegistryPublicationRepository(session).publish(
        authority.registry_publication,
        commit=False,
    )
    session.commit()
    return authority


__all__ = [
    "TEST_CONTRACT_AUTHORITY_TIME",
    "build_test_futures_contract_authority",
    "publish_test_futures_contract_authority",
]
