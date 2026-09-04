"""Contracts for the current-only implementation control plane."""

from __future__ import annotations

import re

from tests.helpers.paths import PROJECT_ROOT

PLANNING_DIR = PROJECT_ROOT / "docs" / "planning"
MASTER_PLAN = PLANNING_DIR / "MASTER_IMPLEMENTATION_PLAN.md"


def _read_master_plan() -> str:
    return MASTER_PLAN.read_text(encoding="utf-8")


def _section(plan: str, heading: str) -> str:
    tail = plan.split(heading, maxsplit=1)[1]
    return re.split(r"\n#{2,3} ", tail, maxsplit=1)[0]


def test_master_plan_is_the_single_current_control_plane() -> None:
    assert MASTER_PLAN.is_file()
    assert {path.name for path in PLANNING_DIR.iterdir() if path.is_file()} == {
        "MASTER_IMPLEMENTATION_PLAN.md"
    }
    assert not (PLANNING_DIR / "Northstar_Quant_Codex_Master_Implementation_Plan.md").exists()


def test_agents_and_document_navigation_link_the_same_master_plan() -> None:
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    docs_index = (PROJECT_ROOT / "docs" / "README.md").read_text(encoding="utf-8")

    assert "docs/planning/MASTER_IMPLEMENTATION_PLAN.md" in agents
    assert "(docs/planning/MASTER_IMPLEMENTATION_PLAN.md)" in readme
    assert "](planning/MASTER_IMPLEMENTATION_PLAN.md)" in docs_index


def test_master_plan_tracks_the_explicitly_authorized_current_work_package() -> None:
    plan = _read_master_plan()

    assert "active_phase: P14" in plan
    assert "active_work_package: BT-02.2" in plan
    assert 'next_task: "BT-02.2 — Publish BacktestResult and Artifact-Integrity Contract"' in plan
    assert "blocked_work_packages: [P10-WP08, P10-WP09, MAINT-WP02]" in plan
    assert plan.count("**Status:** IN_PROGRESS") == 1

    expected_sections = {
        "### BT-02.2 — Publish BacktestResult and Artifact-Integrity Contract": "IN_PROGRESS",
        "### P10-WP08 — Platform Production / DR Acceptance": "BLOCKED",
        "### P10-WP09 — Authoritative Data & Source Onboarding": "BLOCKED",
        "### MAINT-WP02 — Native Linux PostgreSQL Development / Docker Removal": "BLOCKED",
    }
    assert re.findall(
        r"^(### .+?)\n\n\*\*Status:\*\* ([A-Z_]+)$",
        plan,
        flags=re.MULTILINE,
    ) == list(expected_sections.items())
    assert "completed_at:" not in plan
    for heading, status in expected_sections.items():
        section = _section(plan, heading)
        assert f"**Status:** {status}" in section
        assert "**Acceptance:**" in section
    active_section = _section(plan, "### BT-02.2 — Publish BacktestResult and Artifact-Integrity Contract")
    assert "[BT-02.2 / #20" in active_section
    assert "[BT-02 / #3" in active_section
    assert "**Fail-closed boundary:**" in active_section
    assert "### BT-02.1" not in plan
    assert "[BT-02.1 / #18" not in plan
    assert "P13" not in plan
    assert "### BT-01.1" not in plan
    assert "### P11-WP02 — Discovery Selection / OOS Release Protocol" not in plan
    assert "### P11-WP03 — Local Research Run Bundle & CLI" not in plan
    assert "### P11-WP04 — Canonical Price/Volume Factors & Robustness Study" not in plan


def test_master_plan_preserves_unfinished_safety_boundaries() -> None:
    plan = _read_master_plan()

    for required_text in (
        "NORTHSTAR_BROKER=paper",
        "NORTHSTAR_LIVE_TRADING_ENABLED=false",
        "NO LIVE ACTION",
        "NO NEW RISK",
        "available_time",
        "Docker/Compose",
        "不得自动恢复 HALT",
        "不得 delete、truncate、reset、stamp 或 downgrade",
    ):
        assert required_text in plan


def test_completed_planning_history_and_evidence_are_not_retained() -> None:
    plan = _read_master_plan()

    for retired_text in (
        "P10_MATURE_V1_ACCEPTANCE_EVIDENCE.md",
        "P10_TRADING_FAILURE_MATRIX.md",
        "### P0-WP01",
        "### P10-WP01",
        "### DEV-WP01",
        "### DEV-WP02",
        "### DEV-WP03",
        "### DEV-WP04",
        "### DEV-WP05",
        "### DOC-WP01",
        "### MAINT-WP01",
        "### BT-02.1",
    ):
        assert retired_text not in plan
