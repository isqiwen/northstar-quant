import pytest

from northstar_quant.intelligence.extraction import ConfidenceInputs


def test_confidence_requires_all_independent_evidence_factors():
    assert ConfidenceInputs(1, 0, 1, 1).final_confidence == 0
    assert ConfidenceInputs(0.8, 0.5, 0.75, 0.9).final_confidence == pytest.approx(0.27)
