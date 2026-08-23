from pathlib import Path

import pytest

from northstar_quant.intelligence.ontology import OntologyError, load_ontology


def test_ontology_v1_has_required_event_categories_and_validates_unknowns():
    ontology = load_ontology(Path("ontology"))
    assert ontology.version == "v1"
    assert {"SUPPLY", "FINANCIAL", "GEOPOLITICS"}.issubset(ontology.event_types)
    with pytest.raises(OntologyError, match="unknown"):
        ontology.validate_event_type("BUY")
