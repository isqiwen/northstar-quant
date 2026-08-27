"""Static contracts for the one allowed PEP 517 materialization boundary."""

from __future__ import annotations

from tests.helpers.paths import PROJECT_ROOT


RUNNER = PROJECT_ROOT / "scripts" / "ci" / "bootstrap_pep517.py"
POLICY = PROJECT_ROOT / "scripts" / "ci" / "check_dependency_policy.py"
JUSTFILE = PROJECT_ROOT / "justfile"
DEV_SETUP = PROJECT_ROOT / "scripts" / "dev" / "setup.py"
DEPLOY_CONTROL = PROJECT_ROOT / "scripts" / "deploy" / "deploy.py"
DEPLOY_PACKAGE = PROJECT_ROOT / "scripts" / "deploy" / "package.py"
ARCHIVE_POLICY = PROJECT_ROOT / "scripts" / "deploy" / "archive_policy.py"
RELEASE_INSTALLER = PROJECT_ROOT / "scripts" / "deploy" / "install-release.sh"
VSCODE_TASKS = PROJECT_ROOT / ".vscode" / "tasks.json"


def test_runner_is_stdlib_only_and_closes_the_source_build_boundary() -> None:
    source = RUNNER.read_text(encoding="utf-8")

    assert "tomllib" not in source or "dependency_policy" in source
    assert "--no-python-downloads" in source
    assert "--no-seed" not in source
    assert "--no-build" in source
    assert "--inexact" in source
    assert "--no-install-project" in source
    assert "--no-install-package" in source
    assert "--offline" in source
    assert "--no-index" in source
    assert "--no-build-isolation" in source
    assert "_NoRedirect" in source
    assert "ProxyHandler({})" in source
    assert "TemporaryDirectory" in source
    assert "include-system-site-packages" in source
    assert "importlib.util" in source
    assert "_load_sibling_dependency_policy" in source
    assert "--managed-python-dir" in source
    assert "outside managed Python" in source
    assert "_development_staging_venv" in source
    assert "_cleanup_failed_development_staging_venv" in source
    assert "_promote_development_venv" in source
    assert "DEVELOPMENT_BOOTSTRAP_STATE_FILENAME" in source
    assert "HERMETIC_PEP517_BOOTSTRAP_REUSED" in source
    assert "UV_CACHE_DIR" in source
    assert "source-artifacts" in source
    assert "--refresh" in source
    assert 'venv_command.append("--clear")' not in source
    assert POLICY.is_file()


def test_local_post_bootstrap_uv_run_routes_are_non_sync_offline() -> None:
    for raw_line in JUSTFILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("python scripts/dev/run_uv.py run "):
            assert line.startswith("python scripts/dev/run_uv.py run --offline --no-sync ")

    dev_setup = DEV_SETUP.read_text(encoding="utf-8")
    assert '"uv", "sync"' not in dev_setup
    assert "repository_uv_executable" in dev_setup
    assert '"run", "--offline", "--no-sync"' in dev_setup

    runner = RUNNER.read_text(encoding="utf-8")
    assert "_repository_uv_executable" in runner
    assert "_release_uv_executable" in runner

    deploy_control = DEPLOY_CONTROL.read_text(encoding="utf-8")
    assert "_project_uv_executable" in deploy_control
    assert '"run", "--offline", "--no-sync"' in deploy_control
    assert '"run", "ruff", "check"' not in deploy_control

    tasks = VSCODE_TASKS.read_text(encoding="utf-8")
    assert '"command": "uv"' not in tasks
    assert '"command": "just"' not in tasks
    assert "scripts/dev/run_just.py" in tasks


def test_signed_release_uses_the_same_runner_and_exact_helper_allowlist() -> None:
    package = DEPLOY_PACKAGE.read_text(encoding="utf-8")
    archive_policy = ARCHIVE_POLICY.read_text(encoding="utf-8")
    installer = RELEASE_INSTALLER.read_text(encoding="utf-8")

    for required in (
        '"README.md"',
        '"scripts/ci/check_dependency_policy.py"',
        '"scripts/ci/bootstrap_pep517.py"',
    ):
        assert required in package
        assert required in archive_policy
        assert required in installer

    assert "uv python find --no-config --no-project --managed-python" in installer
    runner = '"${MANAGED_BOOTSTRAP_PYTHON}" -I "${STAGE_DIR}/scripts/ci/bootstrap_pep517.py"'
    assert runner in installer
    assert "--profile release --project-root \"${STAGE_DIR}\" --venv \"${VENV_BUILD_DIR}\"" in installer
    assert "--link-mode copy --python \"${PYTHON_VERSION}\"" not in installer
    assert "--link-mode copy --python \"${MANAGED_BOOTSTRAP_PYTHON}\"" in installer
    assert "--managed-python-dir \"${UV_PYTHON_INSTALL_DIR}\"" in installer
    assert "uv sync --directory \"${STAGE_DIR}\" --frozen" not in installer
    assert installer.index("seal_staged_release") < installer.index(runner) < installer.index(
        "验证并导入由服务账户构建的虚拟环境"
    )
