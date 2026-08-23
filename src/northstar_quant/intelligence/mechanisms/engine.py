"""P4-WP09 fail-closed classification of events into economic mechanisms."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from northstar_quant.intelligence.domain import Evidence, Mechanism
from northstar_quant.intelligence.extraction import ExtractedEvent
from northstar_quant.intelligence.ontology import Ontology


class MechanismError(ValueError):
    pass


class MechanismType(StrEnum):
    SUPPLY_REDUCTION = "SUPPLY_REDUCTION"
    SUPPLY_INCREASE = "SUPPLY_INCREASE"
    DEMAND_INCREASE = "DEMAND_INCREASE"
    DEMAND_REDUCTION = "DEMAND_REDUCTION"
    INVENTORY_DRAW = "INVENTORY_DRAW"
    INVENTORY_BUILD = "INVENTORY_BUILD"
    TRANSPORT_DISRUPTION = "TRANSPORT_DISRUPTION"
    IMPORT_COST_INCREASE = "IMPORT_COST_INCREASE"
    EXPORT_AVAILABILITY_REDUCTION = "EXPORT_AVAILABILITY_REDUCTION"
    RISK_PREMIUM_INCREASE = "RISK_PREMIUM_INCREASE"
    LIQUIDITY_SHOCK = "LIQUIDITY_SHOCK"


_ALLOWED_BY_EVENT_TYPE: dict[str, frozenset[MechanismType]] = {
    "SUPPLY": frozenset({MechanismType.SUPPLY_REDUCTION, MechanismType.SUPPLY_INCREASE, MechanismType.EXPORT_AVAILABILITY_REDUCTION}),
    "DEMAND": frozenset({MechanismType.DEMAND_INCREASE, MechanismType.DEMAND_REDUCTION}),
    "INVENTORY": frozenset({MechanismType.INVENTORY_DRAW, MechanismType.INVENTORY_BUILD}),
    "LOGISTICS": frozenset({MechanismType.TRANSPORT_DISRUPTION, MechanismType.IMPORT_COST_INCREASE, MechanismType.EXPORT_AVAILABILITY_REDUCTION}),
    "WEATHER": frozenset({MechanismType.SUPPLY_REDUCTION, MechanismType.TRANSPORT_DISRUPTION}),
    "POLICY": frozenset({MechanismType.IMPORT_COST_INCREASE, MechanismType.EXPORT_AVAILABILITY_REDUCTION, MechanismType.SUPPLY_INCREASE, MechanismType.SUPPLY_REDUCTION, MechanismType.DEMAND_INCREASE, MechanismType.DEMAND_REDUCTION}),
    "MACRO": frozenset({MechanismType.DEMAND_INCREASE, MechanismType.DEMAND_REDUCTION, MechanismType.IMPORT_COST_INCREASE, MechanismType.RISK_PREMIUM_INCREASE}),
    "GEOPOLITICS": frozenset({MechanismType.SUPPLY_REDUCTION, MechanismType.TRANSPORT_DISRUPTION, MechanismType.EXPORT_AVAILABILITY_REDUCTION, MechanismType.RISK_PREMIUM_INCREASE}),
    "POSITIONING": frozenset({MechanismType.LIQUIDITY_SHOCK, MechanismType.RISK_PREMIUM_INCREASE}),
    "FINANCIAL": frozenset({MechanismType.LIQUIDITY_SHOCK, MechanismType.RISK_PREMIUM_INCREASE, MechanismType.IMPORT_COST_INCREASE}),
}


@dataclass(frozen=True, slots=True)
class MechanismAssessment:
    extraction_id: str
    mechanism_type: MechanismType
    ontology_version: str
    evidence: Evidence
    rationale: str
    confidence: float

    def __post_init__(self) -> None:
        if not isinstance(self.extraction_id, str) or not self.extraction_id.strip():
            raise MechanismError("extraction_id is required")
        if not isinstance(self.mechanism_type, MechanismType):
            raise MechanismError("mechanism_type must be a supported economic mechanism")
        if not isinstance(self.ontology_version, str) or not self.ontology_version.strip():
            raise MechanismError("ontology_version is required")
        if not isinstance(self.evidence, Evidence):
            raise MechanismError("mechanism assessment requires typed evidence")
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise MechanismError("rationale is required")
        if not 0 <= self.confidence <= 1:
            raise MechanismError("confidence must be in [0, 1]")

    @property
    def domain_mechanism(self) -> Mechanism:
        return Mechanism(self.mechanism_type.value, self.ontology_version)


def assess_mechanism(
    *,
    candidate: ExtractedEvent,
    mechanism_type: MechanismType,
    ontology: Ontology,
    rationale: str,
    confidence: float,
) -> MechanismAssessment:
    """Make an auditable classification; it deliberately emits no market signal."""
    if not isinstance(candidate, ExtractedEvent) or not isinstance(ontology, Ontology):
        raise MechanismError("candidate and ontology must be typed")
    if candidate.ontology_version != ontology.version:
        raise MechanismError("candidate ontology_version does not match ontology")
    allowed = _ALLOWED_BY_EVENT_TYPE.get(candidate.event_type)
    if allowed is None or mechanism_type not in allowed:
        raise MechanismError("mechanism is not permitted for event type")
    if mechanism_type.value not in ontology.mechanisms:
        raise MechanismError("mechanism is not present in ontology")
    return MechanismAssessment(
        extraction_id=candidate.extraction_id,
        mechanism_type=mechanism_type,
        ontology_version=ontology.version,
        evidence=candidate.evidence,
        rationale=rationale,
        confidence=confidence,
    )


__all__ = ["MechanismAssessment", "MechanismError", "MechanismType", "assess_mechanism"]
