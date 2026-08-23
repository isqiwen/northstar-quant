"""Contracts for the consolidated documentation surface."""

from __future__ import annotations

import ast
from pathlib import Path
import re

from northstar_quant.foundation.config.settings import Settings
from tests.helpers.paths import PROJECT_ROOT


README_PATH = PROJECT_ROOT / "README.md"
DOCS_DIR = PROJECT_ROOT / "docs"
DOCS_INDEX_PATH = DOCS_DIR / "README.md"
ARCHITECTURE_PATH = DOCS_DIR / "ARCHITECTURE.md"
DEVELOPMENT_PATH = DOCS_DIR / "DEVELOPMENT.md"
OPERATIONS_PATH = DOCS_DIR / "OPERATIONS.md"
GOVERNANCE_PATH = DOCS_DIR / "GOVERNANCE.md"
PLANNING_INDEX_PATH = DOCS_DIR / "planning" / "README.md"
MASTER_PLAN_PATH = DOCS_DIR / "planning" / "MASTER_IMPLEMENTATION_PLAN.md"
LOCAL_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

CANONICAL_DOCUMENTS = (
    "ARCHITECTURE.md",
    "DEVELOPMENT.md",
    "OPERATIONS.md",
    "GOVERNANCE.md",
)
RETIRED_DOCUMENTS = (
    "00_第一个策略与回测教程.md",
    "01_架构总览.md",
    "02_配置说明.md",
    "03_执行与安全边界.md",
    "04_期货回测器说明.md",
    "05_报告_PDF与通知.md",
    "06_代码与配置注释规范.md",
    "07_Linux一键部署.md",
    "08_项目主规划与实施状态.md",
    "09_研究准入政策与数据治理.md",
    "10_AI研究工具边界.md",
    "platform_security_audit.md",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _fenced_python_blocks(markdown: str) -> list[str]:
    return re.findall(r"```python\n(.*?)\n```", markdown, flags=re.DOTALL)


def _markdown_files() -> list[Path]:
    return sorted(
        path
        for path in PROJECT_ROOT.rglob("*.md")
        if ".venv" not in path.parts and ".git" not in path.parts
    )


def _local_link_target(markdown_path: Path, raw_target: str) -> Path | None:
    target = raw_target.split("#", maxsplit=1)[0].strip().strip("<>")
    if not target or target.startswith(("https://", "http://", "mailto:")):
        return None
    return (markdown_path.parent / target).resolve()


def test_docs_index_is_the_only_canonical_navigation() -> None:
    readme = _read(README_PATH)
    docs_index = _read(DOCS_INDEX_PATH)

    assert "[文档导航](docs/README.md)" in readme
    for filename in CANONICAL_DOCUMENTS:
        assert (DOCS_DIR / filename).is_file()
        assert f"]({filename})" in docs_index
    assert "](planning/README.md)" in docs_index
    for filename in RETIRED_DOCUMENTS:
        assert not (DOCS_DIR / filename).exists()


def test_consolidated_documents_have_single_responsibility() -> None:
    architecture = _read(ARCHITECTURE_PATH)
    development = _read(DEVELOPMENT_PATH)
    operations = _read(OPERATIONS_PATH)
    governance = _read(GOVERNANCE_PATH)

    assert "长期软件架构" in architecture
    assert "开发环境、代码约定、第一条研究路径" in development
    assert "运行、配置、报告、部署和数据保全" in operations
    assert "数据授权、研究准入、AI 权限、安全审计和人工控制" in governance
    assert "唯一实施进度事实来源" in _read(PLANNING_INDEX_PATH)
    assert MASTER_PLAN_PATH.is_file()


def test_architecture_document_excludes_planning_and_acceptance_status() -> None:
    architecture = _read(ARCHITECTURE_PATH)

    assert "P10" not in architecture
    assert "P8" not in architecture
    assert "P3" not in architecture
    assert "VERIFIED_OFFLINE" not in architecture
    assert "VERIFIED_SIMULATION" not in architecture
    assert "P10 验收证据" not in architecture
    assert "Work Package" not in architecture
    assert "模块职责、依赖方向、领域语义、证据流和安全控制边界" in architecture


def test_architecture_documents_verified_core_class_relationship_diagrams() -> None:
    architecture = _read(ARCHITECTURE_PATH)
    diagrams = re.findall(
        r"```mermaid\nclassDiagram\n(.*?)\n```",
        architecture,
        flags=re.DOTALL,
    )

    domain_sections = (
        ("### Foundation\n", "### Data\n", "RuntimeConfiguration"),
        ("### Data\n", "### Intelligence\n", "DatasetVersion"),
        (
            "### Intelligence\n",
            "### Research & Strategy\n",
            "IntelligenceFeatureProjectionRequest",
        ),
        ("### Research & Strategy\n", "### Portfolio & Risk\n", "ExperimentSpec"),
        (
            "### Portfolio & Risk\n",
            "### Trading & Execution\n",
            "PortfolioCompositionEvidence",
        ),
        ("### Trading & Execution\n", "## 4. 跨领域证据流\n", "ExecutionPlan"),
    )
    assert "## 3. 六个领域" in architecture
    assert "## 5. 核心类型关系图" not in architecture
    assert len(diagrams) == len(domain_sections) + 1
    for start, end, class_name in domain_sections:
        domain_section = architecture.split(start, maxsplit=1)[1].split(end, maxsplit=1)[0]
        assert "#### 核心类型关系图" in domain_section
        assert f"class {class_name}" in domain_section

    application_section = architecture.split(
        "### Application：跨领域 composition root\n", maxsplit=1
    )[1].split("## 3. 六个领域\n", maxsplit=1)[0]
    assert "class CtpSimCandidateExecutor" in application_section

    source_classes = {
        "src/northstar_quant/foundation/config/runtime_configuration.py": (
            "RuntimeConfiguration",
        ),
        "src/northstar_quant/data/contracts/data_domain.py": (
            "Artifact",
            "NormalizedArtifact",
            "DatasetVersion",
        ),
        "src/northstar_quant/intelligence/domain/models.py": ("Event",),
        "src/northstar_quant/intelligence/feature_projection/projection.py": (
            "IntelligenceFeatureProjectionRequest",
            "IntelligenceFeatureProjector",
            "VersionedIntelligenceFeatureProjection",
        ),
        "src/northstar_quant/research/features/models.py": (
            "FeatureVersion",
            "FeatureLineage",
            "FeatureBackfill",
        ),
        "src/northstar_quant/research/experiments/models.py": (
            "ExperimentSpec",
            "ExperimentRun",
        ),
        "src/northstar_quant/portfolio_risk/portfolio/composition.py": (
            "CanonicalPortfolioComposer",
            "PortfolioCompositionEvidence",
        ),
        "src/northstar_quant/portfolio_risk/portfolio/approval.py": (
            "ApprovedPortfolioTarget",
        ),
        "src/northstar_quant/trading_execution/execution/plan.py": ("ExecutionPlan",),
        "src/northstar_quant/trading_execution/live/plan_gate.py": ("PlanPreTradeGate",),
        "src/northstar_quant/trading_execution/orders/durable_submission.py": (
            "DurableBrokerAdapter",
        ),
        "src/northstar_quant/application/research_strategy_activation.py": (
            "ResearchStrategyTargetActivator",
        ),
        "src/northstar_quant/application/execution_provenance_preflight.py": (
            "ExecutionProvenancePreflight",
        ),
        "src/northstar_quant/application/ctp_sim_candidate_execution.py": (
            "CtpSimCandidateExecutor",
        ),
    }
    for relative_path, class_names in source_classes.items():
        source = _read(PROJECT_ROOT / relative_path)
        for class_name in class_names:
            assert f"class {class_name}" in source
            assert f"class {class_name}" in architecture

    for relation in (
        "RuntimeConfiguration o-- Settings : settings",
        "DatasetVersion *-- ArtifactSnapshot : artifact_snapshots",
        "Event *-- Evidence : evidence",
        "ExperimentSpec --> ExperimentFeatureInput : feature_inputs",
        "PortfolioCompositionEvidence *-- PortfolioTarget : portfolio_target",
        "PlanPreTradeGate o-- PreflightResult : preflight",
        "CtpSimCandidateExecutionBundle *-- ExecutionProvenancePreflightReceipt : receipt",
    ):
        assert relation in architecture


def test_architecture_places_domain_semantics_with_owning_modules() -> None:
    architecture = _read(ARCHITECTURE_PATH)

    assert "## 3. 不可合并的领域语义" not in architecture
    data_section = architecture.split("### Data\n", maxsplit=1)[1].split(
        "### Intelligence\n", maxsplit=1
    )[0]
    trading_section = architecture.split("### Trading & Execution\n", maxsplit=1)[1].split(
        "## 4. 跨领域证据流\n", maxsplit=1
    )[0]
    assert "`Commodity` 是经济品种，`Instrument` 是可交易标的，`Contract` 是具体可交易合约" in data_section
    assert "`Fill` 是外部成交事实，不等同于 `ClosedTrade` 或收益结论" in trading_section


def test_local_markdown_links_resolve() -> None:
    broken_links: list[str] = []
    for markdown_path in _markdown_files():
        for raw_target in LOCAL_LINK_PATTERN.findall(_read(markdown_path)):
            target = _local_link_target(markdown_path, raw_target)
            if target is not None and not target.exists():
                broken_links.append(f"{markdown_path.relative_to(PROJECT_ROOT)} -> {raw_target}")

    assert not broken_links, "失效的仓库内 Markdown 链接：\n" + "\n".join(broken_links)


def test_retired_document_names_have_no_remaining_references() -> None:
    remnants: list[str] = []
    for markdown_path in _markdown_files():
        content = _read(markdown_path)
        for filename in RETIRED_DOCUMENTS:
            if filename in content:
                remnants.append(f"{markdown_path.relative_to(PROJECT_ROOT)} -> {filename}")

    assert not remnants, "已收敛文档仍被引用：\n" + "\n".join(remnants)


def test_user_facing_uv_run_commands_cannot_implicitly_materialize_dependencies() -> None:
    documentation_paths = (
        README_PATH,
        PROJECT_ROOT / "AGENTS.md",
        *_markdown_files(),
        *(path for path in (PROJECT_ROOT / "configs" / "profiles").rglob("README.md")),
        PROJECT_ROOT / "tests" / "README.md",
        PROJECT_ROOT / "infra" / "docker" / "README.md",
    )
    violations: list[str] = []
    for path in dict.fromkeys(documentation_paths):
        for line_number, raw_line in enumerate(_read(path).splitlines(), start=1):
            command = raw_line.strip()
            if command.startswith("uv run ") and not command.startswith(
                "uv run --offline --no-sync "
            ):
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{line_number}: {command}")

    assert not violations, "用户文档包含会隐式 materialize 依赖的 uv run 命令：\n" + "\n".join(
        violations
    )


def test_root_readme_links_to_control_plane_without_a_stale_roadmap() -> None:
    readme = _read(README_PATH)

    assert "[主实施计划](docs/planning/MASTER_IMPLEMENTATION_PLAN.md)" in readme
    assert "P10 已完成 7/9 个 Work Package（78%）" in readme
    assert "唯一实施进度事实来源" in _read(PLANNING_INDEX_PATH)
    assert "DOC-WP01" in _read(MASTER_PLAN_PATH)


def test_configuration_documentation_matches_safe_runtime_defaults() -> None:
    operations = _read(OPERATIONS_PATH)

    for required in (
        "scripts/dev/setup.py",
        "configs/data/sources.yaml",
        "research/admission",
        "ctp_sim",
        "runtime.downloads_dir",
        "runtime.log_dir",
        "configs/app.example.yaml",
        "configs/app.yaml",
        "configs/app.local.yaml",
        "northstar data cleanup",
        "northstar ops backup status",
        "database_backup_readiness.yaml",
        "backup_bundle.py",
        "restore_drill.py",
        "futures.calendar_artifact_snapshot_hashes",
        "ArtifactSnapshot",
        "PostgreSQL-only",
    ):
        assert required in operations
    assert (PROJECT_ROOT / "configs" / "maintenance" / "output_retention.yaml").is_file()
    assert (
        PROJECT_ROOT / "configs" / "maintenance" / "database_backup_readiness.yaml"
    ).is_file()
    assert Settings.model_fields["broker"].default == "paper"
    assert Settings.model_fields["live_trading_enabled"].default is False
    assert Settings.model_fields["kill_switch_enabled"].default is False
    assert not (PROJECT_ROOT / "configs" / "risk" / "global.yaml").exists()
    assert not (PROJECT_ROOT / "configs" / "portfolio" / "multi_strategy.yaml").exists()


def test_calendar_docs_keep_runtime_sources_fail_closed() -> None:
    architecture = _read(ARCHITECTURE_PATH)
    operations = _read(OPERATIONS_PATH)
    simulated = _read(
        PROJECT_ROOT / "configs" / "profiles" / "simulated" / "README.md"
    )
    calendar_readme = PROJECT_ROOT / "configs" / "calendars" / "README.md"

    assert "Trading Calendar" in architecture
    assert "test_only" in architecture
    assert "futures.calendar_artifact_snapshot_hashes" in architecture
    assert "runtime Calendar Artifact" in operations
    assert "TRADING_CALENDAR_ARTIFACT_REQUIRED" in simulated
    assert calendar_readme.is_file()
    assert "没有可运行的日历制品" in _read(calendar_readme)


def test_architecture_preserves_non_trading_submission_boundary() -> None:
    architecture = _read(ARCHITECTURE_PATH)
    governance = _read(GOVERNANCE_PATH)
    scripts = _read(PROJECT_ROOT / "scripts" / "README.md")

    for required in (
        "ResearchStrategyTargetActivator",
        "StrategyTargetActivationRef",
        "ExecutionProvenancePreflight",
        "CtpSimCandidateExecutor",
        "eligible_for_broker_order=false",
        "CTP_REAL_FRONT_DISABLED",
    ):
        assert required in architecture
    assert "不得 approve、enable-live、resume-risk、submit、连接 broker" in governance
    assert "opaque-authority `Portfolio/Risk→ctp_sim`" in scripts
    assert "P8_RESEARCH_TO_PORTFOLIO_RISK" in scripts
    assert "P8_EXECUTION_PROVENANCE_PREFLIGHT" in scripts
    assert "P8_CTP_SIM_CANDIDATE_E2E" in scripts


def test_development_python_example_is_parseable_and_describes_flat_targets() -> None:
    development = _read(DEVELOPMENT_PATH)
    python_blocks = _fenced_python_blocks(development)

    assert python_blocks
    for index, source in enumerate(python_blocks, start=1):
        ast.parse(source, filename=f"{DEVELOPMENT_PATH.name}:python-block-{index}")
    assert "target_weight: 0.0" in development
    assert "uv run --offline --no-sync northstar backtest run portfolio" in development
