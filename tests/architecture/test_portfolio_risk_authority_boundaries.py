"""P10-WP05 authority binding must not contaminate the pure P3 review gate."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.architecture._imports import (
    PACKAGE_ROOT,
    format_diagnostics,
    runtime_import_edges,
)


APPROVAL_MODULE = "northstar_quant.portfolio_risk.portfolio.approval"
AUTHORITY_MODULE = "northstar_quant.application.portfolio_risk_authority"
APPROVAL_PATH = PACKAGE_ROOT / "portfolio_risk" / "portfolio" / "approval.py"
AUTHORITY_PATH = PACKAGE_ROOT / "application" / "portfolio_risk_authority.py"


def _direct_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def _has_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def test_p3_approval_gate_has_no_application_database_broker_or_config_dependency(
) -> None:
    imports = _direct_imports(APPROVAL_PATH)
    forbidden_prefixes = (
        "northstar_quant.application",
        "northstar_quant.platform",
        "northstar_quant.trading_execution",
        "sqlalchemy",
        "psycopg",
    )
    violations = sorted(
        imported
        for imported in imports
        if any(_has_prefix(imported, prefix) for prefix in forbidden_prefixes)
    )

    assert not violations, (
        "P3 approval must remain a pure replayable domain gate; profile authority, "
        f"database, broker, and config imports belong in application: {violations}"
    )


def test_portfolio_risk_authority_is_an_application_only_composition_boundary() -> None:
    assert AUTHORITY_PATH.is_file(), (
        "authority binding must live in application/portfolio_risk_authority.py"
    )
    incoming = [
        edge
        for edge in runtime_import_edges()
        if _has_prefix(edge.target_module, AUTHORITY_MODULE)
    ]
    violations = [edge for edge in incoming if edge.source_scope != "application"]

    assert not violations, (
        "only application composition may depend on portfolio-risk authority binding; "
        "P3/P5/platform domains must not acquire an upward authority dependency:\n"
        f"{format_diagnostics(violations)}"
    )


def test_authority_resolver_accepts_typed_sources_but_no_database_or_broker_io(
) -> None:
    imports = _direct_imports(AUTHORITY_PATH)
    forbidden_prefixes = (
        "northstar_quant.platform.db",
        "northstar_quant.trading_execution.broker.broker_base",
        "northstar_quant.trading_execution.broker.ctp_broker",
        "northstar_quant.trading_execution.broker.ctp_front",
        "northstar_quant.trading_execution.broker.ctp_sim_broker",
        "sqlalchemy",
        "psycopg",
    )
    violations = sorted(
        imported
        for imported in imports
        if any(_has_prefix(imported, prefix) for prefix in forbidden_prefixes)
    )

    assert not violations, (
        "the authority resolver must consume typed P5 observations supplied by the "
        "candidate composition root rather than acquiring DB/broker capability itself: "
        f"{violations}"
    )
