from northstar_quant.config.settings import get_settings
from northstar_quant.monitoring import health
from northstar_quant.monitoring.health import run_healthcheck
from tests.postgresql import postgresql_test_url


def test_healthcheck_contains_app_name():
    payload = run_healthcheck()
    assert payload["app_name"] == "Northstar Quant"
    assert payload["status"] in {"ok", "degraded", "blocked"}
    assert payload["checks"]


def test_healthcheck_marks_ctp_as_blocked(monkeypatch, tmp_path):
    settings = get_settings().model_copy(
        update={
            "broker": "ctp",
            "storage_dir": tmp_path / "storage",
            "reports_dir": tmp_path / "reports",
            "database_url": postgresql_test_url(tmp_path / "missing.db"),
        }
    )
    monkeypatch.setattr(health, "get_settings", lambda: settings)

    payload = health.run_healthcheck()

    assert payload["status"] == "blocked"
    broker_check = next(
        item for item in payload["checks"] if item["code"] == "broker_capability"
    )
    assert broker_check["status"] == "fail"
