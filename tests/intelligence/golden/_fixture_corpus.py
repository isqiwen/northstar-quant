"""Strict, test-only loader for the P10 six-commodity intelligence corpus.

The corpus is deliberately isolated beneath ``tests/``.  It models immutable
fixture evidence only; it is never a data authorization, contract master,
calendar, rulebook, broker mapping, or trading input.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re

from northstar_quant.intelligence.domain import Document, Entity, Event, Evidence
from northstar_quant.intelligence.event_merge import CanonicalEvent, EventLifecycle
from northstar_quant.intelligence.extraction import ExtractedEvent, validate_extraction
from northstar_quant.intelligence.impact_graph import (
    ContractRef,
    ImpactGraphError,
    ImpactPath,
    InstrumentRef,
    MarketRef,
    build_impact_path,
)
from northstar_quant.intelligence.mechanisms import MechanismAssessment, MechanismType
from northstar_quant.intelligence.ingestion import RawDocument, normalize_document
from northstar_quant.intelligence.ontology import Ontology
from northstar_quant.research.features import FeatureVersion


FIXTURE_CORPUS_SCHEMA_VERSION = "northstar.intelligence.fixture-only-corpus.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FIXTURE_PREFIX = "fixture-"
_IMPACT_DIRECTIONS = frozenset({"UP", "DOWN", "NEUTRAL"})
_AUTHORITY_FIELDS = frozenset(
    {
        "authorized_market_data",
        "actual_contract_data",
        "authoritative_calendar",
        "authoritative_dynamic_rules",
        "eligible_for_trading",
    }
)


class FixtureCorpusError(ValueError):
    """Raised when a golden fixture could be confused with an authorized input."""


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise FixtureCorpusError(f"{field} must be a JSON object")
    return value


def _sequence(value: object, field: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, list):
        raise FixtureCorpusError(f"{field} must be a JSON array")
    return value


def _exact_keys(value: Mapping[str, object], expected: frozenset[str], field: str) -> None:
    if set(value) != expected:
        raise FixtureCorpusError(
            f"{field} keys must be exactly {sorted(expected)!r}, got {sorted(value)!r}"
        )


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value.strip() != value:
        raise FixtureCorpusError(f"{field} must be non-empty trimmed text")
    return value


def _fixture_id(value: object, field: str) -> str:
    identifier = _text(value, field)
    if not identifier.startswith(_FIXTURE_PREFIX):
        raise FixtureCorpusError(f"{field} must start with {_FIXTURE_PREFIX!r}")
    return identifier


def _time(value: object, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value, field))
    except ValueError as exc:
        raise FixtureCorpusError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FixtureCorpusError(f"{field} must be timezone-aware")
    return parsed


def _sha(value: object, field: str) -> str:
    digest = _text(value, field)
    if _SHA256.fullmatch(digest) is None:
        raise FixtureCorpusError(f"{field} must be a lowercase SHA-256")
    return digest


def _commitment_hash(value: object) -> str:
    """Return one deterministic test-only commitment without retaining raw text."""

    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _number(value: object, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise FixtureCorpusError(f"{field} must be a number")
    parsed = float(value)
    if not 0 <= parsed <= 1:
        raise FixtureCorpusError(f"{field} must be in [0, 1]")
    return parsed


def _lifecycle(value: object, field: str) -> EventLifecycle:
    try:
        return EventLifecycle(_text(value, field))
    except ValueError as exc:
        raise FixtureCorpusError(f"{field} must be a supported lifecycle") from exc


def _mechanism(value: object, field: str) -> MechanismType:
    try:
        return MechanismType(_text(value, field))
    except ValueError as exc:
        raise FixtureCorpusError(f"{field} must be a supported mechanism") from exc


@dataclass(frozen=True, slots=True)
class FixtureEntity:
    entity_id: str
    entity_type: str
    canonical_name: str
    alias: str

    def as_domain_entity(self) -> Entity:
        return Entity(self.entity_id, self.entity_type, self.canonical_name)


@dataclass(frozen=True, slots=True)
class FixtureDocument:
    document_key: str
    source_id: str
    canonical_url: str
    title: str
    license_classification: str
    content: str
    content_sha256: str
    published_at: datetime
    collected_at: datetime
    extraction_id: str
    extraction_confidence: float
    span_start: int
    span_end: int
    evidence_text: str
    observed_at: datetime
    lifecycle: EventLifecycle


@dataclass(frozen=True, slots=True)
class FixtureEvent:
    event_id: str
    semantic_key: str
    event_type: str
    mechanism_type: MechanismType
    rationale: str
    assessment_confidence: float
    impact_id: str
    direction: str
    primary_extraction_id: str
    evidence_document_keys: tuple[str, ...]
    expected_event_hash: str


@dataclass(frozen=True, slots=True)
class FixtureOnlyCrosswalk:
    event_id: str
    mechanism_type: MechanismType
    entity_id: str
    commodity_id: str
    market: MarketRef
    instrument: InstrumentRef
    contract: ContractRef
    feature_id: str
    expected_feature_definition_handoff_hash: str

    @classmethod
    def from_mapping(cls, value: object) -> FixtureOnlyCrosswalk:
        payload = _mapping(value, "crosswalk")
        _exact_keys(
            payload,
            frozenset(
                {
                    "fixture_only",
                    "event_id",
                    "mechanism_type",
                    "entity_id",
                    "commodity_id",
                    "market",
                    "instrument",
                    "contract",
                    "feature_id",
                    "expected_feature_definition_handoff_hash",
                    "research_only",
                }
            ),
            "crosswalk",
        )
        if payload["fixture_only"] is not True or payload["research_only"] is not True:
            raise FixtureCorpusError("crosswalk must be fixture_only and research_only")
        event_id = _fixture_id(payload["event_id"], "crosswalk.event_id")
        mechanism_type = _mechanism(payload["mechanism_type"], "crosswalk.mechanism_type")
        entity_id = _fixture_id(payload["entity_id"], "crosswalk.entity_id")
        commodity_id = _text(payload["commodity_id"], "crosswalk.commodity_id")
        feature_id = _text(payload["feature_id"], "crosswalk.feature_id")
        if not feature_id.startswith("intelligence."):
            raise FixtureCorpusError("crosswalk.feature_id must be an intelligence feature")
        expected_feature_definition_handoff_hash = _sha(
            payload["expected_feature_definition_handoff_hash"],
            "crosswalk.expected_feature_definition_handoff_hash",
        )
        market_payload = _mapping(payload["market"], "crosswalk.market")
        _exact_keys(market_payload, frozenset({"market_id", "commodity_id"}), "crosswalk.market")
        instrument_payload = _mapping(payload["instrument"], "crosswalk.instrument")
        _exact_keys(
            instrument_payload,
            frozenset({"instrument_id", "market_id", "commodity_id"}),
            "crosswalk.instrument",
        )
        contract_payload = _mapping(payload["contract"], "crosswalk.contract")
        _exact_keys(
            contract_payload,
            frozenset({"contract_id", "instrument_id", "commodity_id"}),
            "crosswalk.contract",
        )
        try:
            market = MarketRef(
                _fixture_id(market_payload["market_id"], "crosswalk.market.market_id"),
                _text(market_payload["commodity_id"], "crosswalk.market.commodity_id"),
            )
            instrument = InstrumentRef(
                _fixture_id(
                    instrument_payload["instrument_id"],
                    "crosswalk.instrument.instrument_id",
                ),
                _fixture_id(
                    instrument_payload["market_id"], "crosswalk.instrument.market_id"
                ),
                _text(
                    instrument_payload["commodity_id"],
                    "crosswalk.instrument.commodity_id",
                ),
            )
            contract = ContractRef(
                _fixture_id(
                    contract_payload["contract_id"], "crosswalk.contract.contract_id"
                ),
                _fixture_id(
                    contract_payload["instrument_id"],
                    "crosswalk.contract.instrument_id",
                ),
                _text(
                    contract_payload["commodity_id"],
                    "crosswalk.contract.commodity_id",
                ),
            )
        except ImpactGraphError as exc:
            raise FixtureCorpusError("crosswalk references must be typed fixture identifiers") from exc
        return cls(
            event_id=event_id,
            mechanism_type=mechanism_type,
            entity_id=entity_id,
            commodity_id=commodity_id,
            market=market,
            instrument=instrument,
            contract=contract,
            feature_id=feature_id,
            expected_feature_definition_handoff_hash=expected_feature_definition_handoff_hash,
        )

    def build_path(
        self,
        *,
        event: Event,
        assessment: MechanismAssessment,
        entity: Entity,
        ontology: Ontology,
    ) -> ImpactPath:
        if event.event_id != self.event_id:
            raise FixtureCorpusError("crosswalk event_id must bind the exact Event")
        if assessment.mechanism_type is not self.mechanism_type:
            raise FixtureCorpusError("crosswalk mechanism_type must bind the assessment")
        if assessment.evidence not in event.evidence:
            raise FixtureCorpusError("crosswalk assessment evidence must be retained by the Event")
        if entity.entity_id != self.entity_id:
            raise FixtureCorpusError("crosswalk entity_id must bind the resolved Entity")
        if self.commodity_id not in {impact.commodity_id for impact in event.impacts}:
            raise FixtureCorpusError("crosswalk commodity must be impacted by the Event")
        try:
            return build_impact_path(
                event=event,
                assessment=assessment,
                affected_entity=entity,
                commodity_id=self.commodity_id,
                market=self.market,
                instrument=self.instrument,
                contract=self.contract,
                ontology=ontology,
            )
        except ImpactGraphError as exc:
            raise FixtureCorpusError("fixture-only crosswalk is inconsistent") from exc


@dataclass(frozen=True, slots=True)
class FixtureCase:
    case_id: str
    commodity_id: str
    entity: FixtureEntity
    documents: tuple[FixtureDocument, ...]
    event: FixtureEvent
    crosswalk: FixtureOnlyCrosswalk

    @property
    def documents_by_key(self) -> dict[str, FixtureDocument]:
        return {document.document_key: document for document in self.documents}

    @property
    def documents_by_extraction_id(self) -> dict[str, FixtureDocument]:
        return {document.extraction_id: document for document in self.documents}


@dataclass(frozen=True, slots=True)
class FixtureOnlyFeatureDefinitionHandoff:
    """Test-only Event-to-Feature-definition commitment.

    This is deliberately not a P1/PIT FeatureValue, a data authorization, a
    research decision, or a trading input. It retains only hashes and typed
    identifiers so the golden corpus can prove the exact fixture-only lineage
    handed to a registered ``intelligence.*`` Feature definition.
    """

    case_id: str
    event_id: str
    event_hash: str
    evidence_commitment_hash: str
    impact_path_hash: str
    feature_id: str
    feature_version_hash: str
    fixture_only: bool
    research_only: bool
    handoff_hash: str = field(init=False)

    def __post_init__(self) -> None:
        case_id = _fixture_id(self.case_id, "feature handoff.case_id")
        event_id = _fixture_id(self.event_id, "feature handoff.event_id")
        event_hash = _sha(self.event_hash, "feature handoff.event_hash")
        evidence_commitment_hash = _sha(
            self.evidence_commitment_hash,
            "feature handoff.evidence_commitment_hash",
        )
        impact_path_hash = _sha(self.impact_path_hash, "feature handoff.impact_path_hash")
        feature_id = _text(self.feature_id, "feature handoff.feature_id")
        if not feature_id.startswith("intelligence."):
            raise FixtureCorpusError("feature handoff must bind an intelligence feature")
        feature_version_hash = _sha(
            self.feature_version_hash,
            "feature handoff.feature_version_hash",
        )
        if self.fixture_only is not True or self.research_only is not True:
            raise FixtureCorpusError("feature handoff must remain fixture_only and research_only")
        payload = {
            "case_id": case_id,
            "event_hash": event_hash,
            "event_id": event_id,
            "evidence_commitment_hash": evidence_commitment_hash,
            "feature_id": feature_id,
            "feature_version_hash": feature_version_hash,
            "fixture_only": True,
            "format": "northstar.fixture-only-feature-definition-handoff.v1",
            "impact_path_hash": impact_path_hash,
            "research_only": True,
        }
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "event_hash", event_hash)
        object.__setattr__(self, "evidence_commitment_hash", evidence_commitment_hash)
        object.__setattr__(self, "impact_path_hash", impact_path_hash)
        object.__setattr__(self, "feature_id", feature_id)
        object.__setattr__(self, "feature_version_hash", feature_version_hash)
        object.__setattr__(self, "handoff_hash", _commitment_hash(payload))

    @classmethod
    def from_lineage(
        cls,
        *,
        case: FixtureCase,
        canonical: CanonicalEvent,
        event: Event,
        path: ImpactPath,
        feature_version: FeatureVersion,
    ) -> FixtureOnlyFeatureDefinitionHandoff:
        """Bind one fully reconstructed fixture Event to one Feature version."""

        if not isinstance(case, FixtureCase):
            raise FixtureCorpusError("feature handoff must bind a typed fixture case")
        if not isinstance(canonical, CanonicalEvent):
            raise FixtureCorpusError("feature handoff must bind a canonical Event")
        if not isinstance(event, Event) or not isinstance(path, ImpactPath):
            raise FixtureCorpusError("feature handoff requires typed Event and ImpactPath")
        if not isinstance(feature_version, FeatureVersion):
            raise FixtureCorpusError("feature handoff requires a registered FeatureVersion")
        expected_extraction_ids = tuple(
            sorted(
                case.documents_by_key[document_key].extraction_id
                for document_key in case.event.evidence_document_keys
            )
        )
        if (
            canonical.semantic_key != case.event.semantic_key
            or canonical.lifecycle is not EventLifecycle.CONFIRMED
            or canonical.extraction_ids != expected_extraction_ids
        ):
            raise FixtureCorpusError("feature handoff canonical lineage must exactly match event evidence")
        expected_evidence = tuple(item.evidence for item in canonical.extractions)
        if event.event_id != case.event.event_id or event.evidence != expected_evidence:
            raise FixtureCorpusError("feature handoff Event must exactly bind canonical evidence")
        if event.event_hash != case.event.expected_event_hash:
            raise FixtureCorpusError("feature handoff Event hash drifted from the golden corpus")
        if (
            event.mechanism.mechanism_id != case.event.mechanism_type.value
            or case.crosswalk.feature_id != feature_version.feature_id
        ):
            raise FixtureCorpusError("feature handoff feature or mechanism binding is inconsistent")
        expected_node_ids = (
            event.event_id,
            case.event.mechanism_type.value,
            case.entity.entity_id,
            case.commodity_id,
            case.crosswalk.market.market_id,
            case.crosswalk.instrument.instrument_id,
            case.crosswalk.contract.contract_id,
        )
        if tuple(node.node_id for node in path.nodes) != expected_node_ids:
            raise FixtureCorpusError("feature handoff ImpactPath must exactly bind the crosswalk")
        evidence_commitment_hash = _commitment_hash(
            [
                {
                    "content_hash": evidence.content_hash,
                    "document_id": evidence.document_id,
                    "span_end": evidence.span_end,
                    "span_start": evidence.span_start,
                }
                for evidence in event.evidence
            ]
        )
        impact_path_hash = _commitment_hash(
            [
                {"node_id": node.node_id, "node_type": node.node_type.value}
                for node in path.nodes
            ]
        )
        return cls(
            case_id=case.case_id,
            event_id=event.event_id,
            event_hash=event.event_hash,
            evidence_commitment_hash=evidence_commitment_hash,
            impact_path_hash=impact_path_hash,
            feature_id=feature_version.feature_id,
            feature_version_hash=feature_version.version_hash,
            fixture_only=True,
            research_only=True,
        )


@dataclass(frozen=True, slots=True)
class FixtureMergeStep:
    document_key: str
    observed_at: datetime
    lifecycle: EventLifecycle
    expected_lifecycle: EventLifecycle
    expected_observed_at: datetime
    expected_extraction_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FixtureMergeScenario:
    scenario_id: str
    case_id: str
    semantic_key: str
    steps: tuple[FixtureMergeStep, ...]


@dataclass(frozen=True, slots=True)
class FixtureCorpus:
    ontology_version: str
    cases: tuple[FixtureCase, ...]
    merge_scenarios: tuple[FixtureMergeScenario, ...]

    @property
    def cases_by_id(self) -> dict[str, FixtureCase]:
        return {case.case_id: case for case in self.cases}


def materialize_case_documents(
    *, case: FixtureCase, ontology: Ontology
) -> dict[str, tuple[Document, ExtractedEvent]]:
    """Rebuild fixture Documents and extraction evidence without any external adapter."""

    materialized: dict[str, tuple[Document, ExtractedEvent]] = {}
    for fixture in case.documents:
        document = normalize_document(
            source_id=fixture.source_id,
            raw=RawDocument(
                fixture.canonical_url,
                fixture.content,
                fixture.published_at,
                fixture.license_classification,
            ),
            collected_at=fixture.collected_at,
        )
        if document.content_hash != fixture.content_sha256:
            raise FixtureCorpusError("normalized Document content hash drifted from fixture")
        evidence = Evidence(
            document.document_id,
            document.content_hash,
            fixture.span_start,
            fixture.span_end,
        )
        candidate = ExtractedEvent(
            fixture.extraction_id,
            document.document_id,
            case.event.event_type,
            ontology.version,
            evidence,
            fixture.extraction_confidence,
        )
        materialized[fixture.document_key] = (
            document,
            validate_extraction(document=document, candidate=candidate, ontology=ontology),
        )
    return materialized


def materialize_feature_definition_handoff(
    *,
    case: FixtureCase,
    ontology: Ontology,
    code_revision: str,
) -> FixtureOnlyFeatureDefinitionHandoff:
    """Rebuild one WP02 fixture Event-to-Feature-definition handoff.

    This stays strictly inside the fixture corpus boundary.  It is deliberately
    not a P1/PIT materialization and returns no ``FeatureValue`` or market-data
    authorization.
    """

    from northstar_quant.intelligence.domain import Impact
    from northstar_quant.intelligence.event_merge import merge_event
    from northstar_quant.intelligence.mechanisms import assess_mechanism
    from northstar_quant.research.features import FeatureRegistry, register_canonical_feature

    if not isinstance(case, FixtureCase):
        raise FixtureCorpusError("feature handoff requires a FixtureCase")
    materialized = materialize_case_documents(case=case, ontology=ontology)
    canonical: CanonicalEvent | None = None
    for document_key in case.event.evidence_document_keys:
        fixture_document = case.documents_by_key[document_key]
        _, candidate = materialized[document_key]
        canonical = merge_event(
            current=canonical,
            candidate=candidate,
            semantic_key=case.event.semantic_key,
            observed_at=fixture_document.observed_at,
            lifecycle=fixture_document.lifecycle,
        )
    if canonical is None or canonical.lifecycle is not EventLifecycle.CONFIRMED:
        raise FixtureCorpusError("fixture handoff requires a confirmed canonical Event")
    candidates_by_id = {
        candidate.extraction_id: candidate for _, candidate in materialized.values()
    }
    primary = candidates_by_id[case.event.primary_extraction_id]
    assessment = assess_mechanism(
        candidate=primary,
        mechanism_type=case.event.mechanism_type,
        ontology=ontology,
        rationale=case.event.rationale,
        confidence=case.event.assessment_confidence,
    )
    event = Event(
        case.event.event_id,
        ontology.version,
        tuple(candidate.evidence for candidate in canonical.extractions),
        assessment.domain_mechanism,
        (Impact(case.event.impact_id, case.commodity_id, case.event.direction),),
    )
    if event.event_hash != case.event.expected_event_hash:
        raise FixtureCorpusError("fixture Event hash drifted from the golden corpus")
    path = case.crosswalk.build_path(
        event=event,
        assessment=assessment,
        entity=case.entity.as_domain_entity(),
        ontology=ontology,
    )
    registry = FeatureRegistry()
    feature_version = register_canonical_feature(
        registry,
        feature_id=case.crosswalk.feature_id,
        version="1.0.0",
        code_revision=code_revision,
    )
    handoff = FixtureOnlyFeatureDefinitionHandoff.from_lineage(
        case=case,
        canonical=canonical,
        event=event,
        path=path,
        feature_version=feature_version,
    )
    if handoff.handoff_hash != case.crosswalk.expected_feature_definition_handoff_hash:
        raise FixtureCorpusError("fixture Feature-definition handoff hash drifted")
    return handoff


def _parse_entity(value: object) -> FixtureEntity:
    payload = _mapping(value, "entity")
    _exact_keys(
        payload,
        frozenset({"entity_id", "entity_type", "canonical_name", "alias"}),
        "entity",
    )
    return FixtureEntity(
        _fixture_id(payload["entity_id"], "entity.entity_id"),
        _text(payload["entity_type"], "entity.entity_type"),
        _text(payload["canonical_name"], "entity.canonical_name"),
        _text(payload["alias"], "entity.alias"),
    )


def _parse_document(value: object) -> FixtureDocument:
    payload = _mapping(value, "document")
    _exact_keys(
        payload,
        frozenset(
            {
                "document_key",
                "source_id",
                "canonical_url",
                "title",
                "license_classification",
                "content",
                "content_sha256",
                "published_at",
                "collected_at",
                "extraction",
            }
        ),
        "document",
    )
    content = _text(payload["content"], "document.content")
    content_sha256 = _sha(payload["content_sha256"], "document.content_sha256")
    if sha256(content.encode("utf-8")).hexdigest() != content_sha256:
        raise FixtureCorpusError("document.content_sha256 does not bind UTF-8 content")
    canonical_url = _text(payload["canonical_url"], "document.canonical_url")
    if not canonical_url.startswith("https://fixture.invalid/"):
        raise FixtureCorpusError("document.canonical_url must use fixture.invalid")
    if payload["license_classification"] != "fixture_only":
        raise FixtureCorpusError("document.license_classification must be fixture_only")
    published_at = _time(payload["published_at"], "document.published_at")
    collected_at = _time(payload["collected_at"], "document.collected_at")
    if collected_at < published_at:
        raise FixtureCorpusError("document.collected_at cannot precede published_at")
    extraction = _mapping(payload["extraction"], "document.extraction")
    _exact_keys(
        extraction,
        frozenset(
            {
                "extraction_id",
                "extraction_confidence",
                "evidence",
                "observed_at",
                "lifecycle",
            }
        ),
        "document.extraction",
    )
    evidence = _mapping(extraction["evidence"], "document.extraction.evidence")
    _exact_keys(
        evidence,
        frozenset({"span_start", "span_end", "text"}),
        "document.extraction.evidence",
    )
    span_start = evidence["span_start"]
    span_end = evidence["span_end"]
    if (
        not isinstance(span_start, int)
        or isinstance(span_start, bool)
        or not isinstance(span_end, int)
        or isinstance(span_end, bool)
        or span_start < 0
        or span_end <= span_start
        or span_end > len(content)
    ):
        raise FixtureCorpusError("document evidence span must be non-empty and in content bounds")
    evidence_text = _text(evidence["text"], "document.extraction.evidence.text")
    if content[span_start:span_end] != evidence_text:
        raise FixtureCorpusError("document evidence text must exactly match its span")
    return FixtureDocument(
        _fixture_id(payload["document_key"], "document.document_key"),
        _fixture_id(payload["source_id"], "document.source_id"),
        canonical_url,
        _text(payload["title"], "document.title"),
        "fixture_only",
        content,
        content_sha256,
        published_at,
        collected_at,
        _fixture_id(extraction["extraction_id"], "document.extraction.extraction_id"),
        _number(extraction["extraction_confidence"], "document.extraction.extraction_confidence"),
        span_start,
        span_end,
        evidence_text,
        _time(extraction["observed_at"], "document.extraction.observed_at"),
        _lifecycle(extraction["lifecycle"], "document.extraction.lifecycle"),
    )


def _parse_event(value: object) -> FixtureEvent:
    payload = _mapping(value, "event")
    _exact_keys(
        payload,
        frozenset(
            {
                "event_id",
                "semantic_key",
                "event_type",
                "mechanism_type",
                "rationale",
                "assessment_confidence",
                "impact",
                "primary_extraction_id",
                "evidence_document_keys",
                "expected_event_hash",
            }
        ),
        "event",
    )
    impact = _mapping(payload["impact"], "event.impact")
    _exact_keys(impact, frozenset({"impact_id", "direction"}), "event.impact")
    document_keys = tuple(
        _fixture_id(item, "event.evidence_document_keys")
        for item in _sequence(payload["evidence_document_keys"], "event.evidence_document_keys")
    )
    if not document_keys or len(set(document_keys)) != len(document_keys):
        raise FixtureCorpusError("event.evidence_document_keys must be unique and non-empty")
    direction = _text(impact["direction"], "event.impact.direction")
    if direction not in _IMPACT_DIRECTIONS:
        raise FixtureCorpusError("event.impact.direction must be a supported direction")
    return FixtureEvent(
        _fixture_id(payload["event_id"], "event.event_id"),
        _fixture_id(payload["semantic_key"], "event.semantic_key"),
        _text(payload["event_type"], "event.event_type"),
        _mechanism(payload["mechanism_type"], "event.mechanism_type"),
        _text(payload["rationale"], "event.rationale"),
        _number(payload["assessment_confidence"], "event.assessment_confidence"),
        _fixture_id(impact["impact_id"], "event.impact.impact_id"),
        direction,
        _fixture_id(payload["primary_extraction_id"], "event.primary_extraction_id"),
        document_keys,
        _sha(payload["expected_event_hash"], "event.expected_event_hash"),
    )


def _parse_case(value: object) -> FixtureCase:
    payload = _mapping(value, "commodity_case")
    _exact_keys(
        payload,
        frozenset({"case_id", "commodity_id", "entity", "documents", "event", "crosswalk"}),
        "commodity_case",
    )
    documents = tuple(
        _parse_document(item) for item in _sequence(payload["documents"], "commodity_case.documents")
    )
    if len(documents) < 2:
        raise FixtureCorpusError("commodity_case must contain at least two fixture documents")
    if len({item.document_key for item in documents}) != len(documents):
        raise FixtureCorpusError("commodity_case document keys must be unique")
    if len({item.extraction_id for item in documents}) != len(documents):
        raise FixtureCorpusError("commodity_case extraction IDs must be unique")
    event = _parse_event(payload["event"])
    entity = _parse_entity(payload["entity"])
    crosswalk = FixtureOnlyCrosswalk.from_mapping(payload["crosswalk"])
    commodity_id = _text(payload["commodity_id"], "commodity_case.commodity_id")
    document_keys = {document.document_key for document in documents}
    documents_by_extraction_id = {
        document.extraction_id: document for document in documents
    }
    if not set(event.evidence_document_keys).issubset(document_keys):
        raise FixtureCorpusError("event evidence documents must be in the commodity case")
    primary_document = documents_by_extraction_id.get(event.primary_extraction_id)
    if primary_document is None:
        raise FixtureCorpusError("event primary extraction must be in the commodity case")
    if primary_document.document_key not in event.evidence_document_keys:
        raise FixtureCorpusError(
            "event primary extraction must be backed by event evidence documents"
        )
    if (
        crosswalk.event_id != event.event_id
        or crosswalk.mechanism_type is not event.mechanism_type
        or crosswalk.entity_id != entity.entity_id
        or crosswalk.commodity_id != commodity_id
    ):
        raise FixtureCorpusError("crosswalk must exactly bind the fixture case")
    return FixtureCase(
        _fixture_id(payload["case_id"], "commodity_case.case_id"),
        commodity_id,
        entity,
        documents,
        event,
        crosswalk,
    )


def _parse_merge_scenario(
    value: object, *, cases_by_id: Mapping[str, FixtureCase]
) -> FixtureMergeScenario:
    payload = _mapping(value, "merge_scenario")
    _exact_keys(
        payload,
        frozenset({"scenario_id", "case_id", "semantic_key", "steps"}),
        "merge_scenario",
    )
    case_id = _fixture_id(payload["case_id"], "merge_scenario.case_id")
    try:
        case = cases_by_id[case_id]
    except KeyError as exc:
        raise FixtureCorpusError("merge_scenario must reference an existing commodity case") from exc
    semantic_key = _fixture_id(payload["semantic_key"], "merge_scenario.semantic_key")
    if semantic_key != case.event.semantic_key:
        raise FixtureCorpusError("merge_scenario semantic_key must bind its fixture case")
    steps: list[FixtureMergeStep] = []
    known_documents = case.documents_by_key
    for index, raw_step in enumerate(_sequence(payload["steps"], "merge_scenario.steps")):
        step = _mapping(raw_step, f"merge_scenario.steps[{index}]")
        _exact_keys(
            step,
            frozenset(
                {
                    "document_key",
                    "observed_at",
                    "lifecycle",
                    "expected_lifecycle",
                    "expected_observed_at",
                    "expected_extraction_ids",
                }
            ),
            f"merge_scenario.steps[{index}]",
        )
        document_key = _fixture_id(step["document_key"], "merge_scenario.document_key")
        if document_key not in known_documents:
            raise FixtureCorpusError("merge step must reference a document in its fixture case")
        expected_extraction_ids = tuple(
            _fixture_id(item, "merge_scenario.expected_extraction_ids")
            for item in _sequence(
                step["expected_extraction_ids"], "merge_scenario.expected_extraction_ids"
            )
        )
        if not expected_extraction_ids or expected_extraction_ids != tuple(
            sorted(set(expected_extraction_ids))
        ):
            raise FixtureCorpusError("merge expected extraction IDs must be sorted and unique")
        steps.append(
            FixtureMergeStep(
                document_key=document_key,
                observed_at=_time(step["observed_at"], "merge_scenario.observed_at"),
                lifecycle=_lifecycle(step["lifecycle"], "merge_scenario.lifecycle"),
                expected_lifecycle=_lifecycle(
                    step["expected_lifecycle"], "merge_scenario.expected_lifecycle"
                ),
                expected_observed_at=_time(
                    step["expected_observed_at"], "merge_scenario.expected_observed_at"
                ),
                expected_extraction_ids=expected_extraction_ids,
            )
        )
    if not steps:
        raise FixtureCorpusError("merge_scenario must contain steps")
    return FixtureMergeScenario(
        _fixture_id(payload["scenario_id"], "merge_scenario.scenario_id"),
        case_id,
        semantic_key,
        tuple(steps),
    )


def load_fixture_only_corpus(path: Path) -> FixtureCorpus:
    """Load one exact, non-authorizing P10 fixture corpus before any test write."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureCorpusError("fixture corpus must be readable JSON") from exc
    root = _mapping(payload, "fixture corpus")
    _exact_keys(
        root,
        frozenset(
            {
                "schema_version",
                "fixture_only",
                "authority",
                "ontology_version",
                "commodity_cases",
                "merge_scenarios",
            }
        ),
        "fixture corpus",
    )
    if root["schema_version"] != FIXTURE_CORPUS_SCHEMA_VERSION:
        raise FixtureCorpusError("fixture corpus schema_version is unsupported")
    if root["fixture_only"] is not True:
        raise FixtureCorpusError("fixture corpus must explicitly be fixture_only")
    authority = _mapping(root["authority"], "fixture corpus authority")
    _exact_keys(authority, _AUTHORITY_FIELDS, "fixture corpus authority")
    if any(value is not False for value in authority.values()):
        raise FixtureCorpusError("fixture corpus authority flags must all be false")
    cases = tuple(
        _parse_case(item)
        for item in _sequence(root["commodity_cases"], "fixture corpus commodity_cases")
    )
    if len({case.case_id for case in cases}) != len(cases):
        raise FixtureCorpusError("fixture corpus case IDs must be unique")
    if len({case.commodity_id for case in cases}) != len(cases):
        raise FixtureCorpusError("fixture corpus commodity IDs must be unique")
    cases_by_id = {case.case_id: case for case in cases}
    scenarios = tuple(
        _parse_merge_scenario(item, cases_by_id=cases_by_id)
        for item in _sequence(root["merge_scenarios"], "fixture corpus merge_scenarios")
    )
    if len({scenario.scenario_id for scenario in scenarios}) != len(scenarios):
        raise FixtureCorpusError("fixture corpus merge scenario IDs must be unique")
    return FixtureCorpus(
        ontology_version=_text(root["ontology_version"], "fixture corpus ontology_version"),
        cases=cases,
        merge_scenarios=scenarios,
    )


__all__ = [
    "FIXTURE_CORPUS_SCHEMA_VERSION",
    "FixtureCase",
    "FixtureCorpus",
    "FixtureCorpusError",
    "FixtureDocument",
    "FixtureEntity",
    "FixtureEvent",
    "FixtureOnlyFeatureDefinitionHandoff",
    "FixtureMergeScenario",
    "FixtureMergeStep",
    "FixtureOnlyCrosswalk",
    "load_fixture_only_corpus",
    "materialize_case_documents",
    "materialize_feature_definition_handoff",
]
