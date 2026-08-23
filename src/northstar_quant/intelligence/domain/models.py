"""P4-WP01 immutable intelligence domain semantics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import re


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


class IntelligenceDomainError(ValueError):
    pass


def _id(value: object, field: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value.strip()) is None:
        raise IntelligenceDomainError(f"{field} must be a non-empty identifier")
    return value.strip()


def _hash(value: object, field: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise IntelligenceDomainError(f"{field} must be a lowercase SHA-256")
    return value


def _time(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise IntelligenceDomainError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Source:
    source_id: str
    trust_score: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _id(self.source_id, "source_id"))
        if not 0 <= self.trust_score <= 1:
            raise IntelligenceDomainError("trust_score must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class Document:
    document_id: str
    source_id: str
    canonical_url: str
    content_hash: str
    published_at: datetime
    collected_at: datetime
    license_classification: str

    def __post_init__(self) -> None:
        for name in ("document_id", "source_id", "license_classification"):
            object.__setattr__(self, name, _id(getattr(self, name), name))
        if not isinstance(self.canonical_url, str) or not self.canonical_url.startswith("https://"):
            raise IntelligenceDomainError("canonical_url must be an https URL")
        object.__setattr__(self, "content_hash", _hash(self.content_hash, "content_hash"))
        published, collected = _time(self.published_at, "published_at"), _time(self.collected_at, "collected_at")
        if collected < published:
            raise IntelligenceDomainError("collected_at cannot precede published_at")
        object.__setattr__(self, "published_at", published)
        object.__setattr__(self, "collected_at", collected)


@dataclass(frozen=True, slots=True)
class Entity:
    entity_id: str
    entity_type: str
    canonical_name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_id", _id(self.entity_id, "entity_id"))
        object.__setattr__(self, "entity_type", _id(self.entity_type, "entity_type"))
        if not isinstance(self.canonical_name, str) or not self.canonical_name.strip():
            raise IntelligenceDomainError("canonical_name is required")


@dataclass(frozen=True, slots=True)
class Evidence:
    document_id: str
    content_hash: str
    span_start: int
    span_end: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "document_id", _id(self.document_id, "document_id"))
        object.__setattr__(self, "content_hash", _hash(self.content_hash, "content_hash"))
        if self.span_start < 0 or self.span_end <= self.span_start:
            raise IntelligenceDomainError("evidence spans must be non-empty and ordered")


@dataclass(frozen=True, slots=True)
class Mechanism:
    mechanism_id: str
    ontology_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "mechanism_id", _id(self.mechanism_id, "mechanism_id"))
        object.__setattr__(self, "ontology_version", _id(self.ontology_version, "ontology_version"))


@dataclass(frozen=True, slots=True)
class Impact:
    impact_id: str
    commodity_id: str
    direction: str

    def __post_init__(self) -> None:
        for name in ("impact_id", "commodity_id", "direction"):
            object.__setattr__(self, name, _id(getattr(self, name), name))


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    ontology_version: str
    evidence: tuple[Evidence, ...]
    mechanism: Mechanism
    impacts: tuple[Impact, ...]
    event_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _id(self.event_id, "event_id"))
        object.__setattr__(self, "ontology_version", _id(self.ontology_version, "ontology_version"))
        if not isinstance(self.evidence, tuple) or not self.evidence or not all(isinstance(item, Evidence) for item in self.evidence):
            raise IntelligenceDomainError("event requires non-empty Evidence, not a Document")
        if not isinstance(self.mechanism, Mechanism) or self.mechanism.ontology_version != self.ontology_version:
            raise IntelligenceDomainError("event mechanism must match ontology_version")
        if not isinstance(self.impacts, tuple) or not self.impacts or not all(isinstance(item, Impact) for item in self.impacts):
            raise IntelligenceDomainError("event requires non-empty impacts")
        payload = {"event_id": self.event_id, "ontology_version": self.ontology_version, "evidence": [(x.document_id, x.content_hash, x.span_start, x.span_end) for x in self.evidence], "mechanism": self.mechanism.mechanism_id, "impacts": [(x.impact_id, x.commodity_id, x.direction) for x in self.impacts]}
        object.__setattr__(self, "event_hash", hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest())


__all__ = ["Document", "Entity", "Event", "Evidence", "Impact", "IntelligenceDomainError", "Mechanism", "Source"]
