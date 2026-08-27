"""PostgreSQL persistence for immutable CTP registry publications.

The typed CTP registry stays free of database imports so a non-submitting
preflight can validate a replayed registry without gaining database capability.
This adapter is the execution infrastructure boundary that persists and loads
those same typed values.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from northstar_quant.foundation.db.models import CtpContractRegistryPublicationRecord
from northstar_quant.foundation.db.session import SessionLocal
from northstar_quant.trading_execution.broker.ctp_contract_mapping import (
    CtpContractMappingError,
    CtpContractRegistryPublication,
    _identifier,
    _registry_from_payload,
    _text,
    _utc_time,
)


__all__ = [
    "PostgresCtpContractRegistryPublicationRepository",
    "load_ctp_contract_registry_publication_at",
]


class PostgresCtpContractRegistryPublicationRepository:
    """Append/replay repository for immutable CTP registry publications."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        if not isinstance(session, Session):
            raise TypeError("CTP_CONTRACT_AUTHORITY_POSTGRESQL_SESSION_REQUIRED")
        self._session = session

    def publish(
        self,
        publication: CtpContractRegistryPublication,
        *,
        commit: bool = True,
    ) -> CtpContractRegistryPublication:
        """Append a registry release, allowing exact idempotent replay only."""

        if type(publication) is not CtpContractRegistryPublication:
            raise TypeError("CTP_CONTRACT_REGISTRY_PUBLICATION_REQUIRED")
        existing = self._session.scalar(
            select(CtpContractRegistryPublicationRecord).where(
                CtpContractRegistryPublicationRecord.publication_hash
                == publication.publication_hash
            )
        )
        if existing is not None:
            self._assert_record_matches(existing, publication)
            return publication
        record = CtpContractRegistryPublicationRecord(
            authority_id=publication.authority_id,
            publication_id=publication.publication_id,
            broker=publication.registry.broker,
            schema_version=publication.schema_version,
            master_publication_hash=publication.master_publication_hash,
            observed_at=publication.observed_at,
            available_at=publication.available_at,
            effective_from=publication.effective_from,
            effective_until=publication.effective_until,
            source_artifact_hash=publication.source_artifact_hash,
            source_authority=publication.source_authority,
            quality_status="pass",
            registry_fingerprint=publication.registry.fingerprint,
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
            raise CtpContractMappingError("CTP_REGISTRY_PUBLICATION_CONFLICT") from exc
        return publication

    def load_at(
        self,
        authority_id: str,
        *,
        broker: str,
        decision_at: datetime,
    ) -> CtpContractRegistryPublication:
        """Replay the latest visible and effective broker registry release."""

        normalized_authority_id = _identifier(authority_id, "authority_id")
        normalized_broker = _text(broker, "broker").lower()
        if normalized_broker not in {"ctp", "ctp_sim"}:
            raise CtpContractMappingError("broker must be ctp or ctp_sim")
        normalized_decision_at = _utc_time(decision_at, "decision_at")
        record = self._session.scalar(
            select(CtpContractRegistryPublicationRecord)
            .where(
                CtpContractRegistryPublicationRecord.authority_id
                == normalized_authority_id,
                CtpContractRegistryPublicationRecord.broker == normalized_broker,
                CtpContractRegistryPublicationRecord.available_at
                <= normalized_decision_at,
                CtpContractRegistryPublicationRecord.effective_from
                <= normalized_decision_at,
                (
                    CtpContractRegistryPublicationRecord.effective_until.is_(None)
                    | (
                        CtpContractRegistryPublicationRecord.effective_until
                        > normalized_decision_at
                    )
                ),
            )
            .order_by(desc(CtpContractRegistryPublicationRecord.available_at))
            .limit(1)
        )
        if record is None:
            raise CtpContractMappingError("CTP_CONTRACT_AUTHORITY_UNAVAILABLE")
        publication = self._record_to_publication(record)
        if (
            publication.available_at > normalized_decision_at
            or publication.effective_from > normalized_decision_at
            or (
                publication.effective_until is not None
                and publication.effective_until <= normalized_decision_at
            )
        ):
            raise CtpContractMappingError("CTP_CONTRACT_AUTHORITY_TIME_INVALID")
        return publication

    @staticmethod
    def _assert_record_matches(
        record: CtpContractRegistryPublicationRecord,
        publication: CtpContractRegistryPublication,
    ) -> None:
        matching = (
            record.authority_id == publication.authority_id
            and record.publication_id == publication.publication_id
            and record.broker == publication.registry.broker
            and record.schema_version == publication.schema_version
            and record.master_publication_hash == publication.master_publication_hash
            and record.observed_at == publication.observed_at
            and record.available_at == publication.available_at
            and record.effective_from == publication.effective_from
            and record.effective_until == publication.effective_until
            and record.source_artifact_hash == publication.source_artifact_hash
            and record.source_authority == publication.source_authority
            and record.quality_status == "pass"
            and record.registry_fingerprint == publication.registry.fingerprint
            and record.content_hash == publication.content_hash
            and record.publication_hash == publication.publication_hash
            and record.payload_json == publication.payload_json
        )
        if not matching:
            raise CtpContractMappingError("CTP_REGISTRY_PUBLICATION_TAMPERED")

    @staticmethod
    def _record_to_publication(
        record: CtpContractRegistryPublicationRecord,
    ) -> CtpContractRegistryPublication:
        if record.quality_status != "pass":
            raise CtpContractMappingError("CTP_REGISTRY_PUBLICATION_QUALITY_REFUSED")
        try:
            payload: Any = json.loads(record.payload_json)
        except json.JSONDecodeError as exc:
            raise CtpContractMappingError("CTP_REGISTRY_PUBLICATION_PAYLOAD_INVALID") from exc
        publication = CtpContractRegistryPublication(
            authority_id=record.authority_id,
            publication_id=record.publication_id,
            master_publication_hash=record.master_publication_hash,
            observed_at=record.observed_at,
            available_at=record.available_at,
            effective_from=record.effective_from,
            effective_until=record.effective_until,
            source_artifact_hash=record.source_artifact_hash,
            source_authority=record.source_authority,
            registry=_registry_from_payload(payload),
            schema_version=record.schema_version,
        )
        PostgresCtpContractRegistryPublicationRepository._assert_record_matches(
            record,
            publication,
        )
        return publication


def load_ctp_contract_registry_publication_at(
    authority_id: str,
    *,
    broker: str,
    decision_at: datetime,
    session_factory: Callable[[], Session] = SessionLocal,
) -> CtpContractRegistryPublication:
    """Load a typed CTP registry through PostgreSQL only."""

    with session_factory() as session:
        return PostgresCtpContractRegistryPublicationRepository(session).load_at(
            authority_id,
            broker=broker,
            decision_at=decision_at,
        )
