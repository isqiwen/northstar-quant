"""公共入口与包边界的回归测试。"""

from __future__ import annotations

from tests.architecture._imports import BUSINESS_DOMAINS, PACKAGE_ROOT, PROJECT_ROOT


LEGACY_TOP_LEVEL_PACKAGES = frozenset(
    {
        "backtest",
        "common",
        "config",
        "data",
        "db",
        "execution",
        "indicators",
        "live",
        "logging_",
        "monitoring",
        "performance",
        "portfolio",
        "reporting",
        "risk",
        "strategies",
    }
)

COMPOSITION_MODULES = {
    "cli.py",
    "reporting.py",
    "dashboard.py",
    "health.py",
    "backtest.py",
    "live_service.py",
    "target_service.py",
    "scheduler.py",
    "decision_replay_backtest.py",
    "agent_tools.py",
    "ops_tools.py",
    "research_agent.py",
    "intelligence_agent.py",
    "data_quality_agent.py",
    "ops_agent.py",
    "candidate_acceptance.py",
    "intelligence_feature_projection.py",
    "research_strategy_activation.py",
    "execution_provenance_preflight.py",
    "ctp_sim_candidate_execution.py",
}

EXPECTED_SUBPACKAGES = {
    "data_platform": {
        "sources",
        "market",
        "fundamentals",
        "contracts",
        "artifacts",
        "quality",
        "calendars",
    },
    "intelligence": {
        "domain",
        "ingestion",
        "ontology",
        "extraction",
        "entity_resolution",
        "event_merge",
        "impact_graph",
        "context",
        "analogue",
        "event_study",
        "feature_projection",
    },
    "research": {"features", "experiments", "validation", "statistics", "factors", "strategies", "backtest"},
    "portfolio_risk": {"portfolio", "allocation", "exposure", "limits", "risk"},
    "trading_execution": {
        "execution",
        "live",
        "broker",
        "orders",
        "positions",
        "reconciliation",
        "settlement",
    },
    "platform": {
        "common",
        "config",
        "db",
        "messaging",
        "scheduling",
        "observability",
        "reporting",
        "security",
        "cli",
        "backup",
    },
}


def test_only_six_domains_and_application_are_top_level_packages() -> None:
    """不保留旧路径兼容别名，避免调用方绕过新的领域边界。"""

    top_level_packages = {
        path.name
        for path in PACKAGE_ROOT.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    }
    assert top_level_packages == set(BUSINESS_DOMAINS) | {"application"}, (
        "src/northstar_quant 只能暴露六领域和 application 组合层，实际为："
        f"{', '.join(sorted(top_level_packages))}"
    )
    lingering_legacy = sorted(
        package
        for package in LEGACY_TOP_LEVEL_PACKAGES
        if (PACKAGE_ROOT / package / "__init__.py").is_file()
    )
    assert not lingering_legacy, f"仍保留旧顶层兼容包：{', '.join(lingering_legacy)}"


def test_each_domain_keeps_its_declared_public_subpackages() -> None:
    """目录树本身也表达职责归属，避免将新模块放回泛化根包。"""

    missing_by_domain = {
        domain: sorted(name for name in expected if not (PACKAGE_ROOT / domain / name).is_dir())
        for domain, expected in EXPECTED_SUBPACKAGES.items()
    }
    missing_by_domain = {
        domain: missing for domain, missing in missing_by_domain.items() if missing
    }
    assert not missing_by_domain, f"领域缺少声明的子包：{missing_by_domain}"


def test_cross_domain_composition_lives_in_application() -> None:
    """CLI、报告、看板、健康检查和编排只能由 application 拥有。"""

    application_root = PACKAGE_ROOT / "application"
    assert application_root.is_dir(), "必须保留 root-level application 组合包"
    application_init = (application_root / "__init__.py").read_text(encoding="utf-8")
    assert "不是第七个业务领域" in application_init

    missing = sorted(module for module in COMPOSITION_MODULES if not (application_root / module).is_file())
    assert not missing, f"application 缺少组合入口：{', '.join(missing)}"

    forbidden_legacy_modules = (
        PACKAGE_ROOT / "platform" / "cli" / "app.py",
        PACKAGE_ROOT / "platform" / "reporting" / "report_builder.py",
        PACKAGE_ROOT / "platform" / "observability" / "monitoring" / "dashboard.py",
        PACKAGE_ROOT / "platform" / "observability" / "monitoring" / "health.py",
        PACKAGE_ROOT / "research" / "backtest" / "runner.py",
        PACKAGE_ROOT / "trading_execution" / "live" / "service.py",
        PACKAGE_ROOT / "trading_execution" / "live" / "target_service.py",
        PACKAGE_ROOT / "trading_execution" / "live" / "scheduler.py",
    )
    lingering = [str(path.relative_to(PROJECT_ROOT)) for path in forbidden_legacy_modules if path.exists()]
    assert not lingering, f"组合实现仍遗留在领域/Platform 内：{', '.join(lingering)}"


def test_console_entrypoint_uses_application_cli() -> None:
    """安装后的公共 CLI 必须走组合层，而不是 Platform。"""

    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'northstar = "northstar_quant.application.cli:app"' in pyproject
