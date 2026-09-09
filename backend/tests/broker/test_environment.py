"""Unsupported Live environments cannot fall through to a simulation account."""

import importlib

import pytest

from northstar_quant.broker.settings import (
    configured_profile,
    get_profile,
    load_credentials,
    profiles,
)


@pytest.mark.parametrize("environment", ["production", "paper", "", "simulation", "simulaton"])
def test_unsupported_environment_refuses_before_database_or_credentials(
    environment, tmp_path, monkeypatch
):
    kernel = importlib.import_module("northstar_quant.apps.live.kernel.application")
    monkeypatch.setenv("NORTHSTAR_LIVE_ENVIRONMENT", environment)
    monkeypatch.delenv("NORTHSTAR_SIMNOW_PASSWORD", raising=False)
    monkeypatch.setenv("NORTHSTAR_LOG_DIR", str(tmp_path / "logs"))

    def forbidden():
        pytest.fail("unsupported environment must not open persistent account state")

    monkeypatch.setattr(kernel, "open_database", forbidden)
    for operation in (kernel.application, profiles, load_credentials):
        with pytest.raises(ValueError, match="Production Live|must be simnow"):
            operation()
    with pytest.raises(ValueError, match="Production Live|must be simnow"):
        get_profile("simnow_dev")


def test_simulation_configuration_does_not_grant_credentials_or_change_broker(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("NORTHSTAR_LIVE_ENVIRONMENT", "simnow_trading")
    monkeypatch.delenv("NORTHSTAR_SIMNOW_PASSWORD", raising=False)
    assert all(profile["environment"] == "SIMNOW" for profile in profiles())
    with pytest.raises(ValueError, match="all four"):
        load_credentials()
    with pytest.raises(ValueError, match="approved SimNow"):
        get_profile("production")


@pytest.mark.parametrize("environment", ["simnow_dev", "simnow_trading"])
def test_configured_environment_is_unique_and_rejects_other_saved_environment(
    monkeypatch, environment
):
    monkeypatch.setenv("NORTHSTAR_LIVE_ENVIRONMENT", environment)
    assert [profile["name"] for profile in profiles()] == [environment]
    assert configured_profile(environment).name == environment
    other = "simnow_dev" if environment == "simnow_trading" else "simnow_trading"
    with pytest.raises(ValueError, match="differs"):
        configured_profile(other)
