import pytest

from northstar_quant.portfolio_risk.exposure import Direction, ExposureError, ExposurePosition, calculate_exposure


def test_exposure_engine_calculates_all_required_aggregates() -> None:
    snapshot = calculate_exposure(positions=(
        ExposurePosition("SHFE.RB2610", "RB", "ferrous", "SHFE", "china_steel", Direction.LONG, 100, 12),
        ExposurePosition("DCE.I2601", "I", "ferrous", "DCE", "china_steel", Direction.SHORT, 50, 8),
    ))
    assert snapshot.gross == 150
    assert snapshot.net == 50
    assert dict(snapshot.by_commodity) == {"I": -50, "RB": 100}
    assert dict(snapshot.by_direction) == {"long": 100, "short": 50}
    assert snapshot.margin_required == 20
    assert snapshot.concentration == pytest.approx(100 / 150)


def test_exposure_engine_rejects_unknown_classification_and_duplicate_instruments() -> None:
    with pytest.raises(ExposureError, match="commodity_id"):
        ExposurePosition("SHFE.RB2610", "", "ferrous", "SHFE", "china_steel", Direction.LONG, 100, 12)
    duplicate = ExposurePosition("SHFE.RB2610", "RB", "ferrous", "SHFE", "china_steel", Direction.LONG, 100, 12)
    with pytest.raises(ExposureError, match="duplicate"):
        calculate_exposure(positions=(duplicate, duplicate))
