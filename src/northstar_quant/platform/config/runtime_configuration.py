"""Cross-domain runtime configuration composition and fail-closed validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from northstar_quant.platform.config.app_runtime import AppConfig, load_app_config
from northstar_quant.platform.config.data_sources import DataSourceConfig, get_data_source
from northstar_quant.platform.config.research_admission import (
    ResearchAdmissionPolicy,
    load_research_admission_policy,
)
from northstar_quant.platform.config.settings import Settings, load_settings
from northstar_quant.platform.config.trading_profile import (
    TradingProfile,
    load_trading_profile,
)


class RuntimeConfigurationError(ValueError):
    """Runtime configuration sources disagree or omit required safety evidence."""


_REQUIRED_ONTOLOGY_FILES = frozenset(
    {"commodities.yaml", "entities.yaml", "events.yaml", "mechanisms.yaml", "relations.yaml"}
)


@dataclass(frozen=True, slots=True)
class RuntimeConfiguration:
    """Validated composition of environment, profile and domain-specific configuration."""

    settings: Settings
    app: AppConfig
    profile: TradingProfile
    data_source: DataSourceConfig
    research_admission_policy: ResearchAdmissionPolicy | None
    intelligence_ontology_dir: Path


def load_runtime_configuration(profile_id: str | None = None) -> RuntimeConfiguration:
    """Load the only active environment and resolve every domain configuration explicitly."""

    settings = load_settings()
    profile = load_trading_profile(profile_id, config_dir=settings.profile_config_dir)
    return resolve_runtime_configuration(settings=settings, profile=profile)


def resolve_runtime_configuration(
    *,
    settings: Settings,
    profile: TradingProfile,
) -> RuntimeConfiguration:
    """Resolve domain configuration against an already loaded environment and profile.

    Supplying both inputs explicitly makes this suitable for composition roots that
    have already loaded a profile, while keeping every supporting configuration
    rooted under the same validated project directory.
    """

    source = get_data_source(
        profile.data.source_id,
        path=settings.project_root / "configs" / "data" / "sources.yaml",
    )
    policy_id = profile.research_admission.policy_id
    if profile.research_admission.enabled:
        if policy_id is None:
            raise RuntimeConfigurationError(
                "CONFIG_RESEARCH_POLICY_REQUIRED: enabled research admission requires its declared policy."
            )
        policy = load_research_admission_policy(
            policy_id,
            directory=settings.project_root / "configs" / "research" / "admission",
        )
    else:
        policy = None
    return build_runtime_configuration(
        settings=settings,
        app=load_app_config(settings.project_root),
        profile=profile,
        data_source=source,
        research_admission_policy=policy,
    )


def build_runtime_configuration(
    *,
    settings: Settings,
    app: AppConfig,
    profile: TradingProfile,
    data_source: DataSourceConfig,
    research_admission_policy: ResearchAdmissionPolicy | None,
    intelligence_ontology_dir: str | Path | None = None,
) -> RuntimeConfiguration:
    """Validate cross-domain identity, data authority and research-policy bindings."""

    if profile.data.provider != data_source.adapter_id:
        raise RuntimeConfigurationError(
            "CONFIG_DATA_ADAPTER_MISMATCH: profile.data.provider must match "
            "the declared data-source adapter_id."
        )
    if not data_source.supports(
        market=profile.market.value,
        asset_type=profile.asset_type.value,
        frequency=profile.data_frequency.value,
    ):
        raise RuntimeConfigurationError(
            "CONFIG_DATA_SCOPE_MISMATCH: the data source does not support the profile dimensions."
        )

    policy = _validate_research_admission(
        profile=profile,
        data_source=data_source,
        policy=research_admission_policy,
    )
    ontology_dir = _validate_ontology_directory(
        intelligence_ontology_dir or settings.project_root / "ontology"
    )
    if settings.storage_dir != app.runtime.storage_dir:
        raise RuntimeConfigurationError(
            "CONFIG_RUNTIME_STORAGE_MISMATCH: Settings and active app runtime disagree."
        )
    if settings.reports_dir != app.runtime.reports_dir or settings.log_dir != app.runtime.log_dir:
        raise RuntimeConfigurationError(
            "CONFIG_RUNTIME_OUTPUT_MISMATCH: Settings and active app runtime disagree."
        )

    return RuntimeConfiguration(
        settings=settings,
        app=app,
        profile=profile,
        data_source=data_source,
        research_admission_policy=policy,
        intelligence_ontology_dir=ontology_dir,
    )


def _validate_research_admission(
    *,
    profile: TradingProfile,
    data_source: DataSourceConfig,
    policy: ResearchAdmissionPolicy | None,
) -> ResearchAdmissionPolicy | None:
    binding = profile.research_admission
    if not binding.enabled:
        if policy is not None:
            raise RuntimeConfigurationError(
                "CONFIG_RESEARCH_POLICY_UNEXPECTED: disabled profiles cannot load an admission policy."
            )
        return None
    if binding.policy_id is None or policy is None:
        raise RuntimeConfigurationError(
            "CONFIG_RESEARCH_POLICY_REQUIRED: enabled research admission requires its declared policy."
        )
    if policy.policy_id != binding.policy_id or policy.status != "active":
        raise RuntimeConfigurationError(
            "CONFIG_RESEARCH_POLICY_INVALID: the declared admission policy must be active and identity-matched."
        )
    if (
        policy.scope.market != profile.market.value
        or policy.scope.asset_type != profile.asset_type.value
        or policy.universe.universe_id != profile.universe_id
        or policy.source.required_source_id != data_source.source_id
    ):
        raise RuntimeConfigurationError(
            "CONFIG_RESEARCH_SCOPE_MISMATCH: admission policy must match the profile and source."
        )
    return policy


def _validate_ontology_directory(value: str | Path) -> Path:
    directory = Path(value).resolve()
    missing = sorted(
        filename for filename in _REQUIRED_ONTOLOGY_FILES if not (directory / filename).is_file()
    )
    if missing:
        raise RuntimeConfigurationError(
            "CONFIG_INTELLIGENCE_ONTOLOGY_INCOMPLETE: missing " + ", ".join(missing)
        )
    return directory


__all__ = [
    "RuntimeConfiguration",
    "RuntimeConfigurationError",
    "build_runtime_configuration",
    "load_runtime_configuration",
    "resolve_runtime_configuration",
]
