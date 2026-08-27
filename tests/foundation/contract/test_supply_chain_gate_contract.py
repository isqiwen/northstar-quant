"""P9 offline supply-chain and credential-gate contracts."""

from __future__ import annotations

import re

from tests.helpers.paths import PROJECT_ROOT


JUSTFILE = PROJECT_ROOT / "justfile"
SECURITY_POLICY = PROJECT_ROOT / "docs" / "GOVERNANCE.md"
ENV_TEMPLATE = PROJECT_ROOT / ".env.example"


def _just_recipe(justfile: str, name: str) -> str:
    match = re.search(rf"(?ms)^{re.escape(name)}:\n(.*?)(?=^\S|\Z)", justfile)
    assert match is not None, f"missing just recipe: {name}"
    return match.group(0)


def test_just_check_runs_locked_offline_policy_and_secret_gates() -> None:
    check = _just_recipe(JUSTFILE.read_text(encoding="utf-8"), "check")

    policy_check = check.index("python scripts/ci/check_dependency_policy.py")
    lock_check = check.index("python scripts/dev/run_uv.py lock --check --offline")
    assert policy_check < lock_check
    assert "python scripts/ci/check_dependency_policy.py" in check
    assert "python scripts/ci/check_secrets.py" in check


def test_just_bootstrap_is_the_only_explicit_local_sync_boundary() -> None:
    justfile = JUSTFILE.read_text(encoding="utf-8")
    bootstrap = _just_recipe(justfile, "env-bootstrap")

    policy_check = bootstrap.index("python scripts/ci/check_dependency_policy.py")
    lock_check = bootstrap.index("python scripts/dev/run_uv.py lock --check --offline")
    secret_check = bootstrap.index("python scripts/ci/check_secrets.py")
    runner = bootstrap.index("python scripts/ci/bootstrap_pep517.py --profile development")

    assert policy_check < lock_check < secret_check < runner
    assert "uv sync" not in justfile
    assert "uv run python scripts/dev/check_env.py" not in justfile
    for raw_line in justfile.splitlines():
        line = raw_line.strip()
        if line.startswith("python scripts/dev/run_uv.py run "):
            assert line.startswith("python scripts/dev/run_uv.py run --offline --no-sync ")

    refresh = _just_recipe(justfile, "env-bootstrap-refresh")
    refresh_policy = refresh.index("python scripts/ci/check_dependency_policy.py")
    refresh_lock = refresh.index("python scripts/dev/run_uv.py lock --check --offline")
    refresh_scan = refresh.index("python scripts/ci/check_secrets.py")
    refresh_runner = refresh.index(
        "python scripts/ci/bootstrap_pep517.py --profile development --refresh"
    )
    assert refresh_policy < refresh_lock < refresh_scan < refresh_runner


def test_security_policy_scopes_offline_supply_chain_and_credential_allowances() -> None:
    policy = SECURITY_POLICY.read_text(encoding="utf-8")

    assert "不访问网络" in policy
    assert "CVE" in policy
    assert "不是" in policy
    assert "secret-scan: allow; reason: ..." in policy
    assert "test fixture" in policy
    for disallowed_scope in ("业务源代码", "配置", "部署", "文档", "生产清单"):
        assert disallowed_scope in policy


def test_secret_scan_hardening_keeps_generated_native_postgres_credentials_untracked() -> None:
    environment_template = ENV_TEMPLATE.read_text(encoding="utf-8")

    assert "POSTGRES_PASSWORD=" in environment_template
    assert "本机 northstar 角色密码" in environment_template
    assert "空密码会生成并仅写入未跟踪 .env" in environment_template
    assert "已存在的角色、密码、认证规则、服务配置或数据不会被覆盖" in environment_template
    assert not (PROJECT_ROOT / "infra" / "docker" / "compose.yaml").exists()
