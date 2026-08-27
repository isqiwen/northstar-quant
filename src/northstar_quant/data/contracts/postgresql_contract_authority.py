"""PostgreSQL-backed immutable Contract Master authority publications.

The database is the sole runtime authority. A publication stores a canonical
serialization of the frozen ContractMaster aggregate together with source
evidence and point-in-time availability. This Data module does not know about
CTP or orders; Application composes a selected Master with a broker registry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
import json
import math
import re
from typing import cast

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from northstar_quant.data.artifacts.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
    require_sha256,
)
from northstar_quant.data.contracts.contract_master import (
    Commodity,
    Contract,
    ContractFeeSchedule,
    ContractMaster,
    ContractMasterError,
    ContractRuleSnapshot,
    ContractTradingSession,
    ContinuousResearchSeries,
    DeliveryRestriction,
    Exchange,
    Instrument,
    ListingState,
    RuleQualityStatus,
)
from northstar_quant.foundation.db.models import ContractMasterPublicationRecord
from northstar_quant.foundation.db.session import SessionLocal


__all__ = [
    "CONTRACT_MASTER_PUBLICATION_SCHEMA_VERSION",
    "ContractAuthorityError",
    "ContractMasterPublication",
    "PostgresContractMasterPublicationRepository",
    "load_contract_master_publication_at",
]


CONTRACT_MASTER_PUBLICATION_SCHEMA_VERSION = 1
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_MAX_TEXT_LENGTH = 256


class ContractAuthorityError(ValueError):
    """Raised when a Contract Authority publication cannot be trusted."""


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value.strip()) is None:
        raise ContractAuthorityError(f"{field_name} must be a stable identifier")
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
        raise ContractAuthorityError(f"{field_name} must be a bounded single-line text")
    return value.strip()


def _hash(value: object, field_name: str) -> str:
    try:
        return require_sha256(cast(str, value), field_name=field_name)
    except FingerprintError as exc:
        raise ContractAuthorityError(str(exc)) from exc


def _utc_time(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ContractAuthorityError(f"{field_name} must be timezone aware")
    return value.astimezone(UTC)


def _date_value(value: object, field_name: str) -> date:
    if isinstance(value, datetime):
        raise ContractAuthorityError(f"{field_name} must be an ISO date")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ContractAuthorityError(f"{field_name} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ContractAuthorityError(f"{field_name} must be an ISO date") from exc


def _time_value(value: object, field_name: str) -> time:
    if not isinstance(value, str):
        raise ContractAuthorityError(f"{field_name} must be an ISO time")
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise ContractAuthorityError(f"{field_name} must be an ISO time") from exc


def _datetime_value(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ContractAuthorityError(f"{field_name} must be an ISO datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractAuthorityError(f"{field_name} must be an ISO datetime") from exc
    return _utc_time(parsed, field_name)


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ContractAuthorityError(f"{field_name} must be finite")
    result = float(value)
    if not math.isfinite(result):
        raise ContractAuthorityError(f"{field_name} must be finite")
    return result


def _bool(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise ContractAuthorityError(f"{field_name} must be boolean")
    return value


def _object(
    value: object,
    field_name: str,
    required_fields: frozenset[str],
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ContractAuthorityError(f"{field_name} must be an object")
    actual_fields = set(value)
    missing = sorted(required_fields - actual_fields)
    unknown = sorted(actual_fields - required_fields)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unknown:
            details.append("unknown=" + ",".join(unknown))
        raise ContractAuthorityError(f"{field_name} fields invalid: {'; '.join(details)}")
    return {str(key): item for key, item in value.items()}


def _array(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise ContractAuthorityError(f"{field_name} must be a list")
    return value


def _canonical_payload_json(payload: dict[str, object]) -> str:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContractAuthorityError("CONTRACT_MASTER_PAYLOAD_NOT_CANONICAL") from exc


def _master_payload(master: ContractMaster) -> dict[str, object]:
    return {
        "commodities": [
            {"commodity_id": item.commodity_id, "name": item.name}
            for item in master.commodities
        ],
        "continuous_series": [
            {
                "instrument_id": item.instrument_id,
                "series_id": item.series_id,
                "symbol": item.symbol,
            }
            for item in master.continuous_series
        ],
        "contracts": [
            {
                "contract_id": item.contract_id,
                "expires_on": item.expires_on.isoformat(),
                "instrument_id": item.instrument_id,
                "listed_on": item.listed_on.isoformat(),
                "symbol": item.symbol,
            }
            for item in master.contracts
        ],
        "exchanges": [
            {
                "exchange_id": item.exchange_id,
                "market": item.market,
                "name": item.name,
                "timezone_name": item.timezone_name,
            }
            for item in master.exchanges
        ],
        "instruments": [
            {
                "commodity_id": item.commodity_id,
                "exchange_id": item.exchange_id,
                "instrument_id": item.instrument_id,
                "product_code": item.product_code,
            }
            for item in master.instruments
        ],
        "master_id": master.master_id,
        "rule_snapshots": [
            {
                "available_at": item.available_at.isoformat(),
                "contract_id": item.contract_id,
                "delivery_restriction": item.delivery_restriction.value,
                "effective_from": item.effective_from.isoformat(),
                "effective_until": (
                    item.effective_until.isoformat()
                    if item.effective_until is not None
                    else None
                ),
                "execution_eligible": item.execution_eligible,
                "expires_on": item.expires_on.isoformat(),
                "fees": {
                    "close_per_lot": item.fees.close_per_lot,
                    "close_rate": item.fees.close_rate,
                    "close_today_per_lot": item.fees.close_today_per_lot,
                    "close_today_rate": item.fees.close_today_rate,
                    "open_per_lot": item.fees.open_per_lot,
                    "open_rate": item.fees.open_rate,
                },
                "initial_margin_rate": item.initial_margin_rate,
                "listing_state": item.listing_state.value,
                "lower_price_limit": item.lower_price_limit,
                "multiplier": item.multiplier,
                "observed_at": item.observed_at.isoformat(),
                "quality_status": item.quality_status.value,
                "sessions": [
                    {
                        "closes_at": session.closes_at.isoformat(),
                        "opens_at": session.opens_at.isoformat(),
                        "session_id": session.session_id,
                    }
                    for session in item.sessions
                ],
                "snapshot_hash": item.snapshot_hash,
                "snapshot_id": item.snapshot_id,
                "source_artifact_hash": item.source_artifact_hash,
                "source_authority": item.source_authority,
                "tick_size": item.tick_size,
                "upper_price_limit": item.upper_price_limit,
            }
            for item in master.rule_snapshots
        ],
        "version": master.version,
    }


_MASTER_FIELDS = frozenset(
    {
        "commodities",
        "continuous_series",
        "contracts",
        "exchanges",
        "instruments",
        "master_id",
        "rule_snapshots",
        "version",
    }
)
_COMMODITY_FIELDS = frozenset({"commodity_id", "name"})
_EXCHANGE_FIELDS = frozenset({"exchange_id", "market", "name", "timezone_name"})
_INSTRUMENT_FIELDS = frozenset(
    {"commodity_id", "exchange_id", "instrument_id", "product_code"}
)
_SERIES_FIELDS = frozenset({"instrument_id", "series_id", "symbol"})
_CONTRACT_FIELDS = frozenset(
    {"contract_id", "expires_on", "instrument_id", "listed_on", "symbol"}
)
_RULE_FIELDS = frozenset(
    {
        "available_at",
        "contract_id",
        "delivery_restriction",
        "effective_from",
        "effective_until",
        "execution_eligible",
        "expires_on",
        "fees",
        "initial_margin_rate",
        "listing_state",
        "lower_price_limit",
        "multiplier",
        "observed_at",
        "quality_status",
        "sessions",
        "snapshot_hash",
        "snapshot_id",
        "source_artifact_hash",
        "source_authority",
        "tick_size",
        "upper_price_limit",
    }
)
_FEE_FIELDS = frozenset(
    {
        "close_per_lot",
        "close_rate",
        "close_today_per_lot",
        "close_today_rate",
        "open_per_lot",
        "open_rate",
    }
)
_SESSION_FIELDS = frozenset({"closes_at", "opens_at", "session_id"})


def _commodity_from_payload(value: object, index: int) -> Commodity:
    item = _object(value, f"commodities[{index}]", _COMMODITY_FIELDS)
    return Commodity(
        commodity_id=_text(item["commodity_id"], f"commodities[{index}].commodity_id"),
        name=_text(item["name"], f"commodities[{index}].name"),
    )


def _exchange_from_payload(value: object, index: int) -> Exchange:
    item = _object(value, f"exchanges[{index}]", _EXCHANGE_FIELDS)
    return Exchange(
        exchange_id=_text(item["exchange_id"], f"exchanges[{index}].exchange_id"),
        name=_text(item["name"], f"exchanges[{index}].name"),
        market=_text(item["market"], f"exchanges[{index}].market"),
        timezone_name=_text(item["timezone_name"], f"exchanges[{index}].timezone_name"),
    )


def _instrument_from_payload(value: object, index: int) -> Instrument:
    item = _object(value, f"instruments[{index}]", _INSTRUMENT_FIELDS)
    return Instrument(
        instrument_id=_text(item["instrument_id"], f"instruments[{index}].instrument_id"),
        commodity_id=_text(item["commodity_id"], f"instruments[{index}].commodity_id"),
        exchange_id=_text(item["exchange_id"], f"instruments[{index}].exchange_id"),
        product_code=_text(item["product_code"], f"instruments[{index}].product_code"),
    )


def _series_from_payload(value: object, index: int) -> ContinuousResearchSeries:
    item = _object(value, f"continuous_series[{index}]", _SERIES_FIELDS)
    return ContinuousResearchSeries(
        series_id=_text(item["series_id"], f"continuous_series[{index}].series_id"),
        instrument_id=_text(
            item["instrument_id"],
            f"continuous_series[{index}].instrument_id",
        ),
        symbol=_text(item["symbol"], f"continuous_series[{index}].symbol"),
    )


def _contract_from_payload(value: object, index: int) -> Contract:
    item = _object(value, f"contracts[{index}]", _CONTRACT_FIELDS)
    return Contract(
        contract_id=_text(item["contract_id"], f"contracts[{index}].contract_id"),
        instrument_id=_text(item["instrument_id"], f"contracts[{index}].instrument_id"),
        symbol=_text(item["symbol"], f"contracts[{index}].symbol"),
        listed_on=_date_value(item["listed_on"], f"contracts[{index}].listed_on"),
        expires_on=_date_value(item["expires_on"], f"contracts[{index}].expires_on"),
    )


def _rule_from_payload(value: object, index: int) -> ContractRuleSnapshot:
    prefix = f"rule_snapshots[{index}]"
    item = _object(value, prefix, _RULE_FIELDS)
    fees = _object(item["fees"], f"{prefix}.fees", _FEE_FIELDS)
    sessions = tuple(
        _session_from_payload(session, prefix, session_index)
        for session_index, session in enumerate(_array(item["sessions"], f"{prefix}.sessions"))
    )
    try:
        return ContractRuleSnapshot(
            snapshot_id=_text(item["snapshot_id"], f"{prefix}.snapshot_id"),
            contract_id=_text(item["contract_id"], f"{prefix}.contract_id"),
            observed_at=_datetime_value(item["observed_at"], f"{prefix}.observed_at"),
            available_at=_datetime_value(item["available_at"], f"{prefix}.available_at"),
            effective_from=_datetime_value(
                item["effective_from"],
                f"{prefix}.effective_from",
            ),
            effective_until=(
                _datetime_value(item["effective_until"], f"{prefix}.effective_until")
                if item["effective_until"] is not None
                else None
            ),
            listing_state=ListingState(
                _text(item["listing_state"], f"{prefix}.listing_state")
            ),
            expires_on=_date_value(item["expires_on"], f"{prefix}.expires_on"),
            multiplier=_number(item["multiplier"], f"{prefix}.multiplier"),
            tick_size=_number(item["tick_size"], f"{prefix}.tick_size"),
            initial_margin_rate=_number(
                item["initial_margin_rate"],
                f"{prefix}.initial_margin_rate",
            ),
            fees=ContractFeeSchedule(
                open_per_lot=_number(fees["open_per_lot"], f"{prefix}.fees.open_per_lot"),
                open_rate=_number(fees["open_rate"], f"{prefix}.fees.open_rate"),
                close_per_lot=_number(
                    fees["close_per_lot"],
                    f"{prefix}.fees.close_per_lot",
                ),
                close_rate=_number(fees["close_rate"], f"{prefix}.fees.close_rate"),
                close_today_per_lot=_number(
                    fees["close_today_per_lot"],
                    f"{prefix}.fees.close_today_per_lot",
                ),
                close_today_rate=_number(
                    fees["close_today_rate"],
                    f"{prefix}.fees.close_today_rate",
                ),
            ),
            lower_price_limit=_number(
                item["lower_price_limit"],
                f"{prefix}.lower_price_limit",
            ),
            upper_price_limit=_number(
                item["upper_price_limit"],
                f"{prefix}.upper_price_limit",
            ),
            sessions=sessions,
            delivery_restriction=DeliveryRestriction(
                _text(item["delivery_restriction"], f"{prefix}.delivery_restriction")
            ),
            source_artifact_hash=_hash(
                item["source_artifact_hash"],
                f"{prefix}.source_artifact_hash",
            ),
            source_authority=_text(item["source_authority"], f"{prefix}.source_authority"),
            quality_status=RuleQualityStatus(
                _text(item["quality_status"], f"{prefix}.quality_status")
            ),
            execution_eligible=_bool(
                item["execution_eligible"],
                f"{prefix}.execution_eligible",
            ),
            snapshot_hash=_hash(item["snapshot_hash"], f"{prefix}.snapshot_hash"),
        )
    except (ContractMasterError, ValueError) as exc:
        raise ContractAuthorityError(f"{prefix} cannot be reconstructed") from exc


def _session_from_payload(
    value: object,
    prefix: str,
    index: int,
) -> ContractTradingSession:
    item = _object(value, f"{prefix}.sessions[{index}]", _SESSION_FIELDS)
    return ContractTradingSession(
        session_id=_text(item["session_id"], f"{prefix}.sessions[{index}].session_id"),
        opens_at=_time_value(item["opens_at"], f"{prefix}.sessions[{index}].opens_at"),
        closes_at=_time_value(item["closes_at"], f"{prefix}.sessions[{index}].closes_at"),
    )


def _master_from_payload(payload: object) -> ContractMaster:
    root = _object(payload, "contract_master_payload", _MASTER_FIELDS)
    try:
        return ContractMaster(
            master_id=_text(root["master_id"], "master_id"),
            version=_text(root["version"], "version"),
            commodities=tuple(
                _commodity_from_payload(item, index)
                for index, item in enumerate(_array(root["commodities"], "commodities"))
            ),
            exchanges=tuple(
                _exchange_from_payload(item, index)
                for index, item in enumerate(_array(root["exchanges"], "exchanges"))
            ),
            instruments=tuple(
                _instrument_from_payload(item, index)
                for index, item in enumerate(_array(root["instruments"], "instruments"))
            ),
            continuous_series=tuple(
                _series_from_payload(item, index)
                for index, item in enumerate(
                    _array(root["continuous_series"], "continuous_series")
                )
            ),
            contracts=tuple(
                _contract_from_payload(item, index)
                for index, item in enumerate(_array(root["contracts"], "contracts"))
            ),
            rule_snapshots=tuple(
                _rule_from_payload(item, index)
                for index, item in enumerate(
                    _array(root["rule_snapshots"], "rule_snapshots")
                )
            ),
        )
    except (ContractAuthorityError, ContractMasterError, TypeError, ValueError) as exc:
        if isinstance(exc, ContractAuthorityError):
            raise
        raise ContractAuthorityError("CONTRACT_MASTER_PUBLICATION_PAYLOAD_INVALID") from exc


@dataclass(frozen=True, slots=True)
class ContractMasterPublication:
    """One immutable, source-bound Contract Master publication."""

    authority_id: str
    publication_id: str
    observed_at: datetime
    available_at: datetime
    source_artifact_hash: str
    source_authority: str
    master: ContractMaster
    schema_version: int = CONTRACT_MASTER_PUBLICATION_SCHEMA_VERSION
    content_hash: str = field(init=False)
    publication_hash: str = field(init=False)

    def __post_init__(self) -> None:
        authority_id = _identifier(self.authority_id, "authority_id")
        publication_id = _identifier(self.publication_id, "publication_id")
        observed_at = _utc_time(self.observed_at, "observed_at")
        available_at = _utc_time(self.available_at, "available_at")
        if available_at < observed_at:
            raise ContractAuthorityError("available_at cannot precede observed_at")
        source_artifact_hash = _hash(self.source_artifact_hash, "source_artifact_hash")
        source_authority = _text(self.source_authority, "source_authority")
        if type(self.master) is not ContractMaster:
            raise ContractAuthorityError("master must be a ContractMaster")
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != CONTRACT_MASTER_PUBLICATION_SCHEMA_VERSION
        ):
            raise ContractAuthorityError("CONTRACT_MASTER_PUBLICATION_SCHEMA_UNSUPPORTED")
        payload = _master_payload(self.master)
        content_hash = canonical_json_sha256(payload)
        publication_hash = canonical_json_sha256(
            {
                "authority_id": authority_id,
                "available_at": available_at.isoformat(),
                "content_hash": content_hash,
                "format": "northstar.contract-master-publication.v1",
                "master_fingerprint": self.master.fingerprint,
                "observed_at": observed_at.isoformat(),
                "publication_id": publication_id,
                "quality_status": "pass",
                "schema_version": self.schema_version,
                "source_artifact_hash": source_artifact_hash,
                "source_authority": source_authority,
            }
        )
        object.__setattr__(self, "authority_id", authority_id)
        object.__setattr__(self, "publication_id", publication_id)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "source_artifact_hash", source_artifact_hash)
        object.__setattr__(self, "source_authority", source_authority)
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "publication_hash", publication_hash)

    @property
    def payload_json(self) -> str:
        """Return the canonical payload stored in PostgreSQL."""

        return _canonical_payload_json(_master_payload(self.master))


class PostgresContractMasterPublicationRepository:
    """Append/replay repository for immutable Contract Master publications."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        if not isinstance(session, Session):
            raise TypeError("CONTRACT_AUTHORITY_POSTGRESQL_SESSION_REQUIRED")
        self._session = session

    def publish(
        self,
        publication: ContractMasterPublication,
        *,
        commit: bool = True,
    ) -> ContractMasterPublication:
        """Append a publication, allowing only exact idempotent replay."""

        if type(publication) is not ContractMasterPublication:
            raise TypeError("CONTRACT_MASTER_PUBLICATION_REQUIRED")
        existing = self._session.scalar(
            select(ContractMasterPublicationRecord).where(
                ContractMasterPublicationRecord.publication_hash
                == publication.publication_hash
            )
        )
        if existing is not None:
            self._assert_record_matches(existing, publication)
            return publication
        record = ContractMasterPublicationRecord(
            authority_id=publication.authority_id,
            publication_id=publication.publication_id,
            schema_version=publication.schema_version,
            master_id=publication.master.master_id,
            master_version=publication.master.version,
            observed_at=publication.observed_at,
            available_at=publication.available_at,
            source_artifact_hash=publication.source_artifact_hash,
            source_authority=publication.source_authority,
            quality_status="pass",
            master_fingerprint=publication.master.fingerprint,
            content_hash=publication.content_hash,
            publication_hash=publication.publication_hash,
            payload_json=publication.payload_json,
        )
        self._session.add(record)
        try:
            self._session.flush()
            if commit:
                self._session.commit()
        except IntegrityError as exc:
            if commit:
                self._session.rollback()
            raise ContractAuthorityError(
                "CONTRACT_MASTER_PUBLICATION_CONFLICT"
            ) from exc
        return publication

    def load_at(
        self,
        authority_id: str,
        *,
        decision_at: datetime,
    ) -> ContractMasterPublication:
        """Replay the latest single publication visible at the decision time."""

        normalized_authority_id = _identifier(authority_id, "authority_id")
        normalized_decision_at = _utc_time(decision_at, "decision_at")
        record = self._session.scalar(
            select(ContractMasterPublicationRecord)
            .where(
                ContractMasterPublicationRecord.authority_id == normalized_authority_id,
                ContractMasterPublicationRecord.available_at <= normalized_decision_at,
            )
            .order_by(desc(ContractMasterPublicationRecord.available_at))
            .limit(1)
        )
        if record is None:
            raise ContractAuthorityError("CONTRACT_MASTER_AUTHORITY_UNAVAILABLE")
        publication = self._record_to_publication(record)
        if publication.available_at > normalized_decision_at:
            raise ContractAuthorityError("CONTRACT_MASTER_AUTHORITY_FUTURE_PUBLICATION")
        return publication

    @staticmethod
    def _assert_record_matches(
        record: ContractMasterPublicationRecord,
        publication: ContractMasterPublication,
    ) -> None:
        matching = (
            record.authority_id == publication.authority_id
            and record.publication_id == publication.publication_id
            and record.schema_version == publication.schema_version
            and record.master_id == publication.master.master_id
            and record.master_version == publication.master.version
            and record.observed_at == publication.observed_at
            and record.available_at == publication.available_at
            and record.source_artifact_hash == publication.source_artifact_hash
            and record.source_authority == publication.source_authority
            and record.quality_status == "pass"
            and record.master_fingerprint == publication.master.fingerprint
            and record.content_hash == publication.content_hash
            and record.publication_hash == publication.publication_hash
            and record.payload_json == publication.payload_json
        )
        if not matching:
            raise ContractAuthorityError("CONTRACT_MASTER_PUBLICATION_TAMPERED")

    @staticmethod
    def _record_to_publication(
        record: ContractMasterPublicationRecord,
    ) -> ContractMasterPublication:
        if record.quality_status != "pass":
            raise ContractAuthorityError("CONTRACT_MASTER_PUBLICATION_QUALITY_REFUSED")
        try:
            payload = json.loads(record.payload_json)
        except json.JSONDecodeError as exc:
            raise ContractAuthorityError("CONTRACT_MASTER_PUBLICATION_PAYLOAD_INVALID") from exc
        publication = ContractMasterPublication(
            authority_id=record.authority_id,
            publication_id=record.publication_id,
            observed_at=record.observed_at,
            available_at=record.available_at,
            source_artifact_hash=record.source_artifact_hash,
            source_authority=record.source_authority,
            master=_master_from_payload(payload),
            schema_version=record.schema_version,
        )
        PostgresContractMasterPublicationRepository._assert_record_matches(
            record,
            publication,
        )
        return publication


def load_contract_master_publication_at(
    authority_id: str,
    *,
    decision_at: datetime,
    session_factory: Callable[[], Session] = SessionLocal,
) -> ContractMasterPublication:
    """Load a typed Master replay through the core PostgreSQL authority only."""

    with session_factory() as session:
        return PostgresContractMasterPublicationRepository(session).load_at(
            authority_id,
            decision_at=decision_at,
        )
