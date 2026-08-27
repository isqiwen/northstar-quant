from datetime import UTC, datetime, timedelta
import hashlib

import pytest

from northstar_quant.trading_execution.broker.ctp_contract_mapping import (
    CtpContractMapping,
    CtpContractMappingError,
    CtpContractRegistry,
    CtpContractRegistryPublication,
)


UTC_TIME = datetime(2026, 7, 1, 8, tzinfo=UTC)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mapping(
    *,
    continuous_symbol: str = "RB_CONT",
    data_symbol: str = "RB2610",
    instrument_id: str = "rb2610",
    exchange_id: str = "SHFE",
    product_id: str = "rb",
    volume_multiple: int = 10,
    price_tick: float = 1.0,
    trading_enabled: bool = True,
) -> CtpContractMapping:
    return CtpContractMapping(
        continuous_symbol=continuous_symbol,
        data_symbol=data_symbol,
        instrument_id=instrument_id,
        exchange_id=exchange_id,
        product_id=product_id,
        volume_multiple=volume_multiple,
        price_tick=price_tick,
        trading_enabled=trading_enabled,
    )


def _registry(*contracts: CtpContractMapping) -> CtpContractRegistry:
    return CtpContractRegistry(version=1, broker="ctp", contracts=contracts)


def test_typed_registry_rejects_disabled_contracts() -> None:
    registry = _registry(_mapping(trading_enabled=False))

    with pytest.raises(CtpContractMappingError, match="CTP_CONTINUOUS_CONTRACT_FORBIDDEN"):
        registry.resolve_continuous("rb_cont")
    with pytest.raises(CtpContractMappingError, match="CTP_CONTRACT_DISABLED"):
        registry.resolve_data_symbol("RB2610")
    with pytest.raises(CtpContractMappingError, match="CTP_CONTRACT_DISABLED"):
        registry.resolve_ctp_identity("rb2610", "SHFE")


def test_typed_registry_requires_unambiguous_contract_identity() -> None:
    with pytest.raises(CtpContractMappingError, match="duplicate data mapping"):
        _registry(
            _mapping(),
            _mapping(
                continuous_symbol="I_CONT",
                instrument_id="RB2610",
            ),
        )


def test_typed_registry_resolves_enabled_reverse_identity() -> None:
    registry = _registry(
        _mapping(
            continuous_symbol="M_CONT",
            data_symbol="M2610",
            instrument_id="m2610",
            exchange_id="DCE",
            product_id="m",
        )
    )

    mapping = registry.resolve_ctp_identity("M2610", "DCE")

    assert mapping.data_symbol == "M2610"
    assert mapping.volume_multiple == 10
    assert mapping.continuous_symbol == "M_CONT"
    with pytest.raises(CtpContractMappingError, match="CTP_CONTINUOUS_CONTRACT_FORBIDDEN"):
        registry.resolve_continuous("m_cont")


def test_typed_registry_public_resolution_apis_reject_unknown_contracts() -> None:
    registry = _registry(_mapping())

    with pytest.raises(CtpContractMappingError, match="CTP_CONTINUOUS_CONTRACT_FORBIDDEN"):
        registry.resolve_continuous("CU_CONT")
    with pytest.raises(CtpContractMappingError, match="CTP_CONTRACT_NOT_CONFIGURED"):
        registry.resolve_data_symbol("CU2610")
    with pytest.raises(CtpContractMappingError, match="CTP_CONTRACT_NOT_CONFIGURED"):
        registry.resolve_ctp_identity("cu2610", "SHFE")


@pytest.mark.parametrize(
    ("kwargs", "error_message"),
    [
        (
            {"data_symbol": "RB_CONT", "instrument_id": "rb_cont"},
            "data_symbol cannot be a continuous research symbol",
        ),
        (
            {"data_symbol": "RB2610", "instrument_id": "rb2602"},
            "data_symbol must exactly match instrument_id",
        ),
        (
            {"product_id": "i"},
            "product_id must be the data symbol and instrument prefix",
        ),
    ],
)
def test_typed_mapping_rejects_non_executable_or_inconsistent_identity(
    kwargs: dict[str, object],
    error_message: str,
) -> None:
    with pytest.raises(CtpContractMappingError, match=error_message):
        _mapping(**kwargs)  # type: ignore[arg-type]


def test_registry_publication_binds_typed_registry_to_master_publication() -> None:
    registry = _registry(_mapping())
    publication = CtpContractRegistryPublication(
        authority_id="cn_futures_ctp_sim",
        publication_id="ctp-sim-20260701",
        master_publication_hash=_hash("master-20260701"),
        observed_at=UTC_TIME,
        available_at=UTC_TIME + timedelta(minutes=5),
        effective_from=UTC_TIME + timedelta(minutes=5),
        effective_until=None,
        source_artifact_hash=_hash("ctp-sim-source"),
        source_authority="ctp_sim_contract_authority",
        registry=registry,
    )

    assert publication.registry is registry
    assert publication.content_hash == registry.fingerprint
    assert publication.payload_json == (
        '{"broker":"ctp","contracts":[{"continuous_symbol":"RB_CONT",'
        '"data_symbol":"RB2610","exchange_id":"SHFE",'
        '"instrument_id":"rb2610","price_tick":1.0,"product_id":"rb",'
        '"trading_enabled":true,"volume_multiple":10}],"version":1}'
    )
    assert publication.publication_hash == CtpContractRegistryPublication(
        authority_id="cn_futures_ctp_sim",
        publication_id="ctp-sim-20260701",
        master_publication_hash=_hash("master-20260701"),
        observed_at=UTC_TIME,
        available_at=UTC_TIME + timedelta(minutes=5),
        effective_from=UTC_TIME + timedelta(minutes=5),
        effective_until=None,
        source_artifact_hash=_hash("ctp-sim-source"),
        source_authority="ctp_sim_contract_authority",
        registry=registry,
    ).publication_hash


def test_registry_publication_rejects_future_or_untrusted_authority_evidence() -> None:
    registry = _registry(_mapping())

    with pytest.raises(CtpContractMappingError, match="available_at cannot precede observed_at"):
        CtpContractRegistryPublication(
            authority_id="cn_futures_ctp_sim",
            publication_id="ctp-sim-invalid-time",
            master_publication_hash=_hash("master-20260701"),
            observed_at=UTC_TIME,
            available_at=UTC_TIME - timedelta(seconds=1),
            effective_from=UTC_TIME,
            effective_until=None,
            source_artifact_hash=_hash("ctp-sim-source"),
            source_authority="ctp_sim_contract_authority",
            registry=registry,
        )

    with pytest.raises(CtpContractMappingError, match="SHA-256"):
        CtpContractRegistryPublication(
            authority_id="cn_futures_ctp_sim",
            publication_id="ctp-sim-invalid-source",
            master_publication_hash="not-a-hash",
            observed_at=UTC_TIME,
            available_at=UTC_TIME,
            effective_from=UTC_TIME,
            effective_until=None,
            source_artifact_hash=_hash("ctp-sim-source"),
            source_authority="ctp_sim_contract_authority",
            registry=registry,
        )
