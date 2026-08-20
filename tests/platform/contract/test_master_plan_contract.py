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
    assert "active_phase: P2" in plan
    assert "| P2 | Research & Strategy Platform | IN_PROGRESS | 44% |" in plan
    assert "active_work_package: P2-WP05" in plan
    assert "id: P2-WP05" in plan
    assert "title: Lookahead Guard\n  status: IN_PROGRESS" in plan
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
    wp04_p1 = plan.split("## P1-WP04 — Trading Calendar", maxsplit=1)[1].split(
        "## P1-WP05", maxsplit=1
    )[0]
    assert "**Status:** DONE" in wp04_p1
