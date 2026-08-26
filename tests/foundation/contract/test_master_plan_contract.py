"""Codex 主实施计划的路径、入口与状态追踪契约。"""

from __future__ import annotations

from tests.helpers.paths import PROJECT_ROOT


MASTER_PLAN = PROJECT_ROOT / "docs" / "planning" / "MASTER_IMPLEMENTATION_PLAN.md"


def test_master_plan_is_at_the_single_authoritative_path() -> None:
    assert MASTER_PLAN.is_file()
    assert not (
        PROJECT_ROOT / "docs" / "planning" / "Northstar_Quant_Codex_Master_Implementation_Plan.md"
    ).exists()


def test_agents_and_readme_link_the_same_master_plan() -> None:
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "docs/planning/MASTER_IMPLEMENTATION_PLAN.md" in agents
    assert "(docs/planning/MASTER_IMPLEMENTATION_PLAN.md)" in readme


def test_master_plan_tracks_completed_wp_and_current_work() -> None:
    plan = MASTER_PLAN.read_text(encoding="utf-8")
    wp04 = plan.split("## P0-WP04 — Platform Support Contract", maxsplit=1)[1].split(
        "# 10. P1 — Data Platform", maxsplit=1
    )[0]
    wp01 = plan.split("## P1-WP01 — Data Domain Core", maxsplit=1)[1].split(
        "## P1-WP02", maxsplit=1
    )[0]
    wp02 = plan.split("## P1-WP02 — Artifact Storage", maxsplit=1)[1].split(
        "## P1-WP03", maxsplit=1
    )[0]
    wp03 = plan.split("## P1-WP03 — Contract Master", maxsplit=1)[1].split(
        "## P1-WP04", maxsplit=1
    )[0]
    wp05 = plan.split("## P1-WP05 — Data Quality Engine", maxsplit=1)[1].split(
        "## P1-WP06", maxsplit=1
    )[0]
    wp06 = plan.split("## P1-WP06 — Data Source Adapter Protocol", maxsplit=1)[1].split(
        "## P1-WP07", maxsplit=1
    )[0]
    wp07 = plan.split("## P1-WP07 — Market Data PIT Snapshot", maxsplit=1)[1].split(
        "## P1-WP08", maxsplit=1
    )[0]
    wp08 = plan.split("## P1-WP08 — Data Platform E2E", maxsplit=1)[1].split(
        "# 11. P2", maxsplit=1
    )[0]

    assert "## P0-WP01 — Master Plan 追踪机制" in plan
    assert "**Status:** DONE" in plan
    assert "## P0-WP02 — 六领域依赖契约" in plan
    assert "P0-WP02：六领域运行时依赖契约" in plan
    assert "## P0-WP03 — scripts / infra / just" in plan
    assert "P0-WP03：scripts / infra / just、跨平台开发工具 bootstrap" in plan
    assert "## P0-WP04 — Platform Support Contract" in plan
    assert "## P1-WP01 — Data Domain Core" in plan
    assert "## P1-WP02 — Artifact Storage" in plan
    assert "P1-WP02：追加式不可变制品库" in plan
    assert "## P1-WP03 — Contract Master" in plan
    assert "P1-WP03：Contract Master、PIT 规则解析与连续研究序列执行门禁" in plan
    assert "P1-WP05：canonical payload 绑定的预发布数据质量引擎" in plan
    assert "P1-WP06：受控数据源适配器协议、授权重验" in plan
    assert "**Status:** DONE" in wp04
    assert "**Status:** DONE" in wp01
    assert "**Status:** DONE" in wp02
    assert "**Status:** DONE" in wp03
    assert "**Status:** DONE" in wp05
    assert "**Status:** DONE" in wp06
    assert "**Status:** DONE" in wp07
    assert "**Status:** DONE" in wp08
    assert "P1-WP08：真实质量引擎驱动的离线 Source→Raw→Normalize" in plan
    assert "| P1 | Data Platform | DONE | 100% |" in plan
    assert "active_phase: P10" in plan
    assert "| P2 | Research & Strategy Platform | DONE | 100% |" in plan
    assert "| P6 | Platform Foundation & Automation | DONE | 100% |" in plan
    assert "| P7 | AI-assisted Research Automation | DONE | 100% |" in plan
    assert "| P8 | Integrated Production Candidate | DONE | 100% |" in plan
    assert "| P9 | Hardening / Performance / Security | DONE | 100% |" in plan
    assert "| P10 | Mature v1 Acceptance | IN_PROGRESS | 78% |" in plan
    assert "P10 已完成 `7/9` 个 Work Package（78%）" in plan
    assert (
        "active_work_package:\n"
        "  id: null\n"
        "  title: null\n"
        "  status: null"
    ) in plan
    assert "id: P10-WP08" in plan
    assert ("title: Platform Production / DR Acceptance\n  status: BLOCKED") in plan
    assert "## DEV-WP01 — Development Alembic Baseline Consolidation" in plan
    dev_wp01 = plan.split("## DEV-WP01", maxsplit=1)[1].split("## DOC-WP01", maxsplit=1)[0]
    assert "**Status:** IN_PROGRESS" in dev_wp01
    assert "0001_current_schema_baseline" in dev_wp01
    assert "P10-WP08 `BLOCKED`" in dev_wp01
    assert "1738 passed, 4 failed" in dev_wp01
    dev_wp02 = plan.split("## DEV-WP02", maxsplit=1)[1].split("## DEV-WP03", maxsplit=1)[0]
    assert "**Status:** VERIFY" in dev_wp02
    assert "PostgreSQL / Parquet / DuckDB / SQLite" in dev_wp02
    assert "1739 passed, 4 failed" in dev_wp02
    assert "## DEV-WP03 — PostgreSQL Trading-State Authority" in plan
    dev_wp03 = plan.split("## DEV-WP03", maxsplit=1)[1].split("## DEV-WP04", maxsplit=1)[0]
    assert "**Status:** VERIFY" in dev_wp03
    assert "不连接真实 CTP" in dev_wp03
    assert "1744 passed, 4 failed, 1 deselected" in dev_wp03
    assert "## DEV-WP04 — PostgreSQL Contract Authority" in plan
    assert "## DEV-WP05 — Historical Parquet Lake & DuckDB Analytics" in plan
    dev_wp05 = plan.split("## DEV-WP05", maxsplit=1)[1].split("## DEV-WP06", maxsplit=1)[0]
    assert "**Status:** DONE" in dev_wp05
    assert "DatasetVersion" in dev_wp05
    assert "## DEV-WP06 — SQLite Local-tools Manifest Index" in plan
    dev_wp06 = plan.split("## DEV-WP06", maxsplit=1)[1].split("## DOC-WP01", maxsplit=1)[0]
    assert "**Status:** DONE" in dev_wp06
    assert "Lake manifest" in dev_wp06
    assert "1772 passed, 4 known unrelated failures, 1 deselected" in dev_wp06
    assert "## DOC-WP01 — Documentation Consolidation & Architecture Specification" in plan
    assert (
        "**Status:** DONE" in plan.split("## DOC-WP01", maxsplit=1)[1].split("# 20.", maxsplit=1)[0]
    )
    assert "## DOC-WP02 — Foundation / Data Module Rename" in plan
    assert (
        "**Status:** DONE" in plan.split("## DOC-WP02", maxsplit=1)[1].split("# 20.", maxsplit=1)[0]
    )
    maintenance = plan.split("## MAINT-WP01", maxsplit=1)[1].split("# 20.", maxsplit=1)[0]
    assert "**Status:** DONE" in maintenance
    assert "northstar_quant.platform" in maintenance
    assert "1787 passed, 1 failed, 1 deselected" in maintenance
    assert "pg_dump、pg_restore 与 psql" in maintenance
    assert "## DOC-WP03 — Module Class Relationship Diagrams" in plan
    assert (
        "**Status:** DONE" in plan.split("## DOC-WP03", maxsplit=1)[1].split("# 20.", maxsplit=1)[0]
    )
    assert "## DOC-WP04 — Architecture Diagram Placement" in plan
    doc_wp04 = plan.split("## DOC-WP04", maxsplit=1)[1].split("# 20.", maxsplit=1)[0]
    assert "**Status:** DONE" in doc_wp04
    assert "application 图位于 `application/` composition-root 的说明中" in doc_wp04
    assert "## DOC-WP05 — Domain Semantics Consolidation" in plan
    doc_wp05 = plan.split("## DOC-WP05", maxsplit=1)[1].split("# 20.", maxsplit=1)[0]
    assert "**Status:** DONE" in doc_wp05
    assert "Commodity/Instrument/Contract 约束放入 Data" in doc_wp05
    assert "## DOC-WP06 — Simulation and Live Data Flows" in plan
    doc_wp06 = plan.split("## DOC-WP06", maxsplit=1)[1].split("# 20.", maxsplit=1)[0]
    assert "**Status:** DONE" in doc_wp06
    assert "分别包含 `paper`、`ctp_sim` 与真实 CTP fail-closed 的完整流图" in doc_wp06
    assert "CTP_REAL_FRONT_DISABLED" in doc_wp06
    assert "未启用 real CTP、账户、凭据或实盘操作" in doc_wp06
    assert "## DOC-WP07 — Unified Local Initialization Entry" in plan
    doc_wp07 = plan.split("## DOC-WP07", maxsplit=1)[1].split("# 20.", maxsplit=1)[0]
    assert "**Status:** DONE" in doc_wp07
    assert "`just setup`" in doc_wp07
    assert "`just setup-postgres`" in doc_wp07
    assert "P10-WP08 与 P10-WP09 均需外部前提" in plan
    wp01_p7 = plan.split("## P7-WP01 — Typed Tool API", maxsplit=1)[1].split(
        "## P7-WP02", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp01_p7
    assert "TypedResearchToolApi" in wp01_p7
    assert "eligible_for_trading=False" in wp01_p7
    wp02_p7 = plan.split("## P7-WP02 — Research Agent", maxsplit=1)[1].split(
        "## P7-WP03", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp02_p7
    assert "ResearchAgent(TypedResearchToolApi)" in wp02_p7
    assert "RESEARCH_ONLY" in wp02_p7
    wp03_p7 = plan.split("## P7-WP03 — Intelligence Agent", maxsplit=1)[1].split(
        "## P7-WP04", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp03_p7
    assert "TypedResearchToolApi.invoke(ToolName.SEARCH_EVENTS" in wp03_p7
    assert "eligible_for_trading=False" in wp03_p7
    assert "P7 intelligence-agent focused suite — 69 passed" in wp03_p7
    wp04_p7 = plan.split("## P7-WP04 — Data Quality Agent", maxsplit=1)[1].split(
        "## P7-WP05", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp04_p7
    assert "inspect_dataset_quality" in wp04_p7
    assert "DIAGNOSTIC_ONLY" in wp04_p7
    assert "P7 data-quality focused suite — 92 passed" in wp04_p7
    wp05_p7 = plan.split("## P7-WP05 — Ops Agent", maxsplit=1)[1].split("# 17. P8", maxsplit=1)[0]
    assert "**Status:** DONE" in wp05_p7
    assert "TypedOpsToolApi" in wp05_p7
    assert "P7 ops focused suite — 49 passed" in wp05_p7
    p8 = plan.split("# 17. P8 — Integrated Production Candidate", maxsplit=1)[1].split(
        "# 18. P9", maxsplit=1
    )[0]
    assert "**Status:** DONE" in p8
    assert "## P8-WP01 — Integrated Candidate Acceptance Harness" in p8
    wp01_p8 = p8.split("## P8-WP01 — Integrated Candidate Acceptance Harness", maxsplit=1)[1].split(
        "## P8-WP02", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp01_p8
    assert "CandidateAcceptanceVerifier" in wp01_p8
    assert "CANDIDATE_EVIDENCE_ONLY" in wp01_p8
    assert "245 passed, 7 skipped" in wp01_p8
    wp02_p8 = p8.split("## P8-WP02 — Intelligence-to-Research Feature Projection", maxsplit=1)[
        1
    ].split("## P8-WP03", maxsplit=1)[0]
    assert "**Status:** DONE" in wp02_p8
    assert "intelligence_feature_projection_v3" in wp02_p8
    assert "1402 passed, 21 skipped" in wp02_p8
    wp03_p8 = p8.split("## P8-WP03", maxsplit=1)[1].split("## P8-WP04", maxsplit=1)[0]
    assert "**Status:** DONE" in wp03_p8
    assert "ResearchStrategyTargetActivator" in wp03_p8
    assert "HumanStrategyTargetActivationApproval" in wp03_p8
    assert "RESEARCH_TO_PORTFOLIO_RISK" in wp03_p8
    assert "decision_time_safe=false" in wp03_p8
    assert "eligible_for_trading=false" in wp03_p8
    wp04_p8 = p8.split("## P8-WP04", maxsplit=1)[1].split("## P8-WP05", maxsplit=1)[0]
    assert "**Status:** DONE" in wp04_p8
    assert "ExecutionProvenancePreflight" in wp04_p8
    assert "eligible_for_ctp_sim=false" in wp04_p8
    assert "PORTFOLIO_RISK_TO_EXECUTION_SIMULATION" in wp04_p8
    wp05_p8 = p8.split("## P8-WP05", maxsplit=1)[1].split("# 18. P9", maxsplit=1)[0]
    assert "**Status:** DONE" in wp05_p8
    assert "CtpSimSubmissionAuthority" in wp05_p8
    assert "1452 passed, 21 skipped" in wp05_p8
    p9 = plan.split("# 18. P9 — Hardening / Performance / Security", maxsplit=1)[1].split(
        "# 19. P10", maxsplit=1
    )[0]
    wp01_p9 = p9.split("## P9-WP01 — Offline Supply-Chain & Credential Gate", maxsplit=1)[1].split(
        "## P9-WP02", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp01_p9
    assert "offline integrity/policy scan" in wp01_p9
    assert "uv lock --check --offline" in wp01_p9
    assert "1505 passed, 22 skipped" in wp01_p9
    wp02_p9 = p9.split("## P9-WP02 — Hermetic PEP 517 Build Bootstrap", maxsplit=1)[1]
    assert "**Status:** DONE" in wp02_p9
    assert "fresh virtual environment" in wp02_p9
    assert "1528 passed, 23 skipped" in wp02_p9
    p10 = plan.split("# 19. P10 — Mature v1 总验收", maxsplit=1)[1].split(
        "# 20. Codex Work Package 标准模板", maxsplit=1
    )[0]
    wp01_p10 = p10.split("## P10-WP01 — Mature v1 Acceptance Evidence Baseline", maxsplit=1)[
        1
    ].split("## Data", maxsplit=1)[0]
    assert "**Status:** DONE" in wp01_p10
    assert "48 项 P10 evidence register" in wp01_p10
    wp02_p10 = p10.split("## P10-WP02 — Six-Commodity Intelligence Evidence Corpus", maxsplit=1)[
        1
    ].split("## P10-WP03", maxsplit=1)[0]
    assert "**Status:** DONE" in wp02_p10
    assert "fixture-only" in wp02_p10
    assert "Feature-definition handoff" in wp02_p10
    wp03_p10 = p10.split(
        "## P10-WP03 — Intelligence Feature Research Backtest Evidence", maxsplit=1
    )[1].split("## P10-WP04", maxsplit=1)[0]
    assert "**Status:** DONE" in wp03_p10
    assert "FIXTURE_ONLY_INTELLIGENCE_REPLAY" in wp03_p10
    assert "synthetic outcome" in wp03_p10
    wp04_p10 = p10.split(
        "## P10-WP04 — Canonical Multi-Strategy Portfolio Composition", maxsplit=1
    )[1].split("## P10-WP05", maxsplit=1)[0]
    assert "**Status:** DONE" in wp04_p10
    assert "CanonicalPortfolioComposer" in wp04_p10
    assert "PortfolioTarget v2" in wp04_p10
    assert "composition_hash" in wp04_p10
    assert "unallocated cash" in wp04_p10
    wp05_p10 = p10.split("## P10-WP05 — Portfolio-Wide Risk Evidence & Approval Gate", maxsplit=1)[
        1
    ].split("## P10-WP06", maxsplit=1)[0]
    assert "**Status:** DONE" in wp05_p10
    assert "VERIFIED_SIMULATION" in wp05_p10
    assert "0008_portfolio_risk_approval" in wp05_p10
    wp06_p10 = p10.split("## P10-WP06 — Durable Agent Evidence Audit", maxsplit=1)[1].split(
        "## P10-WP07", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp06_p10
    assert "0010_agent_run_audit_hardening" in wp06_p10
    wp07_p10 = p10.split("## P10-WP07 — Trading Acceptance Evidence Closure", maxsplit=1)[1].split(
        "## P10-WP08", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp07_p10
    assert "P10_TRADING_FAILURE_MATRIX.md" in wp07_p10
    assert "broker.submit_order" in wp07_p10
    assert "SAFE_BOUNDARY" in wp07_p10
    wp01_p6 = plan.split("## P6-WP01 — Config Unification", maxsplit=1)[1].split(
        "## P6-WP02", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp01_p6
    wp02_p6 = plan.split("## P6-WP02 — Messaging Abstraction", maxsplit=1)[1].split(
        "## P6-WP03", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp02_p6
    wp03_p6 = plan.split("## P6-WP03 — Scheduling", maxsplit=1)[1].split("## P6-WP04", maxsplit=1)[
        0
    ]
    assert "**Status:** DONE" in wp03_p6
    wp04_p6 = plan.split("## P6-WP04 — Observability", maxsplit=1)[1].split(
        "## P6-WP05", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp04_p6
    wp05_p6 = plan.split("## P6-WP05 — Security", maxsplit=1)[1].split("## P6-WP06", maxsplit=1)[0]
    assert "**Status:** DONE" in wp05_p6
    wp06_p6 = plan.split("## P6-WP06 — Cross-platform Deployment Control", maxsplit=1)[1].split(
        "## P6-WP07", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp06_p6
    wp07_p6 = plan.split("## P6-WP07 — Linux Production Layout", maxsplit=1)[1].split(
        "## P6-WP08", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp07_p6
    assert "root-only deploy-state" in wp07_p6
    wp08_p6 = plan.split("## P6-WP08 — Backup / Restore", maxsplit=1)[1].split(
        "## P6-WP09", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp08_p6
    assert "Linux container no-replace publication probe" in wp08_p6
    wp09_p6 = plan.split("## P6-WP09 — Release Pipeline", maxsplit=1)[1].split(
        "# 16. P7", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp09_p6
    assert "fixed root-owned signed release gate" in wp09_p6
    wp01_p2 = plan.split("## P2-WP01 — Feature Registry", maxsplit=1)[1].split(
        "## P2-WP02", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp01_p2
    assert "FeatureComputer" in wp01_p2
    assert "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY" in wp01_p2
    wp02_p2 = plan.split("## P2-WP02 — Canonical Feature Families", maxsplit=1)[1].split(
        "## P2-WP03", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp02_p2
    assert "technical.open_interest_change" in wp02_p2
    assert "cn_futures_actual_contract_feature_bar_v1" in wp02_p2
    assert "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY" in wp02_p2
    wp03_p2 = plan.split("## P2-WP03 — Experiment Model", maxsplit=1)[1].split(
        "## P2-WP04", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp03_p2
    assert "ExperimentRegistry" in wp03_p2
    assert "STATIC_REPRODUCIBILITY_ONLY" in wp03_p2
    assert "eligible_for_admission=false" in wp03_p2
    wp04_p2 = plan.split("## P2-WP04 — Backtest Interface Unification", maxsplit=1)[1].split(
        "## P2-WP05", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp04_p2
    assert "RunManifest v4" in wp04_p2
    assert "candidate_admission_eligible" in wp04_p2
    wp05_p2 = plan.split("## P2-WP05 — Lookahead Guard", maxsplit=1)[1].split(
        "## P2-WP06", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp05_p2
    assert "Artifact RuleBook" in wp05_p2
    wp06_p2 = plan.split("## P2-WP06 — Validation Framework", maxsplit=1)[1].split(
        "## P2-WP07", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp06_p2
    assert "Monte Carlo" in wp06_p2
    wp07_p2 = plan.split("## P2-WP07", maxsplit=1)[1].split("## P2-WP08", maxsplit=1)[0]
    assert "**Status:** DONE" in wp07_p2
    assert "named human approval" in wp07_p2
    wp08_p2 = plan.split("## P2-WP08", maxsplit=1)[1].split("## P2-WP09", maxsplit=1)[0]
    assert "**Status:** DONE" in wp08_p2
    assert "RunManifest" in wp08_p2
    wp09_p2 = plan.split("## P2-WP09", maxsplit=1)[1].split("# 12. P3", maxsplit=1)[0]
    assert "**Status:** DONE" in wp09_p2
    assert "Research Card" in wp09_p2
    wp04_p1 = plan.split("## P1-WP04 — Trading Calendar", maxsplit=1)[1].split(
        "## P1-WP05", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp04_p1
