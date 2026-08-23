"""P4-WP10 typed, evidence-preserving Event-to-Contract impact paths."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from northstar_quant.intelligence.domain import Entity, Event
from northstar_quant.intelligence.mechanisms import MechanismAssessment
from northstar_quant.intelligence.ontology import Ontology


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class ImpactGraphError(ValueError):
    pass


class ImpactNodeType(StrEnum):
    EVENT = "EVENT"
    MECHANISM = "MECHANISM"
    ENTITY = "ENTITY"
    COMMODITY = "COMMODITY"
    MARKET = "MARKET"
    INSTRUMENT = "INSTRUMENT"
    CONTRACT = "CONTRACT"


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value.strip()) is None:
        raise ImpactGraphError(f"{field} must be a non-empty identifier")
    return value.strip()


@dataclass(frozen=True, slots=True)
class MarketRef:
    market_id: str
    commodity_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "market_id", _identifier(self.market_id, "market_id"))
        object.__setattr__(self, "commodity_id", _identifier(self.commodity_id, "commodity_id"))


@dataclass(frozen=True, slots=True)
class InstrumentRef:
    instrument_id: str
    market_id: str
    commodity_id: str

    def __post_init__(self) -> None:
        for field in ("instrument_id", "market_id", "commodity_id"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))


@dataclass(frozen=True, slots=True)
class ContractRef:
    contract_id: str
    instrument_id: str
    commodity_id: str

    def __post_init__(self) -> None:
        for field in ("contract_id", "instrument_id", "commodity_id"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))


@dataclass(frozen=True, slots=True)
class ImpactNode:
    node_id: str
    node_type: ImpactNodeType

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_id", _identifier(self.node_id, "node_id"))
        if not isinstance(self.node_type, ImpactNodeType):
            raise ImpactGraphError("node_type must be an impact node type")


@dataclass(frozen=True, slots=True)
class ImpactEdge:
    source_id: str
    target_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _identifier(self.source_id, "source_id"))
        object.__setattr__(self, "target_id", _identifier(self.target_id, "target_id"))


@dataclass(frozen=True, slots=True)
class ImpactPath:
    nodes: tuple[ImpactNode, ...]
    edges: tuple[ImpactEdge, ...]

    def __post_init__(self) -> None:
        required_types = tuple(ImpactNodeType)
        if tuple(node.node_type for node in self.nodes) != required_types:
            raise ImpactGraphError("impact path must be Event → Mechanism → Entity → Commodity → Market → Instrument → Contract")
        if len({node.node_id for node in self.nodes}) != len(self.nodes):
            raise ImpactGraphError("impact path node identifiers must be unique")
        expected_edges = tuple(ImpactEdge(left.node_id, right.node_id) for left, right in zip(self.nodes, self.nodes[1:]))
        if self.edges != expected_edges:
            raise ImpactGraphError("impact path edges must preserve the directed domain sequence")


def build_impact_path(
    *,
    event: Event,
    assessment: MechanismAssessment,
    affected_entity: Entity,
    commodity_id: str,
    market: MarketRef,
    instrument: InstrumentRef,
    contract: ContractRef,
    ontology: Ontology,
) -> ImpactPath:
    """Build a validated exposure path, without pricing, targeting or execution semantics."""
    if not all(isinstance(value, expected) for value, expected in ((event, Event), (assessment, MechanismAssessment), (affected_entity, Entity), (market, MarketRef), (instrument, InstrumentRef), (contract, ContractRef), (ontology, Ontology))):
        raise ImpactGraphError("impact path inputs must be typed")
    commodity_id = _identifier(commodity_id, "commodity_id")
    if commodity_id not in ontology.commodities:
        raise ImpactGraphError("commodity must be present in ontology")
    if affected_entity.entity_type not in ontology.entity_types:
        raise ImpactGraphError("affected entity type must be present in ontology")
    if event.ontology_version != ontology.version or assessment.ontology_version != ontology.version:
        raise ImpactGraphError("event and mechanism must use the supplied ontology version")
    if event.mechanism != assessment.domain_mechanism:
        raise ImpactGraphError("event mechanism must match the evidence-backed assessment")
    if assessment.evidence not in event.evidence:
        raise ImpactGraphError("mechanism assessment evidence must be retained by the Event")
    if not any(impact.commodity_id == commodity_id for impact in event.impacts):
        raise ImpactGraphError("event must carry an impact for the path commodity")
    if {market.commodity_id, instrument.commodity_id, contract.commodity_id} != {commodity_id}:
        raise ImpactGraphError("market, instrument and contract must share the path commodity")
    if instrument.market_id != market.market_id or contract.instrument_id != instrument.instrument_id:
        raise ImpactGraphError("instrument and contract must preserve the market mapping")
    nodes = (
        ImpactNode(event.event_id, ImpactNodeType.EVENT),
        ImpactNode(assessment.mechanism_type.value, ImpactNodeType.MECHANISM),
        ImpactNode(affected_entity.entity_id, ImpactNodeType.ENTITY),
        ImpactNode(commodity_id, ImpactNodeType.COMMODITY),
        ImpactNode(market.market_id, ImpactNodeType.MARKET),
        ImpactNode(instrument.instrument_id, ImpactNodeType.INSTRUMENT),
        ImpactNode(contract.contract_id, ImpactNodeType.CONTRACT),
    )
    return ImpactPath(nodes, tuple(ImpactEdge(left.node_id, right.node_id) for left, right in zip(nodes, nodes[1:])))


__all__ = ["ContractRef", "ImpactEdge", "ImpactGraphError", "ImpactNode", "ImpactNodeType", "ImpactPath", "InstrumentRef", "MarketRef", "build_impact_path"]
