"""Forward labels are bounded and unavailable observations cannot become predictive evidence."""

from dataclasses import replace
from datetime import timedelta

import pytest

from northstar_quant.research.factor_analysis import analyze
from tests.test_research import dataset


def material():
    data = dataset(tuple(str((i + 10) ** 2) for i in range(40)))
    values = [
        {
            "observation_id": str(bar.observation_id),
            "at": bar.available_at.isoformat(),
            "status": "READY",
            "value": str(index),
        }
        for index, bar in enumerate(data.bars)
    ]
    return data, values


def test_average_rank_correlations_groups_and_fixed_window_labels():
    data, values = material()
    report = analyze(data, values)
    for item in report["horizons"]:
        assert item["samples"] == len(data.bars) - item["bars"]
        assert item["excluded"] == {"LABEL_OUTSIDE_FIXED_WINDOW": item["bars"]}
        assert item["spearman"] == pytest.approx(-1)
        assert sum(group["samples"] for group in item["groups"]) == item["samples"]
        assert item["groups"][0]["mean_forward_return"] > item["groups"][-1]["mean_forward_return"]
        assert item["days"][0]["spearman"] == pytest.approx(-1)
    assert report == analyze(data, values)
    ties = analyze(data, [{**row, "value": "1"} for row in values])
    assert all(
        h["spearman"] is None and h["status"] == "INSUFFICIENT_VARIATION" for h in ties["horizons"]
    )
    assert all(len(h["groups"]) == 1 for h in ties["horizons"])


def test_late_features_and_session_gaps_are_excluded_instead_of_lookahead_labels():
    data, values = material()
    bars = list(data.bars)
    bars[4] = replace(bars[4], available_at=bars[5].available_at)
    values[4] = {**values[4], "at": bars[4].available_at.isoformat()}
    for i in range(20, len(bars)):
        bars[i] = replace(
            bars[i],
            event_time=bars[i].event_time + timedelta(minutes=1),
            completed_at=bars[i].completed_at + timedelta(minutes=1),
            available_at=bars[i].available_at + timedelta(minutes=1),
        )
        values[i] = {**values[i], "at": bars[i].available_at.isoformat()}
    report = analyze(replace(data, bars=tuple(bars)), values)
    for item in report["horizons"]:
        assert item["excluded"]["FACTOR_NOT_AVAILABLE_BEFORE_LABEL_INTERVAL"] == 1
        assert item["excluded"]["LABEL_CROSSES_SESSION_GAP_OR_TRADING_DAY"] == item["bars"]
        assert item["samples"] + sum(item["excluded"].values()) == len(bars)
    missing = analyze(
        data,
        [
            {**row, "at": bar.available_at.isoformat(), "status": "WARMING_UP", "value": None}
            for row, bar in zip(values, data.bars)
        ],
    )
    assert all(h["samples"] == 0 and h["spearman"] is None for h in missing["horizons"])
