"""数据库保全边界的静态契约。"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

from tests.helpers.paths import PROJECT_ROOT


AUTOMATION_TARGETS = (
    PROJECT_ROOT / "justfile",
    PROJECT_ROOT / ".vscode" / "tasks.json",
    PROJECT_ROOT / "scripts" / "dev",
    PROJECT_ROOT / "scripts" / "db",
    PROJECT_ROOT / "scripts" / "deploy",
    PROJECT_ROOT / "scripts" / "maintenance",
    PROJECT_ROOT / "scripts" / "ops",
    PROJECT_ROOT / "infra" / "docker" / "compose.yaml",
)
AUTOMATION_SUFFIXES = frozenset({".json", ".py", ".ps1", ".sh", ".sql"})
DESTRUCTIVE_COMMAND_PATTERNS = (
    re.compile(r"\bdrop\s+(?:database|schema|table)\b", re.IGNORECASE),
    re.compile(r"\btruncate(?:\s+table)?\b", re.IGNORECASE),
    re.compile(r"\bdelete\s+from\b", re.IGNORECASE),
    re.compile(
        r"\bdocker\s+compose\b[^\n]*(?:\bdown\b[^\n]*(?:\s-v\b|\s--volumes\b)|(?:\s-v\b|\s--volumes\b)[^\n]*\bdown\b)",
        re.IGNORECASE,
    ),
    re.compile(r"\bdocker\s+volume\s+(?:rm|prune)\b", re.IGNORECASE),
    re.compile(
        r"\bdocker\s+system\s+prune\b[^\n]*(?:\s-v\b|\s--volumes\b)",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:alembic|command)\.downgrade\s*\(", re.IGNORECASE),
    re.compile(r"\balembic\s+downgrade\b", re.IGNORECASE),
    re.compile(r"\.drop_all\s*\(", re.IGNORECASE),
)
DESTRUCTIVE_MIGRATION_SQL = re.compile(
    r"\b(?:drop\s+(?:database|schema|table|index|column|constraint)|"
    r"truncate(?:\s+table)?|delete\s+from)\b",
    re.IGNORECASE,
)
_IMMUTABLE_TRUNCATE_GUARD_SQL = re.compile(
    r"\A\s*CREATE\s+TRIGGER\s+"
    r"trg_research_agent_(?:audit_events|trace_entries)_reject_truncate\s+"
    r"BEFORE\s+TRUNCATE\s+ON\s+research_agent_run_"
    r"(?:audit_events|trace_entries)\s+FOR\s+EACH\s+STATEMENT\s+"
    r"EXECUTE\s+FUNCTION\s+"
    r"northstar_reject_research_agent_run_audit_mutation\s*\(\s*\)\s*;\s*\Z",
    re.IGNORECASE | re.DOTALL,
)
DOCUMENTATION_TARGETS = (
    PROJECT_ROOT / "AGENTS.md",
    PROJECT_ROOT / "README.md",
    PROJECT_ROOT / "docs" / "OPERATIONS.md",
    PROJECT_ROOT / "scripts" / "README.md",
)
PRESERVATION_STATEMENT = "仓库自动化绝不删除或清空数据库、表、schema 或 Docker 数据卷"
MANUAL_ONLY_STATEMENT = "数据库删除或清空只能由用户在仓库自动化之外手动执行"


def _automation_files() -> Iterator[Path]:
    for target in AUTOMATION_TARGETS:
        if target.is_file():
            yield target
            continue
        yield from (
            path
            for path in sorted(target.rglob("*"))
            if path.is_file() and path.suffix in AUTOMATION_SUFFIXES
        )


def _module_function(module: ast.Module, name: str, path: Path) -> ast.FunctionDef:
    functions = [
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(functions) == 1, f"{path.relative_to(PROJECT_ROOT)} 必须有且仅有一个 {name}()"
    return functions[0]


def _migration_upgrade_functions(module: ast.Module, path: Path) -> list[ast.FunctionDef]:
    functions = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and (node.name == "upgrade" or node.name.startswith("_apply_"))
    ]
    assert functions, f"{path.relative_to(PROJECT_ROOT)} 缺少 upgrade()"
    return functions


def _statements_after_docstring(function: ast.FunctionDef) -> list[ast.stmt]:
    statements = function.body
    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        statements = statements[1:]
    return statements


def _first_statement_after_docstring(function: ast.FunctionDef) -> ast.stmt:
    statements = _statements_after_docstring(function)
    assert statements, f"{function.name}() 不能为空"
    return statements[0]


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _static_sql(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_sql(node.left)
        right = _static_sql(node.right)
        return f"{left}{right}" if left is not None and right is not None else None
    if isinstance(node, ast.Call) and len(node.args) == 1:
        name = _call_name(node.func)
        if name and name.rsplit(".", 1)[-1] in {"DDL", "text"}:
            return _static_sql(node.args[0])
    return None


def _destructive_migration_violations(
    function: ast.FunctionDef,
    *,
    path: Path,
) -> list[str]:
    violations: list[str] = []
    for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
        call_name = _call_name(call.func)
        if not call_name:
            continue
        operation = call_name.rsplit(".", 1)[-1]
        if operation.startswith("drop_"):
            violations.append(
                f"{path.relative_to(PROJECT_ROOT)}:{call.lineno}: "
                f"{function.name}() 不得调用 {call_name}"
            )
            continue
        if operation not in {"execute", "exec_driver_sql"}:
            continue
        if not call.args:
            violations.append(
                f"{path.relative_to(PROJECT_ROOT)}:{call.lineno}: "
                f"{function.name}() 包含无法静态审查的 SQL 执行"
            )
            continue
        sql = _static_sql(call.args[0])
        if sql is None:
            violations.append(
                f"{path.relative_to(PROJECT_ROOT)}:{call.lineno}: "
                f"{function.name}() SQL 必须可静态审查"
            )
        elif (
            DESTRUCTIVE_MIGRATION_SQL.search(sql)
            and _IMMUTABLE_TRUNCATE_GUARD_SQL.fullmatch(sql) is None
        ):
            violations.append(
                f"{path.relative_to(PROJECT_ROOT)}:{call.lineno}: {function.name}() 包含破坏性 SQL"
            )
    return violations


def test_repository_automation_never_contains_destructive_database_commands() -> None:
    """日常开发、部署与运维入口不能成为数据库清理工具。"""

    violations: list[str] = []
    for path in _automation_files():
        source = path.read_text(encoding="utf-8")
        for pattern in DESTRUCTIVE_COMMAND_PATTERNS:
            match = pattern.search(source)
            if match:
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{source.count(chr(10), 0, match.start()) + 1}: "
                    f"禁止的数据库破坏命令 {match.group(0)!r}"
                )

    assert not violations, "\n".join(violations)


def test_database_preservation_policy_is_visible_in_operator_documents() -> None:
    for path in DOCUMENTATION_TARGETS:
        source = path.read_text(encoding="utf-8")
        relative_path = path.relative_to(PROJECT_ROOT)
        normalized_source = re.sub(r"\s+", "", source)
        assert re.sub(r"\s+", "", PRESERVATION_STATEMENT) in normalized_source, relative_path
        assert re.sub(r"\s+", "", MANUAL_ONLY_STATEMENT) in normalized_source, relative_path


def test_init_db_only_requests_alembic_upgrade_to_head() -> None:
    path = PROJECT_ROOT / "src" / "northstar_quant" / "foundation" / "db" / "init_db.py"
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    init_db = _module_function(module, "init_db", path)

    alembic_calls = [
        call
        for call in ast.walk(init_db)
        if isinstance(call, ast.Call) and (_call_name(call.func) or "").startswith("command.")
    ]

    assert len(alembic_calls) == 1
    upgrade = alembic_calls[0]
    assert _call_name(upgrade.func) == "command.upgrade"
    assert len(upgrade.args) == 2
    assert isinstance(upgrade.args[1], ast.Constant)
    assert upgrade.args[1].value == "head"

    forbidden_calls = {"create_all", "drop_all", "downgrade", "stamp"}
    names = {
        call_name.rsplit(".", 1)[-1]
        for call in ast.walk(init_db)
        if isinstance(call, ast.Call) and (call_name := _call_name(call.func))
    }
    assert not names & forbidden_calls


def test_migration_upgrade_functions_have_no_destructive_ddl_or_dml() -> None:
    """前向迁移不得含破坏性 DDL/DML。"""

    violations: list[str] = []
    for path in sorted((PROJECT_ROOT / "alembic" / "versions").glob("*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for upgrade in _migration_upgrade_functions(module, path):
            violations.extend(_destructive_migration_violations(upgrade, path=path))

    assert not violations, "\n".join(violations)


def test_migration_downgrades_only_fail_closed_without_destructive_ddl_or_dml() -> None:
    """回滚入口只能失败关闭，且迁移文件不能保留破坏性回滚语句。"""

    violations: list[str] = []
    for path in sorted((PROJECT_ROOT / "alembic" / "versions").glob("*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        downgrade = _module_function(module, "downgrade", path)
        statements = _statements_after_docstring(downgrade)
        assert len(statements) == 1, (
            f"{path.relative_to(PROJECT_ROOT)} 的 downgrade() 只能包含失败关闭的 RuntimeError。"
        )
        first_statement = _first_statement_after_docstring(downgrade)
        assert isinstance(first_statement, ast.Raise), path.relative_to(PROJECT_ROOT)
        assert first_statement.exc is not None, path.relative_to(PROJECT_ROOT)
        violations.extend(_destructive_migration_violations(downgrade, path=path))

    assert not violations, "\n".join(violations)


def test_new_migration_template_defaults_to_fail_closed_downgrade() -> None:
    template = (PROJECT_ROOT / "alembic" / "script.py.mako").read_text(encoding="utf-8")
    assert re.search(
        r'def downgrade\(\) -> None:\n\s+"""[^\n]+"""\n\s+raise RuntimeError\(',
        template,
    )
    assert "${downgrades" not in template
