"""P9 offline supply-chain and credential-gate contracts."""

from __future__ import annotations

import re

from tests.helpers.paths import PROJECT_ROOT


JUSTFILE = PROJECT_ROOT / "justfile"
CI_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
SECURITY_POLICY = PROJECT_ROOT / "docs" / "platform_security_audit.md"
LOCAL_POSTGRES_COMPOSE = PROJECT_ROOT / "infra" / "docker" / "compose.yaml"


def _just_recipe(justfile: str, name: str) -> str:
    match = re.search(rf"(?ms)^{re.escape(name)}:\n(.*?)(?=^\S|\Z)", justfile)
    assert match is not None, f"missing just recipe: {name}"
    return match.group(0)


def _ci_job(workflow: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        workflow,
    )
    assert match is not None, f"missing CI job: {name}"
    return match.group(0)


def test_just_check_runs_locked_offline_policy_and_secret_gates() -> None:
    check = _just_recipe(JUSTFILE.read_text(encoding="utf-8"), "check")

    policy_check = check.index("python scripts/ci/check_dependency_policy.py")
    lock_check = check.index("uv lock --check --offline")
    assert policy_check < lock_check
    assert "python scripts/ci/check_dependency_policy.py" in check
    assert "python scripts/ci/check_secrets.py" in check


def test_just_bootstrap_is_the_only_explicit_local_sync_boundary() -> None:
    justfile = JUSTFILE.read_text(encoding="utf-8")
    bootstrap = _just_recipe(justfile, "env-bootstrap")

    policy_check = bootstrap.index("python scripts/ci/check_dependency_policy.py")
    lock_check = bootstrap.index("uv lock --check --offline")
    secret_check = bootstrap.index("python scripts/ci/check_secrets.py")
    runner = bootstrap.index("python scripts/ci/bootstrap_pep517.py --profile development")

    assert policy_check < lock_check < secret_check < runner
    assert "uv sync" not in justfile
    assert "uv run python scripts/dev/check_env.py" not in justfile
    for raw_line in justfile.splitlines():
        line = raw_line.strip()
        if line.startswith("uv run "):
            assert line.startswith("uv run --offline --no-sync ")


def test_each_tier_one_ci_job_validates_lock_and_policy_before_hermetic_bootstrap() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    for job_name in ("linux-platform", "windows-workstation"):
        job = _ci_job(workflow, job_name)
        policy_check = job.index("python scripts/ci/check_dependency_policy.py")
        lock_check = job.index("uv lock --check --offline")
        secret_check = job.index("python scripts/ci/check_secrets.py")
        bootstrap = job.index("python scripts/ci/bootstrap_pep517.py --profile ci")
        general_check = job.index("just check")

        assert policy_check < lock_check < secret_check < bootstrap < general_check
        assert "uv sync" not in job
        for raw_line in job.splitlines():
            line = raw_line.strip()
            if line.startswith("run: uv run ") or line.startswith("uv run "):
                assert "uv run --offline --no-sync " in line


def test_security_policy_scopes_offline_supply_chain_and_credential_allowances() -> None:
    policy = SECURITY_POLICY.read_text(encoding="utf-8")

    assert "不访问网络" in policy
    assert "CVE" in policy
    assert "不是" in policy
    assert "secret-scan: allow; reason: ..." in policy
    assert "test/CI fixture" in policy
    for disallowed_scope in ("业务源代码", "配置", "部署", "文档", "生产清单"):
        assert disallowed_scope in policy


def test_secret_scan_hardening_does_not_weaken_local_postgres_password_requirement() -> None:
    compose = LOCAL_POSTGRES_COMPOSE.read_text(encoding="utf-8")

    assert (
        "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}" in compose  # secret-scan: allow; reason: disposable test fixture
    )
