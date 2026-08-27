"""Application composition for the PostgreSQL Contract Authority.

This is the only cross-domain boundary that combines a Data Contract Master
publication and a Trading CTP registry publication. It binds both facts to the
same explicit decision time and rejects any mixed, future, missing, or
rule-inconsistent release before a caller can plan or submit new risk.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from northstar_quant.data.artifacts.fingerprints import canonical_json_sha256
from northstar_quant.data.contracts.contract_master import (
    ContractMaster,
    ContractMasterError,
)
from northstar_quant.data.contracts.postgresql_contract_authority import (
    ContractAuthorityError,
    ContractMasterPublication,
    PostgresContractMasterPublicationRepository,
)
from northstar_quant.foundation.db.session import SessionLocal
from northstar_quant.trading_execution.broker.ctp_contract_mapping import (
    CtpContractMappingError,
    CtpContractRegistry,
    CtpContractRegistryPublication,
)
from northstar_quant.trading_execution.broker.postgresql_contract_registry import (
    PostgresCtpContractRegistryPublicationRepository,
)


__all__ = [
    "FuturesContractAuthority",
    "FuturesContractAuthorityError",
    "resolve_futures_contract_authority",
]


class FuturesContractAuthorityError(PermissionError):
    """A Master and broker registry cannot prove a safe futures contract fact."""


def _time(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise FuturesContractAuthorityError(f"{field_name} must be timezone aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class FuturesContractAuthority:
    """One coherent, typed Master and registry replay for a broker decision."""

    decision_at: datetime
    master_publication: ContractMasterPublication
    registry_publication: CtpContractRegistryPublication
    authority_hash: str = field(init=False)

    def __post_init__(self) -> None:
        decision_at = _time(self.decision_at, "decision_at")
        if type(self.master_publication) is not ContractMasterPublication:
            raise FuturesContractAuthorityError("CONTRACT_MASTER_PUBLICATION_REQUIRED")
        if type(self.registry_publication) is not CtpContractRegistryPublication:
            raise FuturesContractAuthorityError("CTP_REGISTRY_PUBLICATION_REQUIRED")
        master_publication = self.master_publication
        registry_publication = self.registry_publication
        if (
            master_publication.authority_id != registry_publication.authority_id
            or master_publication.publication_hash
            != registry_publication.master_publication_hash
        ):
            raise FuturesContractAuthorityError("CONTRACT_AUTHORITY_PUBLICATION_BINDING_MISMATCH")
        if (
            master_publication.available_at > decision_at
            or registry_publication.available_at > decision_at
            or registry_publication.effective_from > decision_at
            or (
                registry_publication.effective_until is not None
                and registry_publication.effective_until <= decision_at
            )
        ):
            raise FuturesContractAuthorityError("CONTRACT_AUTHORITY_FUTURE_OR_EXPIRED")
        _validate_registry_against_master(
            master_publication.master,
            registry_publication.registry,
            decision_at=decision_at,
        )
        authority_hash = canonical_json_sha256(
            {
                "decision_at": decision_at.isoformat(),
                "format": "northstar.futures-contract-authority.v1",
                "master_publication_hash": master_publication.publication_hash,
                "registry_publication_hash": registry_publication.publication_hash,
            }
        )
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "authority_hash", authority_hash)

    @property
    def authority_id(self) -> str:
        return self.master_publication.authority_id

    @property
    def master(self) -> ContractMaster:
        return self.master_publication.master

    @property
    def registry(self) -> CtpContractRegistry:
        return self.registry_publication.registry


def _validate_registry_against_master(
    master: ContractMaster,
    registry: CtpContractRegistry,
    *,
    decision_at: datetime,
) -> None:
    """Bind each enabled mapping to an actual, currently executable rule fact."""

    try:
        instruments = {item.instrument_id: item for item in master.instruments}
        for mapping in registry.contracts:
            contract = next(
                (
                    item
                    for item in master.contracts
                    if item.symbol == mapping.data_symbol
                ),
                None,
            )
            if contract is None:
                raise FuturesContractAuthorityError(
                    "CONTRACT_AUTHORITY_MAPPING_CONTRACT_UNKNOWN"
                )
            instrument = instruments.get(contract.instrument_id)
            if instrument is None:
                raise FuturesContractAuthorityError(
                    "CONTRACT_AUTHORITY_MAPPING_INSTRUMENT_UNKNOWN"
                )
            if (
                mapping.instrument_id != contract.symbol.lower()
                or mapping.exchange_id != instrument.exchange_id.upper()
                or mapping.product_id != instrument.product_code.lower()
            ):
                raise FuturesContractAuthorityError(
                    "CONTRACT_AUTHORITY_MAPPING_IDENTITY_MISMATCH"
                )
            resolution = master.resolve_for_execution(
                mapping.data_symbol,
                decision_at=decision_at,
            )
            _contract, rules = master.require_execution_contract(resolution)
            if (
                float(mapping.volume_multiple) != float(rules.multiplier)
                or float(mapping.price_tick) != float(rules.tick_size)
            ):
                raise FuturesContractAuthorityError(
                    "CONTRACT_AUTHORITY_MAPPING_RULE_MISMATCH"
                )
    except ContractMasterError as exc:
        raise FuturesContractAuthorityError(
            "CONTRACT_AUTHORITY_MAPPING_RULE_UNRESOLVED"
        ) from exc
    except CtpContractMappingError as exc:
        raise FuturesContractAuthorityError(
            "CONTRACT_AUTHORITY_REGISTRY_INVALID"
        ) from exc


def resolve_futures_contract_authority(
    authority_id: str,
    *,
    broker: str,
    decision_at: datetime,
    session_factory: Callable[[], Session] = SessionLocal,
) -> FuturesContractAuthority:
    """Resolve one database-only Master/CTP authority replay."""

    normalized_time = _time(decision_at, "decision_at")
    with session_factory() as session:
        try:
            master = PostgresContractMasterPublicationRepository(session).load_at(
                authority_id,
                decision_at=normalized_time,
            )
            registry = PostgresCtpContractRegistryPublicationRepository(session).load_at(
                authority_id,
                broker=broker,
                decision_at=normalized_time,
            )
            return FuturesContractAuthority(
                decision_at=normalized_time,
                master_publication=master,
                registry_publication=registry,
            )
        except (ContractAuthorityError, CtpContractMappingError) as exc:
            raise FuturesContractAuthorityError(
                "CONTRACT_AUTHORITY_UNAVAILABLE"
            ) from exc
