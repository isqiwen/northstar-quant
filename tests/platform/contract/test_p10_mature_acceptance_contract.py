"""P10 mature-v1 evidence register and plan-state contracts."""

from __future__ import annotations

from tests.helpers.paths import PROJECT_ROOT


MASTER_PLAN = PROJECT_ROOT / "docs" / "planning" / "MASTER_IMPLEMENTATION_PLAN.md"
EVIDENCE_REGISTER = PROJECT_ROOT / "docs" / "planning" / "P10_MATURE_V1_ACCEPTANCE_EVIDENCE.md"
FAILURE_MATRIX = PROJECT_ROOT / "docs" / "planning" / "P10_TRADING_FAILURE_MATRIX.md"


EXPECTED_EVIDENCE_IDS = (
    *(f"D{index:02d}" for index in range(1, 5)),
    *(f"I{index:02d}" for index in range(1, 7)),
    *(f"R{index:02d}" for index in range(1, 8)),
    *(f"PR{index:02d}" for index in range(1, 14)),
    *(f"T{index:02d}" for index in range(1, 8)),
    *(f"PL{index:02d}" for index in range(1, 8)),
    *(f"A{index:02d}" for index in range(1, 5)),
)


def test_p10_evidence_register_covers_every_acceptance_item() -> None:
    register = EVIDENCE_REGISTER.read_text(encoding="utf-8")

    assert EVIDENCE_REGISTER.is_file()
    for evidence_id in EXPECTED_EVIDENCE_IDS:
        assert f"| {evidence_id} " in register
    for status in (
        "VERIFIED_OFFLINE",
        "VERIFIED_SIMULATION",
        "SAFE_BOUNDARY",
        "PARTIAL",
        "INCOMPLETE",
        "BLOCKED_EXTERNAL",
        "HOSTED_EVIDENCE_PENDING",
    ):
        assert f"`{status}`" in register


def test_p10_evidence_register_keeps_real_money_and_external_boundaries_explicit() -> None:
    register = EVIDENCE_REGISTER.read_text(encoding="utf-8")

    assert "NO NEW RISK" in register
    assert "not live" not in register.lower()
    assert "不等同真实 broker 或实盘" in register
    assert "NORTHSTAR_BROKER=paper" in register
    assert "NORTHSTAR_LIVE_TRADING_ENABLED=false" in register
    assert "raw prompt 或 CoT" in register


def test_p10_trading_failure_matrix_is_complete_and_simulation_scoped() -> None:
    matrix = FAILURE_MATRIX.read_text(encoding="utf-8")

    assert FAILURE_MATRIX.is_file()
    for matrix_id in tuple(f"T05-{index:02d}" for index in range(1, 14)):
        assert f"| {matrix_id} " in matrix
    for test_anchor in (
        "test_ctp_sim_recovers_submitted_order_after_disconnect",
        "test_ctp_sim_disconnect_recovery_reconciles_order_fill_and_position",
        "test_chase_restart_restores_persisted_price_and_quantity",
        "test_order_state_machine_handles_submit_fill_cancel_and_duplicate_callbacks",
        "test_order_state_machine_rejects_out_of_order_broker_callback",
        "test_unexplained_broker_order_halts_until_named_manual_recovery",
        "test_runtime_risk_blocks_stale_quotes_and_high_margin",
        "test_final_adapter_lock_refuses_stale_runtime_facts_before_simulator_mutation",
        "test_database_unavailable_prevents_any_broker_submission",
        "test_timeout_or_network_partition_stays_unknown_and_cannot_be_retried",
        "test_reconcile_rolls_back_all_state_rows_when_later_identity_check_fails",
        "test_ctp_sim_rejects_opening_order_when_margin_is_insufficient",
        "test_price_limit_blocks_buy_at_upper_limit_before_submission",
        "test_cancel_reject_is_durable_and_does_not_claim_cancellation",
        "test_ctp_sim_requires_explicit_shfe_close_and_tracks_yesterday",
        "test_authority_bound_p3_block_cannot_reach_candidate_plan_intent_or_simulator",
        "test_application_composition_root_still_rejects_real_ctp_before_connecting",
        "test_ctp_skeleton_rejects_any_non_fake_front_before_connection",
        "test_candidate_executor_cannot_reach_live_ctp_or_ai_control_surfaces",
    ):
        assert test_anchor in matrix
    assert matrix.count("`VERIFIED_SIMULATION`") >= 12
    assert "`SAFE_BOUNDARY`" in matrix
    assert "NO NEW RISK" in matrix
    assert "NORTHSTAR_BROKER=paper" in matrix
    assert "NORTHSTAR_LIVE_TRADING_ENABLED=false" in matrix


def test_p10_master_plan_links_evidence_and_tracks_only_ready_offline_work() -> None:
    plan = MASTER_PLAN.read_text(encoding="utf-8")
    register = EVIDENCE_REGISTER.read_text(encoding="utf-8")

    assert "P10_MATURE_V1_ACCEPTANCE_EVIDENCE.md" in plan
    assert "## P10-WP02 — Six-Commodity Intelligence Evidence Corpus" in plan
    assert "**Status:** DONE" in plan.split(
        "## P10-WP02 — Six-Commodity Intelligence Evidence Corpus", maxsplit=1
    )[1].split("## P10-WP03", maxsplit=1)[0]
    assert "**Status:** DONE" in plan.split(
        "## P10-WP03 — Intelligence Feature Research Backtest Evidence", maxsplit=1
    )[1].split("## P10-WP04", maxsplit=1)[0]
    assert "**Status:** DONE" in plan.split(
        "## P10-WP04 — Canonical Multi-Strategy Portfolio Composition", maxsplit=1
    )[1].split("## P10-WP05", maxsplit=1)[0]
    assert "**Status:** DONE" in plan.split(
        "## P10-WP05 — Portfolio-Wide Risk Evidence & Approval Gate", maxsplit=1
    )[1].split("## P10-WP06", maxsplit=1)[0]
    assert "**Status:** DONE" in plan.split(
        "## P10-WP06 — Durable Agent Evidence Audit", maxsplit=1
    )[1].split("## P10-WP07", maxsplit=1)[0]
    assert "**Status:** DONE" in plan.split(
        "## P10-WP07 — Trading Acceptance Evidence Closure", maxsplit=1
    )[1].split("## P10-WP08", maxsplit=1)[0]
    assert "**Status:** BLOCKED" in plan.split(
        "## P10-WP08 — Platform Production / DR Acceptance", maxsplit=1
    )[1].split("## P10-WP09", maxsplit=1)[0]
    assert "**Status:** BLOCKED" in plan.split(
        "## P10-WP09 — Authoritative Data & Source Onboarding", maxsplit=1
    )[1].split("---", maxsplit=1)[0]
    assert "| P10 | Mature v1 Acceptance | IN_PROGRESS | 78% |" in plan
    assert "P10 已完成 `7/9` 个 Work Package（78%）" in plan
    assert "active_work_package: null" in plan
    assert "id: P10-WP08" in plan
    assert (
        "title: Platform Production / DR Acceptance\n"
        "  status: BLOCKED"
    ) in plan
    assert "blocked_work_packages: [P10-WP08, P10-WP09]" in plan

    for evidence_id in ("I01", "I04", "I05", "I06"):
        line = next(line for line in register.splitlines() if line.startswith(f"| {evidence_id} "))
        assert "`VERIFIED_OFFLINE`" in line
    assert "P10-WP02 六商品情报证据语料 | `DONE`" in register
    assert "P10-WP03 情报 Feature 研究回测闭环 | `DONE`" in register
    assert "P10-WP04 Canonical Multi-Strategy Portfolio Composition | `DONE`" in register
    assert "P10-WP05 Portfolio-Wide Risk Evidence & Approval Gate | `DONE`" in register
    assert "P10-WP06 Durable Agent Evidence Audit | `DONE`" in register
    assert "P10-WP07 Trading Acceptance Evidence Closure | `DONE`" in register
    t05 = next(line for line in register.splitlines() if line.startswith("| T05 "))
    assert "`VERIFIED_SIMULATION`" in t05
    assert "P10_TRADING_FAILURE_MATRIX.md" in t05
    a03 = next(line for line in register.splitlines() if line.startswith("| A03 "))
    assert "`VERIFIED_OFFLINE`" in a03
    assert "0010_agent_run_audit_hardening" in a03
    pr03 = next(line for line in register.splitlines() if line.startswith("| PR03 "))
    pr09 = next(line for line in register.splitlines() if line.startswith("| PR09 "))
    assert "`VERIFIED_OFFLINE`" in pr03
    assert "`VERIFIED_OFFLINE`" in pr09
    assert "PortfolioTarget v2" in pr09
    for evidence_id in ("PR04", "PR05", "PR06", "PR07", "PR08", "PR10", "PR11", "PR12", "PR13"):
        line = next(line for line in register.splitlines() if line.startswith(f"| {evidence_id} "))
        assert "`VERIFIED_SIMULATION`" in line
    pr13 = next(line for line in register.splitlines() if line.startswith("| PR13 "))
    assert "BLOCKED_EXTERNAL" in pr13
    assert "Private test composition issuance proves no human identity" in pr13
    i06 = next(line for line in register.splitlines() if line.startswith("| I06 "))
    assert "fixture-only synthetic alignment replay" in i06
    assert "P3 activation" in i06


def test_p10_reconciles_stale_completion_matrix_entries() -> None:
    plan = MASTER_PLAN.read_text(encoding="utf-8")
    reconciliation = plan.split("## P5-WP08 — Reconciliation", maxsplit=1)[1].split(
        "## P5-WP09", maxsplit=1
    )[0]

    assert "**Status:** DONE" in reconciliation
    assert "HALT →" in reconciliation
    assert "| Risk | Stress | TODO |" not in plan
    assert "| Trading | Ledger | TODO |" not in plan
    assert "Portfolio-wide exposure / limits / stress / risk-state evidence and approval gate" in plan


def test_p10_fixture_only_intelligence_corpus_is_documented_as_non_authorizing() -> None:
    architecture = (PROJECT_ROOT / "docs" / "01_架构总览.md").read_text(encoding="utf-8")
    tests_readme = (PROJECT_ROOT / "tests" / "README.md").read_text(encoding="utf-8")

    assert "six_commodity_fixture_only_v1.json" in architecture
    assert "Feature **定义**" in architecture
    assert "FIXTURE_ONLY_INTELLIGENCE_REPLAY" in architecture
    assert "synthetic outcome" in architecture
    assert "不会构造" in architecture
    assert "fixture_only" in tests_readme
    assert "交易映射" in tests_readme


def test_p10_canonical_multi_strategy_composition_is_documented_as_non_executable() -> None:
    architecture = (PROJECT_ROOT / "docs" / "01_架构总览.md").read_text(encoding="utf-8")
    tests_readme = (PROJECT_ROOT / "tests" / "README.md").read_text(encoding="utf-8")

    assert "CanonicalPortfolioComposer" in architecture
    assert "PortfolioCompositionEvidence" in architecture
    assert "PortfolioTarget v2" in architecture
    assert "eligible_for_broker_order=false" in architecture
    assert "p10_canonical_multi_strategy_composition_v1.json" in tests_readme
