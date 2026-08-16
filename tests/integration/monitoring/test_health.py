from northstar_quant.config.settings import get_settings
from northstar_quant.monitoring import health
from northstar_quant.monitoring.health import run_healthcheck
from tests.support.postgresql import postgresql_test_url


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


def test_healthcheck_labels_ctp_sim_as_local_simulation(monkeypatch, tmp_path):
    settings = get_settings().model_copy(
        update={
            "broker": "ctp_sim",
            "storage_dir": tmp_path / "storage",
            "reports_dir": tmp_path / "reports",
            "database_url": postgresql_test_url(tmp_path / "missing.db"),
        }
    )
    monkeypatch.setattr(health, "get_settings", lambda: settings)

    payload = health.run_healthcheck()

    broker_check = next(
        item for item in payload["checks"] if item["code"] == "broker_capability"
    )
    assert broker_check["status"] == "pass"
    assert payload["ctp_simulation_available"] is True
    assert payload["ctp_execution_available"] is False


def test_healthcheck_warns_when_private_ntfy_is_incomplete(monkeypatch):
    settings = get_settings().model_copy(
        update={
            "alert_mode": "ntfy",
            "ntfy_base_url": "https://ntfy.example.test",
            "ntfy_topic": None,
            "ntfy_token": None,
        }
    )
    monkeypatch.setattr(health, "get_settings", lambda: settings)

    payload = health.run_healthcheck()

    alert_check = next(
        item for item in payload["checks"] if item["code"] == "alert_delivery"
    )
    assert alert_check["status"] == "warn"
    assert "缺少必要凭据" in alert_check["message"]


def test_healthcheck_accepts_complete_private_ntfy_configuration(monkeypatch):
    settings = get_settings().model_copy(
        update={
            "alert_mode": "ntfy",
            "ntfy_base_url": "https://ntfy.example.test",
            "ntfy_topic": "northstar_alerts",
            "ntfy_token": "tk_12345678901234567890123456789",
        }
    )
    monkeypatch.setattr(health, "get_settings", lambda: settings)

    payload = health.run_healthcheck()

    alert_check = next(
        item for item in payload["checks"] if item["code"] == "alert_delivery"
    )
    assert alert_check["status"] == "pass"
    assert "配置可用" in alert_check["message"]
