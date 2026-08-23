from datetime import UTC, datetime, timedelta

import pytest

from northstar_quant.intelligence.event_study import EventStudyError, EventStudyResult, EventWindow, event_study_as_of


def _result(*, available_at: datetime | None = None) -> EventStudyResult:
    event_time = datetime(2026, 8, 22, 9, tzinfo=UTC)
    window = EventWindow.T_PLUS_15_MINUTES
    end = event_time + window.duration
    return EventStudyResult("study-1", "event-1", "dataset-1", window, event_time, end, available_at or end, 0.02, 0.18, 1_000.0, 500.0, 3.0, 1.5, 0.03, -0.01)


def test_event_study_records_every_required_metric_after_the_declared_window():
    result = _result()
    assert event_study_as_of(result=result, simulation_time=result.available_at) is result
    assert result.window == EventWindow.T_PLUS_15_MINUTES
    assert result.mfe == 0.03
    assert result.mae == -0.01


def test_event_study_rejects_lookahead_and_inconsistent_window_or_metrics():
    result = _result(available_at=datetime(2026, 8, 22, 10, tzinfo=UTC))
    with pytest.raises(EventStudyError, match="not yet available"):
        event_study_as_of(result=result, simulation_time=result.window_end)
    with pytest.raises(EventStudyError, match="window_end"):
        EventStudyResult("study-2", "event-1", "dataset-1", EventWindow.T_PLUS_1_HOUR, result.event_time, result.event_time + timedelta(minutes=15), result.event_time + timedelta(hours=1), 0.0, 0.1, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0)
    with pytest.raises(EventStudyError, match="MAE"):
        EventStudyResult("study-3", "event-1", "dataset-1", EventWindow.T_PLUS_15_MINUTES, result.event_time, result.window_end, result.window_end, 0.0, 0.1, 1.0, 1.0, 0.0, 0.0, 0.0, 0.01)
