"""P4-WP05 canonical entity and alias resolution."""

from __future__ import annotations

from dataclasses import dataclass


ENTITY_TYPES = frozenset({"Country", "Region", "Company", "Mine", "Refinery", "Port", "Pipeline", "Commodity", "Exchange", "Instrument", "Contract", "GovernmentAgency"})


class EntityResolutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CanonicalEntity:
    entity_id: str
    entity_type: str
    canonical_name: str
    aliases: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.entity_type not in ENTITY_TYPES:
            raise EntityResolutionError("unknown entity_type")
        if not self.entity_id or not self.canonical_name or not isinstance(self.aliases, tuple):
            raise EntityResolutionError("canonical entity fields are required")
        if not self.aliases or any(not isinstance(alias, str) or not alias.strip() for alias in self.aliases):
            raise EntityResolutionError("aliases must be a non-empty text tuple")
        if len(set(alias.casefold() for alias in self.aliases)) != len(self.aliases):
            raise EntityResolutionError("aliases cannot duplicate case-insensitively")


class EntityResolver:
    def __init__(self, *, entities: tuple[CanonicalEntity, ...]) -> None:
        if not entities or len({entity.entity_id for entity in entities}) != len(entities):
            raise EntityResolutionError("entities must have unique canonical IDs")
        aliases: dict[str, CanonicalEntity] = {}
        for entity in entities:
            for alias in (entity.canonical_name, *entity.aliases):
                key = alias.casefold()
                if key in aliases and aliases[key].entity_id != entity.entity_id:
                    raise EntityResolutionError("alias conflict")
                aliases[key] = entity
        self._aliases = aliases

    def resolve(self, alias: str) -> CanonicalEntity:
        if not isinstance(alias, str) or not alias.strip():
            raise EntityResolutionError("alias is required")
        try:
            return self._aliases[alias.casefold()]
        except KeyError as exc:
            raise EntityResolutionError("unknown alias") from exc


__all__ = ["CanonicalEntity", "ENTITY_TYPES", "EntityResolutionError", "EntityResolver"]
