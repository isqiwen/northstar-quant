"""P6-WP01 unified cross-domain configuration contract."""

from __future__ import annotations

from dataclasses import replace

import pytest

from northstar_quant.foundation.config.app_runtime import load_app_config
from northstar_quant.foundation.config.data_sources import get_data_source
from northstar_quant.foundation.config.runtime_configuration import (
    RuntimeConfigurationError,
    build_runtime_configuration,
    load_runtime_configuration,
)
from northstar_quant.foundation.config.settings import Settings
from northstar_quant.foundation.config.trading_profile import load_trading_profile


def _configuration():
    settings = Settings(_env_file=None)
    profile = load_trading_profile("cn_futures_daily_trend_simulated")
    return settings, load_app_config(settings.project_root), profile, get_data_source(profile.data.source_id)


def test_runtime_configuration_composes_environment_profile_data_and_intelligence():
    settings, app, profile, source = _configuration()

    configuration = build_runtime_configuration(
        settings=settings,
        app=app,
        profile=profile,
        data_source=source,
        research_admission_policy=None,
    )

    assert configuration.profile.profile_id == profile.profile_id
    assert configuration.data_source.source_id == profile.data.source_id
    assert configuration.intelligence_ontology_dir.name == "ontology"


def test_runtime_configuration_loads_the_default_safe_profile():
    configuration = load_runtime_configuration()

    assert configuration.profile.profile_id == "cn_futures_daily_trend_offline"
    assert configuration.research_admission_policy is None


def test_runtime_configuration_rejects_research_policy_pending_owner_approval():
    with pytest.raises(RuntimeConfigurationError, match="CONFIG_RESEARCH_POLICY_INVALID"):
        load_runtime_configuration("cn_futures_daily_actual_offline")


def test_runtime_configuration_rejects_profile_data_adapter_mismatch():
    settings, app, profile, source = _configuration()
    mismatched_profile = replace(profile, data=replace(profile.data, provider="wrong-adapter"))

    with pytest.raises(RuntimeConfigurationError, match="CONFIG_DATA_ADAPTER_MISMATCH"):
        build_runtime_configuration(
            settings=settings,
            app=app,
            profile=mismatched_profile,
            data_source=source,
            research_admission_policy=None,
        )


def test_runtime_configuration_rejects_settings_and_app_runtime_mismatch(tmp_path):
    settings, app, profile, source = _configuration()
    mismatched_app = replace(
        app,
        runtime=replace(app.runtime, storage_dir=tmp_path / "untrusted-storage"),
    )

    with pytest.raises(RuntimeConfigurationError, match="CONFIG_RUNTIME_STORAGE_MISMATCH"):
        build_runtime_configuration(
            settings=settings,
            app=mismatched_app,
            profile=profile,
            data_source=source,
            research_admission_policy=None,
        )


def test_runtime_configuration_rejects_incomplete_intelligence_ontology(tmp_path):
    settings, app, profile, source = _configuration()

    with pytest.raises(RuntimeConfigurationError, match="CONFIG_INTELLIGENCE_ONTOLOGY_INCOMPLETE"):
        build_runtime_configuration(
            settings=settings,
            app=app,
            profile=profile,
            data_source=source,
            research_admission_policy=None,
            intelligence_ontology_dir=tmp_path,
        )
