import pytest

from northstar_quant.intelligence.entity_resolution import CanonicalEntity, EntityResolutionError, EntityResolver


def test_entity_resolver_returns_canonical_id_and_rejects_alias_conflicts():
    resolver = EntityResolver(entities=(CanonicalEntity("commodity.copper", "Commodity", "Copper", ("Cu",)),))
    assert resolver.resolve("cu").entity_id == "commodity.copper"
    with pytest.raises(EntityResolutionError, match="unknown alias"):
        resolver.resolve("unknown")
    with pytest.raises(EntityResolutionError, match="alias conflict"):
        EntityResolver(entities=(CanonicalEntity("a", "Port", "Port A", ("A",)), CanonicalEntity("b", "Port", "Port B", ("a",))))
