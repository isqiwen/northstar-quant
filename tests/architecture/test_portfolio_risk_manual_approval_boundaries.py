"""P10 manual-risk approval stays outside P3, candidate, CLI, and AI surfaces."""

from __future__ import annotations

import ast
import re

import northstar_quant.application.portfolio_risk_manual_approval as manual_approval
from tests.architecture._imports import PACKAGE_ROOT


MODULE_PATH = PACKAGE_ROOT / "application" / "portfolio_risk_manual_approval.py"
P3_APPROVAL_PATH = PACKAGE_ROOT / "portfolio_risk" / "portfolio" / "approval.py"
CANDIDATE_PATH = PACKAGE_ROOT / "application" / "ctp_sim_candidate_execution.py"
CLI_PATH = PACKAGE_ROOT / "application" / "cli.py"
AGENT_TOOLS_PATH = PACKAGE_ROOT / "application" / "agent_tools.py"
REPOSITORIES_PATH = PACKAGE_ROOT / "foundation" / "db" / "repositories.py"
MANUAL_APPROVAL_MODULE = "northstar_quant.application.portfolio_risk_manual_approval"
REPOSITORIES_MODULE = "northstar_quant.foundation.db.repositories"
RAW_VERIFIER_RECEIPT_RE = re.compile(r"\bverifier_receipt\b(?!_hash)")


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def _from_imported_names(path, module: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            names.update(alias.name for alias in node.names)
    return names


def _imports_or_calls_repository_writer(path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == REPOSITORIES_MODULE:
            if any(alias.name == "record_portfolio_risk_approval" for alias in node.names):
                return True
        if isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Name) and function.id == "record_portfolio_risk_approval":
                return True
            if (
                isinstance(function, ast.Attribute)
                and function.attr == "record_portfolio_risk_approval"
            ):
                return True
    return False


def test_manual_risk_approval_is_an_application_only_database_boundary() -> None:
    assert MODULE_PATH.is_file()
    p3_imports = _imports(P3_APPROVAL_PATH)
    assert MANUAL_APPROVAL_MODULE not in p3_imports


def test_public_production_surface_has_no_issuer_or_verifier_factory() -> None:
    assert set(manual_approval.__all__) == {
        "ManualRiskApprovalBinding",
        "ManualRiskApprovalError",
        "PersistedPortfolioRiskApproval",
        "require_persisted_portfolio_risk_approval",
    }
    for name in (
        "IssuedPortfolioRiskApproval",
        "PortfolioRiskApprovalIssuer",
        "ManualRiskApprovalVerifier",
        "UnavailableManualRiskApprovalVerifier",
        "VerifiedManualRiskApproval",
    ):
        assert not hasattr(manual_approval, name)


def test_candidate_cli_and_ai_cannot_construct_or_inject_manual_approval_verifiers() -> None:
    forbidden_markers = (
        "PortfolioRiskApprovalIssuer",
        "ManualRiskApprovalVerifier",
        "UnavailableManualRiskApprovalVerifier",
        "VerifiedManualRiskApproval",
        "_create_portfolio_risk_approval_issuer_for_test",
        "_verified_manual_risk_approval_from_trusted_verifier",
    )
    violations = {
        str(path): [
            marker for marker in forbidden_markers if marker in path.read_text(encoding="utf-8")
        ]
        for path in (CANDIDATE_PATH, CLI_PATH, AGENT_TOOLS_PATH)
    }
    violations = {path: markers for path, markers in violations.items() if markers}
    assert not violations, (
        "only trusted composition and tests may construct verifier-backed manual approvals: "
        f"{violations}"
    )


def test_candidate_imports_only_the_persisted_approval_reader() -> None:
    assert _from_imported_names(CANDIDATE_PATH, MANUAL_APPROVAL_MODULE) == {
        "require_persisted_portfolio_risk_approval"
    }


def test_no_production_module_uses_the_test_only_issuer_factory() -> None:
    factory_marker = "_create_portfolio_risk_approval_issuer_for_test"
    violations = [
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if path != MODULE_PATH and factory_marker in path.read_text(encoding="utf-8")
    ]
    assert not violations, (
        "test-only fake verifier composition cannot be imported from production source: "
        f"{violations}"
    )


def test_only_manual_approval_boundary_imports_or_calls_grant_repository_writer() -> None:
    violations = [
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if path not in {MODULE_PATH, REPOSITORIES_PATH}
        and _imports_or_calls_repository_writer(path)
    ]
    assert not violations, (
        "candidate, CLI, agents, and every other production module must only read "
        f"manual approval grants through the application boundary: {violations}"
    )


def test_production_code_and_migrations_never_persist_or_return_raw_verifier_receipts() -> None:
    paths = [*PACKAGE_ROOT.rglob("*.py"), *MODULE_PATH.parents[3].joinpath("alembic").rglob("*.py")]
    violations = [
        path
        for path in paths
        if RAW_VERIFIER_RECEIPT_RE.search(path.read_text(encoding="utf-8"))
    ]
    assert not violations, (
        "only a verifier_receipt_hash may cross the durable approval boundary: "
        f"{violations}"
    )
