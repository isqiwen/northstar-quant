import pytest

from northstar_quant.platform.observability.monitoring.metrics import MetricsError, MetricsRegistry


def test_metrics_collect_and_export_deterministically():
    metrics = MetricsRegistry()
    metrics.increment("jobs_total", job="risk")
    metrics.increment("jobs_total", 2, job="risk")
    metrics.gauge("risk_state", 1, state="blocked")
    assert metrics.export_prometheus() == 'jobs_total{job="risk"} 3\nrisk_state{state="blocked"} 1\n'


def test_metrics_reject_invalid_input():
    with pytest.raises(MetricsError, match="METRIC_VALUE_INVALID"):
        MetricsRegistry().gauge("risk_state", float("nan"))
