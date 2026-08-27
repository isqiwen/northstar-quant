"""Typed CTP broker-registry values and PostgreSQL authority publications.

Mappings are runtime facts only when replayed from PostgreSQL at a caller's
explicit decision time. This module has no YAML loader, path setting, or
fallback. A registry publication is bound to the exact Master publication
hash that Application composes alongside it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import math
import re
from typing import NoReturn, cast

from northstar_quant.foundation.common.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
    require_sha256,
)


__all__ = [
    "CTP_CONTRACT_REGISTRY_PUBLICATION_SCHEMA_VERSION",
    "CtpContractMapping",
    "CtpContractMappingError",
    "CtpContractRegistry",
    "CtpContractRegistryPublication",
]


CTP_CONTRACT_REGISTRY_PUBLICATION_SCHEMA_VERSION = 1
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_INSTRUMENT_ID_RE = re.compile(r"^[A-Za-z]+\d{3,4}$")
_EXCHANGES = frozenset({"SHFE", "DCE", "CZCE", "CFFEX", "INE", "GFEX"})
_MAX_TEXT_LENGTH = 256


class CtpContractMappingError(ValueError):
    """CTP contract mapping or authority publication is invalid."""


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value.strip()) is None:
        raise CtpContractMappingError(f"{field_name} must be a stable identifier")
    return value.strip()


def _text(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\x00" in value
        or "\r" in value
        or "\n" in value
        or len(value.strip()) > _MAX_TEXT_LENGTH
    ):
        raise CtpContractMappingError(f"{field_name} must be bounded single-line text")
    return value.strip()


def _hash(value: object, field_name: str) -> str:
    try:
        return require_sha256(cast(str, value), field_name=field_name)
    except FingerprintError as exc:
        raise CtpContractMappingError(str(exc)) from exc


def _utc_time(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CtpContractMappingError(f"{field_name} must be timezone aware")
    return value.astimezone(UTC)


def _data_symbol(value: object, field_name: str) -> str:
    return _text(value, field_name).upper()


def _instrument_id(value: object, field_name: str) -> str:
    result = _text(value, field_name).lower()
    if _INSTRUMENT_ID_RE.fullmatch(result) is None:
        raise CtpContractMappingError(f"{field_name} must be a concrete CTP contract")
    return result


def _exchange_id(value: object, field_name: str) -> str:
    result = _text(value, field_name).upper()
    if result not in _EXCHANGES:
        raise CtpContractMappingError(f"{field_name} is not a supported CTP exchange")
    return result


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CtpContractMappingError(f"{field_name} must be a positive integer")
    return value


def _positive_float(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CtpContractMappingError(f"{field_name} must be a positive finite number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise CtpContractMappingError(f"{field_name} must be a positive finite number")
    return result


@dataclass(frozen=True, slots=True)
class CtpContractMapping:
    """One explicit actual-contract identity mapping for CTP or ctp_sim."""

    continuous_symbol: str
    data_symbol: str
    instrument_id: str
    exchange_id: str
    product_id: str
    volume_multiple: int
    price_tick: float
    trading_enabled: bool

    def __post_init__(self) -> None:
        continuous_symbol = _data_symbol(self.continuous_symbol, "continuous_symbol")
        if not continuous_symbol.endswith("_CONT"):
            raise CtpContractMappingError(
                "continuous_symbol must be a continuous research symbol"
            )
        data_symbol = _data_symbol(self.data_symbol, "data_symbol")
        if data_symbol.endswith("_CONT"):
            raise CtpContractMappingError(
                "data_symbol cannot be a continuous research symbol"
            )
        instrument_id = _instrument_id(self.instrument_id, "instrument_id")
        if data_symbol.lower() != instrument_id:
            raise CtpContractMappingError(
                "data_symbol must exactly match instrument_id ignoring case"
            )
        product_id = _text(self.product_id, "product_id").lower()
        if not product_id.isalpha() or not data_symbol.lower().startswith(product_id):
            raise CtpContractMappingError(
                "product_id must be the data symbol and instrument prefix"
            )
        exchange_id = _exchange_id(self.exchange_id, "exchange_id")
        if type(self.trading_enabled) is not bool:
            raise CtpContractMappingError("trading_enabled must be boolean")
        object.__setattr__(self, "continuous_symbol", continuous_symbol)
        object.__setattr__(self, "data_symbol", data_symbol)
        object.__setattr__(self, "instrument_id", instrument_id)
        object.__setattr__(self, "exchange_id", exchange_id)
        object.__setattr__(self, "product_id", product_id)
        object.__setattr__(
            self,
            "volume_multiple",
            _positive_int(self.volume_multiple, "volume_multiple"),
        )
        object.__setattr__(self, "price_tick", _positive_float(self.price_tick, "price_tick"))

    def require_trading_enabled(self) -> "CtpContractMapping":
        """Confirm the mapping was explicitly enabled in its authority release."""

        if not self.trading_enabled:
            raise CtpContractMappingError(
                f"CTP_CONTRACT_DISABLED: {self.data_symbol} is not enabled."
            )
        return self


@dataclass(frozen=True, slots=True)
class CtpContractRegistry:
    """Immutable broker registry projected from a PostgreSQL publication."""

    version: int
    broker: str
    contracts: tuple[CtpContractMapping, ...]

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version != 1:
            raise CtpContractMappingError("CTP registry version must be integer 1")
        broker = _text(self.broker, "broker").lower()
        if broker not in {"ctp", "ctp_sim"}:
            raise CtpContractMappingError("broker must be ctp or ctp_sim")
        if not isinstance(self.contracts, tuple) or not all(
            type(item) is CtpContractMapping for item in self.contracts
        ):
            raise CtpContractMappingError("contracts must be a CtpContractMapping tuple")
        _validate_unique(self.contracts)
        object.__setattr__(self, "broker", broker)
        object.__setattr__(
            self,
            "contracts",
            tuple(sorted(self.contracts, key=lambda item: item.data_symbol)),
        )

    @property
    def fingerprint(self) -> str:
        return canonical_json_sha256(_registry_payload(self))

    def resolve_continuous(self, continuous_symbol: str) -> NoReturn:
        normalized = _data_symbol(continuous_symbol, "continuous_symbol")
        raise CtpContractMappingError(
            "CTP_CONTINUOUS_CONTRACT_FORBIDDEN: "
            f"{normalized} cannot resolve to a broker order identity."
        )

    def resolve_data_symbol(self, data_symbol: str) -> CtpContractMapping:
        normalized = _data_symbol(data_symbol, "data_symbol")
        for contract in self.contracts:
            if contract.data_symbol == normalized:
                return contract.require_trading_enabled()
        raise CtpContractMappingError(
            f"CTP_CONTRACT_NOT_CONFIGURED: {normalized} is not in the registry."
        )

    def resolve_ctp_identity(
        self,
        instrument_id: str,
        exchange_id: str,
    ) -> CtpContractMapping:
        normalized_instrument = _instrument_id(instrument_id, "instrument_id")
        normalized_exchange = _exchange_id(exchange_id, "exchange_id")
        for contract in self.contracts:
            if (
                contract.instrument_id == normalized_instrument
                and contract.exchange_id == normalized_exchange
            ):
                return contract.require_trading_enabled()
        raise CtpContractMappingError(
            "CTP_CONTRACT_NOT_CONFIGURED: "
            f"{normalized_exchange}/{normalized_instrument} is not in the registry."
        )


def _validate_unique(contracts: tuple[CtpContractMapping, ...]) -> None:
    continuous_symbols: set[str] = set()
    data_symbols: set[str] = set()
    identities: set[tuple[str, str]] = set()
    for contract in contracts:
        if contract.continuous_symbol in continuous_symbols:
            raise CtpContractMappingError(
                f"duplicate continuous mapping: {contract.continuous_symbol}"
            )
        if contract.data_symbol in data_symbols:
            raise CtpContractMappingError(
                f"duplicate data mapping: {contract.data_symbol}"
            )
        identity = (contract.exchange_id, contract.instrument_id)
        if identity in identities:
            raise CtpContractMappingError(
                f"duplicate CTP identity: {contract.exchange_id}/{contract.instrument_id}"
            )
        continuous_symbols.add(contract.continuous_symbol)
        data_symbols.add(contract.data_symbol)
        identities.add(identity)


def _registry_payload(registry: CtpContractRegistry) -> dict[str, object]:
    return {
        "broker": registry.broker,
        "contracts": [
            {
                "continuous_symbol": item.continuous_symbol,
                "data_symbol": item.data_symbol,
                "exchange_id": item.exchange_id,
                "instrument_id": item.instrument_id,
                "price_tick": item.price_tick,
                "product_id": item.product_id,
                "trading_enabled": item.trading_enabled,
                "volume_multiple": item.volume_multiple,
            }
            for item in registry.contracts
        ],
        "version": registry.version,
    }


_REGISTRY_FIELDS = frozenset({"broker", "contracts", "version"})
_MAPPING_FIELDS = frozenset(
    {
        "continuous_symbol",
        "data_symbol",
        "exchange_id",
        "instrument_id",
        "price_tick",
        "product_id",
        "trading_enabled",
        "volume_multiple",
    }
)


def _object(value: object, field_name: str, expected: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CtpContractMappingError(f"{field_name} must be an object")
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unknown:
            details.append("unknown=" + ",".join(unknown))
        raise CtpContractMappingError(f"{field_name} fields invalid: {'; '.join(details)}")
    return {str(key): item for key, item in value.items()}


def _array(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise CtpContractMappingError(f"{field_name} must be a list")
    return value


def _registry_from_payload(payload: object) -> CtpContractRegistry:
    root = _object(payload, "ctp_registry_payload", _REGISTRY_FIELDS)
    raw_version = root["version"]
    if isinstance(raw_version, bool) or not isinstance(raw_version, int):
        raise CtpContractMappingError("registry version must be an integer")
    return CtpContractRegistry(
        version=raw_version,
        broker=_text(root["broker"], "broker"),
        contracts=tuple(
            _mapping_from_payload(item, index)
            for index, item in enumerate(_array(root["contracts"], "contracts"))
        ),
    )


def _mapping_from_payload(value: object, index: int) -> CtpContractMapping:
    item = _object(value, f"contracts[{index}]", _MAPPING_FIELDS)
    return CtpContractMapping(
        continuous_symbol=_text(item["continuous_symbol"], f"contracts[{index}].continuous_symbol"),
        data_symbol=_text(item["data_symbol"], f"contracts[{index}].data_symbol"),
        instrument_id=_text(item["instrument_id"], f"contracts[{index}].instrument_id"),
        exchange_id=_text(item["exchange_id"], f"contracts[{index}].exchange_id"),
        product_id=_text(item["product_id"], f"contracts[{index}].product_id"),
        volume_multiple=_positive_int(
            item["volume_multiple"],
            f"contracts[{index}].volume_multiple",
        ),
        price_tick=_positive_float(item["price_tick"], f"contracts[{index}].price_tick"),
        trading_enabled=_bool(item["trading_enabled"], f"contracts[{index}].trading_enabled"),
    )


def _bool(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise CtpContractMappingError(f"{field_name} must be boolean")
    return value


def _payload_json(registry: CtpContractRegistry) -> str:
    try:
        return json.dumps(
            _registry_payload(registry),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CtpContractMappingError("CTP_REGISTRY_PAYLOAD_NOT_CANONICAL") from exc


@dataclass(frozen=True, slots=True)
class CtpContractRegistryPublication:
    """One immutable broker registry release bound to a Master publication."""

    authority_id: str
    publication_id: str
    master_publication_hash: str
    observed_at: datetime
    available_at: datetime
    effective_from: datetime
    effective_until: datetime | None
    source_artifact_hash: str
    source_authority: str
    registry: CtpContractRegistry
    schema_version: int = CTP_CONTRACT_REGISTRY_PUBLICATION_SCHEMA_VERSION
    content_hash: str = field(init=False)
    publication_hash: str = field(init=False)

    def __post_init__(self) -> None:
        authority_id = _identifier(self.authority_id, "authority_id")
        publication_id = _identifier(self.publication_id, "publication_id")
        master_publication_hash = _hash(
            self.master_publication_hash,
            "master_publication_hash",
        )
        observed_at = _utc_time(self.observed_at, "observed_at")
        available_at = _utc_time(self.available_at, "available_at")
        effective_from = _utc_time(self.effective_from, "effective_from")
        effective_until = (
            _utc_time(self.effective_until, "effective_until")
            if self.effective_until is not None
            else None
        )
        if available_at < observed_at:
            raise CtpContractMappingError("available_at cannot precede observed_at")
        if effective_until is not None and effective_until <= effective_from:
            raise CtpContractMappingError("registry effective window is invalid")
        source_artifact_hash = _hash(self.source_artifact_hash, "source_artifact_hash")
        source_authority = _text(self.source_authority, "source_authority")
        if type(self.registry) is not CtpContractRegistry:
            raise CtpContractMappingError("registry must be a CtpContractRegistry")
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != CTP_CONTRACT_REGISTRY_PUBLICATION_SCHEMA_VERSION
        ):
            raise CtpContractMappingError("CTP_REGISTRY_PUBLICATION_SCHEMA_UNSUPPORTED")
        payload = _registry_payload(self.registry)
        content_hash = canonical_json_sha256(payload)
        publication_hash = canonical_json_sha256(
            {
                "authority_id": authority_id,
                "available_at": available_at.isoformat(),
                "broker": self.registry.broker,
                "content_hash": content_hash,
                "effective_from": effective_from.isoformat(),
                "effective_until": (
                    effective_until.isoformat() if effective_until is not None else None
                ),
                "format": "northstar.ctp-contract-registry-publication.v1",
                "master_publication_hash": master_publication_hash,
                "observed_at": observed_at.isoformat(),
                "publication_id": publication_id,
                "quality_status": "pass",
                "registry_fingerprint": self.registry.fingerprint,
                "schema_version": self.schema_version,
                "source_artifact_hash": source_artifact_hash,
                "source_authority": source_authority,
            }
        )
        object.__setattr__(self, "authority_id", authority_id)
        object.__setattr__(self, "publication_id", publication_id)
        object.__setattr__(self, "master_publication_hash", master_publication_hash)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "effective_from", effective_from)
        object.__setattr__(self, "effective_until", effective_until)
        object.__setattr__(self, "source_artifact_hash", source_artifact_hash)
        object.__setattr__(self, "source_authority", source_authority)
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "publication_hash", publication_hash)

    @property
    def payload_json(self) -> str:
        """Return the canonical registry payload stored in PostgreSQL."""

        return _payload_json(self.registry)
