"""Contracts for the current-only implementation control plane."""

from __future__ import annotations

from tests.helpers.paths import PROJECT_ROOT


PLANNING_DIR = PROJECT_ROOT / "docs" / "planning"
MASTER_PLAN = PLANNING_DIR / "MASTER_IMPLEMENTATION_PLAN.md"


def _read_master_plan() -> str:
    return MASTER_PLAN.read_text(encoding="utf-8")


def _section(plan: str, heading: str) -> str:
    return plan.split(heading, maxsplit=1)[1].split("\n### ", maxsplit=1)[0]


def test_master_plan_is_the_single_current_control_plane() -> None:
    assert MASTER_PLAN.is_file()
    assert {
        path.name
        for path in PLANNING_DIR.iterdir()
        if path.is_file()
    } == {"MASTER_IMPLEMENTATION_PLAN.md"}
    assert not (
        PLANNING_DIR / "Northstar_Quant_Codex_Master_Implementation_Plan.md"
    ).exists()


def test_agents_and_document_navigation_link_the_same_master_plan() -> None:
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    docs_index = (PROJECT_ROOT / "docs" / "README.md").read_text(encoding="utf-8")

    assert "docs/planning/MASTER_IMPLEMENTATION_PLAN.md" in agents
    assert "(docs/planning/MASTER_IMPLEMENTATION_PLAN.md)" in readme
    assert "](planning/MASTER_IMPLEMENTATION_PLAN.md)" in docs_index


def test_master_plan_tracks_only_current_work_packages_and_statuses() -> None:
    plan = _read_master_plan()

    assert "active_phase: P10" in plan
    assert (
        "active_work_package:\n"
        "  id: MAINT-WP02\n"
        "  title: Native Linux PostgreSQL Development / Docker Removal\n"
        "  status: VERIFY"
    ) in plan
    assert (
        "next_task:\n"
        "  id: P10-WP08\n"
        "  title: Platform Production / DR Acceptance\n"
        "  status: BLOCKED"
    ) in plan
    assert "blocked_work_packages: [P10-WP08, P10-WP09]" in plan

    expected_sections = {
        "### P10-WP08 — Platform Production / DR Acceptance": "BLOCKED",
        "### P10-WP09 — Authoritative Data & Source Onboarding": "BLOCKED",
        "### DEV-WP01 — Development Alembic Baseline Consolidation": "IN_PROGRESS",
        "### DEV-WP02 — Four-Tier Storage Boundary": "VERIFY",
        "### DEV-WP03 — PostgreSQL Trading-State Authority": "VERIFY",
        "### DEV-WP04 — PostgreSQL Contract Authority": "TODO",
        "### MAINT-WP02 — Native Linux PostgreSQL Development / Docker Removal": "VERIFY",
        "### DOC-WP08 — VS Code Daily Task Surface": "VERIFY",
    }
    for heading, status in expected_sections.items():
        section = _section(plan, heading)
        assert f"**Status:** {status}" in section
        assert "**Acceptance:**" in section


def test_master_plan_preserves_unfinished_safety_boundaries() -> None:
    plan = _read_master_plan()

    for required_text in (
        "NORTHSTAR_BROKER=paper",
        "NORTHSTAR_LIVE_TRADING_ENABLED=false",
        "NO LIVE ACTION",
        "NO NEW RISK",
        "0001_current_schema_baseline",
        "state.json",
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
        "## P0-WP01",
        "## P10-WP01",
        "## DEV-WP05",
        "## DOC-WP01",
        "## MAINT-WP01",
        "**Status:** DONE",
    ):
        assert retired_text not in plan
