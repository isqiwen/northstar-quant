"""Slow retention must not stall the owner responsible for collector supervision."""

from time import monotonic

from northstar_quant.apps.data_hub import retention_worker


def stalled_cleanup(stop):
    stop.wait(60)


def test_slow_retention_leaves_supervisor_responsive(monkeypatch):
    monkeypatch.setattr(retention_worker, "_run", stalled_cleanup)
    worker = retention_worker.RetentionWorker()
    started = monotonic()
    try:
        worker.maintain()
        assert worker.process is not None
        pid = worker.process.pid
        for _ in range(20):
            worker.maintain()
            assert worker.process.pid == pid
        assert worker.process.is_alive()
        assert monotonic() - started < 3
    finally:
        worker.close()
    assert monotonic() - started < 10
