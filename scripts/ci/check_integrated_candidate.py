"""Run the fixed, local evidence matrix for integrated candidate acceptance."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Sequence

import pytest


@dataclass(frozen=True, slots=True)
class EvidenceGate:
    """A fixed collection of hermetic evidence for one acceptance gate."""

    gate_id: str
    paths: tuple[str, ...]


EVIDENCE_GATES: tuple[EvidenceGate, ...] = (
    EvidenceGate(
        "P1_DATA_PIT",
        ("tests/data/integration/test_data_e2e.py",),
    ),
    EvidenceGate(
        "P4_INTELLIGENCE_GOLDEN",
        ("tests/intelligence/golden/test_supply_outage_e2e.py",),
    ),
    EvidenceGate(
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
    EvidenceGate(
        "P8_RESEARCH_TO_PORTFOLIO_RISK",
        (
            "tests/application/unit/test_research_strategy_activation.py",
            "tests/portfolio_risk/unit/test_portfolio_targets.py",
            "tests/architecture/test_research_strategy_activation_boundaries.py",
        ),
    ),
    EvidenceGate(
        "P8_EXECUTION_PROVENANCE_PREFLIGHT",
        (
            "tests/application/unit/test_execution_provenance_preflight.py",
            "tests/trading_execution/unit/test_preflight.py",
            "tests/architecture/test_execution_provenance_preflight_boundaries.py",
        ),
    ),
    EvidenceGate(
        "P2_RESEARCH_E2E",
        ("tests/research/e2e/test_research_card_reproducibility.py",),
    ),
    EvidenceGate(
        "P3_PORTFOLIO_RISK_E2E",
        ("tests/portfolio_risk/e2e/test_portfolio_risk_workflow.py",),
    ),
    EvidenceGate(
        "P8_CTP_SIM_CANDIDATE_E2E",
        (
            "tests/application/unit/test_ctp_sim_candidate_execution.py",
            "tests/e2e/test_trading_execution_e2e.py",
            "tests/architecture/test_ctp_sim_candidate_execution_boundaries.py",
        ),
    ),
    EvidenceGate(
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
    EvidenceGate(
        "P6_MONITORING_HERMETIC",
        (
            "tests/foundation/unit/test_metrics.py",
            "tests/foundation/unit/test_operational_snapshot.py",
        ),
    ),
    EvidenceGate(
        "P6_BACKUP_HERMETIC",
        (
            "tests/foundation/unit/test_database_backup_readiness_config.py",
            "tests/foundation/unit/test_database_backup_readiness.py",
            "tests/foundation/unit/test_backup_bundle.py",
            "tests/foundation/unit/test_postgresql_backup.py",
            "tests/foundation/unit/test_postgresql_restore_drill.py",
        ),
    ),
    EvidenceGate(
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
    EvidenceGate(
        "P7_CONTRACT",
        (
            "tests/foundation/contract/test_agent_tool_api_contract.py",
            "tests/foundation/contract/test_research_agent_contract.py",
            "tests/foundation/contract/test_intelligence_agent_contract.py",
            "tests/foundation/contract/test_data_quality_agent_contract.py",
            "tests/foundation/contract/test_ops_agent_contract.py",
        ),
    ),
    EvidenceGate("P7_ARCHITECTURE", ("tests/architecture",)),
)

EVIDENCE_PATHS: tuple[str, ...] = tuple(
    path for gate in EVIDENCE_GATES for path in gate.paths
)

_FALSE_LIVE_VALUES = frozenset({"", "0", "false", "no", "off"})
_ALLOWED_BROKERS = frozenset({"paper", "ctp_sim"})
_ALLOWED_ENVIRONMENTS = frozenset({"dev", "test", "offline"})


def _is_truthy(value: str | None) -> bool:
    return value is not None and value.strip().casefold() not in _FALSE_LIVE_VALUES


def _safety_failure() -> str | None:
    if _is_truthy(os.environ.get("NORTHSTAR_LIVE_TRADING_ENABLED")):
        return "P8_LIVE_TRADING_REFUSED"

    broker = os.environ.get("NORTHSTAR_BROKER", "paper").strip().casefold()
    if broker not in _ALLOWED_BROKERS:
        return "P8_BROKER_REFUSED"

    environment = os.environ.get("NORTHSTAR_ENV", "dev").strip().casefold()
    if environment == "production":
        return "P8_PRODUCTION_ENV_REFUSED"
    if environment not in _ALLOWED_ENVIRONMENTS:
        return "P8_ENVIRONMENT_REFUSED"
    return None


def _print_matrix() -> None:
    for gate in EVIDENCE_GATES:
        print(gate.gate_id)
        for path in gate.paths:
            print(path)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the acceptance evidence matrix only when the local context is safe."""

    failure = _safety_failure()
    if failure is not None:
        print(failure)
        return 2

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments not in ((), ("--collect-only",)):
        print("P8_ARGUMENTS_REFUSED")
        return 2

    _print_matrix()
    pytest_arguments = ["-q"]
    if arguments == ("--collect-only",):
        pytest_arguments.append("--collect-only")
    pytest_arguments.extend(EVIDENCE_PATHS)
    return int(pytest.main(pytest_arguments))


if __name__ == "__main__":
    raise SystemExit(main())
