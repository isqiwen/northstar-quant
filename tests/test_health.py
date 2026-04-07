from northstar_quant.monitoring.health import run_healthcheck


def test_healthcheck_contains_app_name():
    payload = run_healthcheck()
    assert payload["app_name"] == "Northstar Quant"
