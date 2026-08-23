"""P4-WP02 versioned ontology loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml  # type: ignore[import-untyped]


class OntologyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Ontology:
    version: str
    event_types: frozenset[str]
    mechanisms: frozenset[str]
    entity_types: frozenset[str]
    commodities: frozenset[str]
    relations: frozenset[str]

    def validate_event_type(self, value: str) -> None:
        if value not in self.event_types:
            raise OntologyError(f"unknown event type: {value}")


def load_ontology(root: Path) -> Ontology:
    specs = {"events": "event_types", "mechanisms": "mechanisms", "entities": "entity_types", "commodities": "commodities", "relations": "relations"}
    payloads: dict[str, dict[str, object]] = {}
    for filename in specs:
        path = root / f"{filename}.yaml"
        if not path.is_file():
            raise OntologyError(f"missing ontology resource: {path.name}")
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise OntologyError(f"ontology resource must be a mapping: {path.name}")
        payloads[filename] = value
    versions = {value.get("ontology_version") for value in payloads.values()}
    version = next(iter(versions)) if len(versions) == 1 else None
    if not isinstance(version, str) or not version:
        raise OntologyError("all ontology resources must share one ontology_version")
    def values(file: str) -> frozenset[str]:
        value = payloads[file].get(specs[file])
        if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
            raise OntologyError(f"invalid ontology values: {file}")
        return frozenset(value)
    return Ontology(version, values("events"), values("mechanisms"), values("entities"), values("commodities"), values("relations"))


__all__ = ["Ontology", "OntologyError", "load_ontology"]
