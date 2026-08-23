from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

import pytest

from scripts.ci import check_integrated_candidate as candidate_check


EXPECTED_GATES = (
    (
        "P1_DATA_PIT",
        ("tests/data/integration/test_data_e2e.py",),
    ),
    (
        "P4_INTELLIGENCE_GOLDEN",
        ("tests/intelligence/golden/test_supply_outage_e2e.py",),
    ),
    (
        "P8_INTELLIGENCE_TO_RESEARCH",
        (
            "tests/intelligence/unit/test_feature_projection.py",
            "tests/application/unit/test_intelligence_feature_projection.py",
            "tests/application/unit/test_intelligence_feature_projection_evidence.py",
            "tests/research/unit/test_intelligence_feature_registry.py",
            "tests/research/integration/test_intelligence_feature_projection_pit.py",
            "tests/e2e/test_integrated_candidate_acceptance.py",
            "tests/architecture/test_intelligence_feature_projection_boundaries.py",
        ),
    ),
    (
        "P8_RESEARCH_TO_PORTFOLIO_RISK",
        (
            "tests/application/unit/test_research_strategy_activation.py",
            "tests/portfolio_risk/unit/test_portfolio_targets.py",
            "tests/architecture/test_research_strategy_activation_boundaries.py",
        ),
    ),
    (
        "P8_EXECUTION_PROVENANCE_PREFLIGHT",
        (
            "tests/application/unit/test_execution_provenance_preflight.py",
            "tests/trading_execution/unit/test_preflight.py",
            "tests/architecture/test_execution_provenance_preflight_boundaries.py",
        ),
    ),
    (
        "P2_RESEARCH_E2E",
        ("tests/research/e2e/test_research_card_reproducibility.py",),
    ),
    (
        "P3_PORTFOLIO_RISK_E2E",
        ("tests/portfolio_risk/e2e/test_portfolio_risk_workflow.py",),
    ),
    (
        "P8_CTP_SIM_CANDIDATE_E2E",
        (
            "tests/application/unit/test_ctp_sim_candidate_execution.py",
            "tests/e2e/test_trading_execution_e2e.py",
            "tests/architecture/test_ctp_sim_candidate_execution_boundaries.py",
        ),
    ),
    (
        "P6_RELEASE_HERMETIC",
        (
            "tests/foundation/unit/test_release_transaction.py",
            "tests/foundation/unit/test_release_control_bundle.py",
            "tests/foundation/unit/test_release_gate_bootstrap.py",
            "tests/foundation/unit/test_release_manifest.py",
            "tests/foundation/unit/test_release_signing.py",
            "tests/foundation/contract/test_release_pipeline_contract.py",
            "tests/foundation/contract/test_release_environment_upgrade_contract.py",
            "tests/foundation/contract/test_release_gate_bootstrap_contract.py",
        ),
    ),
    (
        "P6_MONITORING_HERMETIC",
        (
            "tests/foundation/unit/test_metrics.py",
            "tests/foundation/unit/test_operational_snapshot.py",
        ),
    ),
    (
        "P6_BACKUP_HERMETIC",
        (
            "tests/foundation/unit/test_database_backup_readiness_config.py",
            "tests/foundation/unit/test_database_backup_readiness.py",
            "tests/foundation/unit/test_backup_bundle.py",
            "tests/foundation/unit/test_postgresql_backup.py",
            "tests/foundation/unit/test_postgresql_restore_drill.py",
        ),
    ),
    (
        "P7_APPLICATION",
        (
            "tests/application/unit/test_agent_tools.py",
            "tests/application/unit/test_research_agent.py",
            "tests/application/unit/test_intelligence_agent.py",
            "tests/application/unit/test_data_quality_agent.py",
            "tests/application/unit/test_ops_tools.py",
            "tests/application/unit/test_ops_agent.py",
        ),
    ),
    (
        "P7_CONTRACT",
        (
            "tests/foundation/contract/test_agent_tool_api_contract.py",
            "tests/foundation/contract/test_research_agent_contract.py",
            "tests/foundation/contract/test_intelligence_agent_contract.py",
            "tests/foundation/contract/test_data_quality_agent_contract.py",
            "tests/foundation/contract/test_ops_agent_contract.py",
        ),
    ),
    ("P7_ARCHITECTURE", ("tests/architecture",)),
)


def _expected_matrix_output() -> list[str]:
    return [entry for gate_id, paths in EXPECTED_GATES for entry in (gate_id, *paths)]


@pytest.fixture
def safe_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "NORTHSTAR_LIVE_TRADING_ENABLED",
        "NORTHSTAR_BROKER",
        "NORTHSTAR_ENV",
    ):
        monkeypatch.delenv(key, raising=False)


def _capturing_pytest_main(calls: list[tuple[str, ...]]) -> Callable[[list[str]], int]:
    def fake_pytest_main(arguments: list[str]) -> int:
        calls.append(tuple(arguments))
        return 0

    return fake_pytest_main


def test_matrix_is_exact_fixed_paths_for_the_required_evidence_lanes() -> None:
    actual = tuple((gate.gate_id, gate.paths) for gate in candidate_check.EVIDENCE_GATES)

    assert actual == EXPECTED_GATES
    assert candidate_check.EVIDENCE_PATHS == tuple(
        path for _, paths in EXPECTED_GATES for path in paths
    )
    assert len(candidate_check.EVIDENCE_PATHS) == len(set(candidate_check.EVIDENCE_PATHS))
    assert all(path.startswith("tests/") for path in candidate_check.EVIDENCE_PATHS)
    assert "tests/foundation/integration/test_postgresql_restore_drill_integration.py" not in (
        candidate_check.EVIDENCE_PATHS
    )


def test_check_has_no_process_or_network_imports() -> None:
    module_path = candidate_check.__file__
    assert module_path is not None
    tree = ast.parse(Path(module_path).read_text(encoding="utf-8"))

    imported_modules = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert imported_modules == {"__future__", "dataclasses", "os", "pytest", "sys", "typing"}


def test_default_safe_environment_runs_the_exact_matrix(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    safe_environment: None,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(candidate_check.pytest, "main", _capturing_pytest_main(calls))

    assert candidate_check.main(()) == 0

    assert calls == [("-q", *candidate_check.EVIDENCE_PATHS)]
    assert capsys.readouterr().out.splitlines() == _expected_matrix_output()


def test_collect_only_runs_the_exact_matrix(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    safe_environment: None,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(candidate_check.pytest, "main", _capturing_pytest_main(calls))

    assert candidate_check.main(("--collect-only",)) == 0

    assert calls == [("-q", "--collect-only", *candidate_check.EVIDENCE_PATHS)]
    assert capsys.readouterr().out.splitlines() == _expected_matrix_output()


@pytest.mark.parametrize("value", ("1", "true", "YES", "unexpected"))
def test_live_trading_values_fail_closed_before_pytest_or_matrix_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    safe_environment: None,
    value: str,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setenv("NORTHSTAR_LIVE_TRADING_ENABLED", value)
    monkeypatch.setattr(candidate_check.pytest, "main", _capturing_pytest_main(calls))

    assert candidate_check.main(()) == 2

    assert calls == []
    output = capsys.readouterr().out
    assert output.splitlines() == ["P8_LIVE_TRADING_REFUSED"]
    assert value not in output


@pytest.mark.parametrize("value", ("ctp", "live", "unapproved-broker-value", ""))
def test_unapproved_broker_fails_closed_before_pytest(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    safe_environment: None,
    value: str,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setenv("NORTHSTAR_BROKER", value)
    monkeypatch.setattr(candidate_check.pytest, "main", _capturing_pytest_main(calls))

    assert candidate_check.main(()) == 2

    assert calls == []
    output = capsys.readouterr().out
    assert output.splitlines() == ["P8_BROKER_REFUSED"]
    if value:
        assert value not in output


@pytest.mark.parametrize(
    ("value", "expected_gate"),
    (
        ("production", "P8_PRODUCTION_ENV_REFUSED"),
        ("PRODUCTION", "P8_PRODUCTION_ENV_REFUSED"),
        ("staging", "P8_ENVIRONMENT_REFUSED"),
        ("", "P8_ENVIRONMENT_REFUSED"),
    ),
)
def test_unapproved_environment_fails_closed_before_pytest(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    safe_environment: None,
    value: str,
    expected_gate: str,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setenv("NORTHSTAR_ENV", value)
    monkeypatch.setattr(candidate_check.pytest, "main", _capturing_pytest_main(calls))

    assert candidate_check.main(()) == 2

    assert calls == []
    output = capsys.readouterr().out
    assert output.splitlines() == [expected_gate]


@pytest.mark.parametrize("broker", ("paper", "ctp_sim"))
@pytest.mark.parametrize("environment", ("dev", "test", "offline"))
def test_only_allowed_brokers_and_environments_run_the_matrix(
    monkeypatch: pytest.MonkeyPatch,
    safe_environment: None,
    broker: str,
    environment: str,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setenv("NORTHSTAR_BROKER", broker)
    monkeypatch.setenv("NORTHSTAR_ENV", environment)
    monkeypatch.setattr(candidate_check.pytest, "main", _capturing_pytest_main(calls))

    assert candidate_check.main(()) == 0
    assert calls == [("-q", *candidate_check.EVIDENCE_PATHS)]


def test_unsupported_arguments_are_refused_without_calling_pytest(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    safe_environment: None,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(candidate_check.pytest, "main", _capturing_pytest_main(calls))

    assert candidate_check.main(("--verbose",)) == 2

    assert calls == []
    assert capsys.readouterr().out.splitlines() == ["P8_ARGUMENTS_REFUSED"]


def test_safety_refusal_precedes_argument_validation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    safe_environment: None,
) -> None:
    monkeypatch.setenv("NORTHSTAR_LIVE_TRADING_ENABLED", "1")

    assert candidate_check.main(("--verbose",)) == 2

    assert capsys.readouterr().out.splitlines() == ["P8_LIVE_TRADING_REFUSED"]
