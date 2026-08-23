"""P8 candidate-acceptance boundary contracts.

``candidate_acceptance`` is a pure, evidence-only receipt boundary.  It must
remain independent from every Northstar domain and must not grow a control,
promotion, execution, deployment, or recovery capability.
"""

from __future__ import annotations

import ast
from dataclasses import fields
import inspect
import sys

import northstar_quant.application.candidate_acceptance as candidate_acceptance
from tests.architecture._imports import PACKAGE_ROOT


CANDIDATE_ACCEPTANCE_MODULE = "northstar_quant.application.candidate_acceptance"
CANDIDATE_ACCEPTANCE_PATH = (
    PACKAGE_ROOT / "application" / "candidate_acceptance.py"
)

_FORBIDDEN_CAPABILITY_IMPORT_ROOTS = frozenset(
    {
        "argparse",
        "asyncio",
        "boto3",
        "httpx",
        "os",
        "paramiko",
        "pathlib",
        "psycopg",
        "requests",
        "shutil",
        "socket",
        "sqlalchemy",
        "sqlite3",
        "subprocess",
        "tempfile",
        "urllib",
        "websockets",
    }
)
_FORBIDDEN_REFLECTIVE_OR_IO_CALLS = frozenset(
    {
        "__import__",
        "eval",
        "exec",
        "getattr",
        "import_module",
        "open",
    }
)
_FORBIDDEN_CONTROL_CALLS = frozenset(
    {
        "approve",
        "approve_production",
        "cancel_order",
        "create_execution_plan",
        "create_target",
        "delete",
        "deploy",
        "disable_kill_switch",
        "enable_live_trading",
        "publish",
        "recover",
        "recovery",
        "restart",
        "restore",
        "resume",
        "rollback",
        "submit",
        "submit_order",
        "trade",
        "transition",
        "write",
    }
)
_FORBIDDEN_PUBLIC_FIELD_FRAGMENTS = (
    "action",
    "broker",
    "command",
    "credential",
    "deploy",
    "host",
    "order",
    "path",
    "payload",
    "recover",
    "restore",
    "resume",
    "secret",
    "submit",
    "target",
    "trade",
)
_SAFE_FIELD_NAMES = frozenset({"eligible_for_trading"})


class _RuntimeSourceVisitor(ast.NodeVisitor):
    """Collect direct runtime imports and capability-shaped calls."""

    def __init__(self) -> None:
        self.imports: set[str] = set()
        self.calls: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self.imports.update(alias.name for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.module is not None:
            self.imports.add(node.module)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if isinstance(node.func, ast.Name):
            self.calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            self.calls.add(node.func.attr)
        self.generic_visit(node)


def _runtime_source_dependencies() -> _RuntimeSourceVisitor:
    tree = ast.parse(
        CANDIDATE_ACCEPTANCE_PATH.read_text(encoding="utf-8"),
        filename=str(CANDIDATE_ACCEPTANCE_PATH),
    )
    visitor = _RuntimeSourceVisitor()
    visitor.visit(tree)
    return visitor


def _is_allowed_direct_import(module: str) -> bool:
    if module == CANDIDATE_ACCEPTANCE_MODULE:
        return True
    root = module.split(".", maxsplit=1)[0]
    return root == "__future__" or root in sys.stdlib_module_names


def test_candidate_acceptance_is_a_declared_pure_application_entrypoint() -> None:
    assert CANDIDATE_ACCEPTANCE_PATH.is_file(), (
        "P8 candidate acceptance must live in application/candidate_acceptance.py"
    )


def test_candidate_acceptance_directly_imports_only_standard_library_modules() -> None:
    visitor = _runtime_source_dependencies()
    unexpected = sorted(
        imported
        for imported in visitor.imports
        if not _is_allowed_direct_import(imported)
    )
    internal = sorted(
        imported
        for imported in visitor.imports
        if imported.startswith("northstar_quant.")
        and imported != CANDIDATE_ACCEPTANCE_MODULE
    )

    assert not unexpected, (
        "CandidateAcceptance must be a pure stdlib-only boundary; unexpected "
        f"runtime imports: {unexpected}"
    )
    assert not internal, (
        "CandidateAcceptance cannot directly compose Data, Intelligence, Research, "
        "Portfolio/Risk, Trading, Platform, or another application capability: "
        f"{internal}"
    )


def test_candidate_acceptance_has_no_process_storage_network_or_dynamic_capability() -> None:
    visitor = _runtime_source_dependencies()
    imports = {
        imported.split(".", maxsplit=1)[0]
        for imported in visitor.imports
    }
    forbidden_imports = sorted(imports.intersection(_FORBIDDEN_CAPABILITY_IMPORT_ROOTS))
    forbidden_calls = sorted(
        visitor.calls.intersection(
            _FORBIDDEN_REFLECTIVE_OR_IO_CALLS | _FORBIDDEN_CONTROL_CALLS
        )
    )

    assert not forbidden_imports, (
        "CandidateAcceptance must not open process, storage, database, network, "
        f"broker, or deployment capability: {forbidden_imports}"
    )
    assert not forbidden_calls, (
        "CandidateAcceptance is evidence-only and must not evaluate dynamic code, "
        f"perform I/O, promote, submit, deploy, recover, or trade: {forbidden_calls}"
    )


def test_candidate_acceptance_has_only_evaluate_as_a_public_behavior_entrypoint() -> None:
    public_module_functions = {
        name
        for name, value in vars(candidate_acceptance).items()
        if inspect.isfunction(value)
        and value.__module__ == CANDIDATE_ACCEPTANCE_MODULE
        and not name.startswith("_")
    }
    public_verifier_methods = {
        name
        for name, value in inspect.getmembers(
            candidate_acceptance.CandidateAcceptanceVerifier,
            predicate=inspect.isfunction,
        )
        if not name.startswith("_")
    }

    assert public_module_functions == set(), (
        "CandidateAcceptance must not expose a module-level control or convenience "
        f"operation: {sorted(public_module_functions)}"
    )
    assert public_verifier_methods == {"evaluate"}, (
        "CandidateAcceptanceVerifier has exactly one behavior entrypoint: "
        f"{sorted(public_verifier_methods)}"
    )
    assert candidate_acceptance.CandidateAcceptanceVerifier.__slots__ == ()


def test_candidate_acceptance_public_records_expose_no_control_or_execution_surface() -> None:
    records = (
        candidate_acceptance.CandidateLaneEvidence,
        candidate_acceptance.CandidateSeamEvidence,
        candidate_acceptance.CandidateAcceptanceRequest,
        candidate_acceptance.CandidateAcceptanceResult,
    )
    violations: dict[str, list[str]] = {}
    for record in records:
        forbidden = sorted(
            field.name
            for field in fields(record)
            if field.name not in _SAFE_FIELD_NAMES
            and any(
                fragment in field.name.casefold()
                for fragment in _FORBIDDEN_PUBLIC_FIELD_FRAGMENTS
            )
        )
        if forbidden:
            violations[record.__name__] = forbidden

    assert not violations, (
        "Candidate evidence records must remain hash-only observations, without "
        f"command/target/order/trading/deployment/recovery fields: {violations}"
    )
