"""Linux 部署脚本的制品与安全门槛测试。"""

from __future__ import annotations

import re
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from tests.helpers.paths import PROJECT_ROOT
from scripts.deploy.inventory import InventoryError, load_inventory
from scripts.deploy.package import build_artifact

ROOT_DIR = PROJECT_ROOT
DEPLOY_DIR = ROOT_DIR / "scripts" / "deploy"
SYSTEMD_DIR = ROOT_DIR / "infra" / "systemd"
RUNTIME_PATH_KEYS = (
    "RUNTIME_STORAGE_DIR",
    "RUNTIME_DOWNLOADS_DIR",
    "RUNTIME_REPORTS_DIR",
    "RUNTIME_LOG_DIR",
    "RUNTIME_CACHE_DIR",
    "RUNTIME_MATPLOTLIB_DIR",
)
NTFY_DEPLOY_KEYS = (
    "NTFY_DEPLOY_ENABLED",
    "NTFY_PUBLIC_HOST",
    "NTFY_ACME_EMAIL",
    "NTFY_IMAGE",
    "NTFY_CADDY_IMAGE",
    "NTFY_CONFIG_DIR",
    "NTFY_DATA_DIR",
    "NTFY_CACHE_DURATION",
)
NTFY_DIR = DEPLOY_DIR / "ntfy"


def _resolve_bash_executable() -> str:
    """Return the Bash executable required by the Linux-only test contract."""

    bash_executable = shutil.which("bash")
    if bash_executable:
        return bash_executable

    pytest.skip("部署脚本契约测试需要 Linux x86_64 上可执行的 Bash。")


BASH_EXECUTABLE = _resolve_bash_executable()


def _bash_path(path: Path) -> str:
    """Return one Linux path for a Bash contract invocation."""

    return str(path)


def _run_bash(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
    """Capture Linux Bash deployment-script output as UTF-8."""

    return subprocess.run(
        [BASH_EXECUTABLE, *args],
        encoding="utf-8",
        errors="replace",
        **kwargs,
    )


def test_python_deploy_entrypoint_is_the_only_local_control_plane() -> None:
    control = (DEPLOY_DIR / "deploy.py").read_text(encoding="utf-8")

    assert "Linux x86_64 部署入口" in control
    assert not (ROOT_DIR / "scripts" / "deploy.sh").exists()
    assert not (DEPLOY_DIR / "deploy.sh").exists()
    assert not (DEPLOY_DIR / "build-artifact.sh").exists()


def test_repository_script_and_infrastructure_layout_is_explicit() -> None:
    """仓库级运行脚本与基础设施模板必须使用唯一的新路径。"""

    db_initializer = ROOT_DIR / "scripts" / "db" / "01_create_test_database.sql"

    assert not (ROOT_DIR / "infra" / "docker" / "compose.yaml").exists()
    assert not db_initializer.exists()
    assert not (ROOT_DIR / "compose.yaml").exists()
    assert not (ROOT_DIR / "scripts" / "postgres").exists()
    assert not (ROOT_DIR / "scripts" / "deploy" / "systemd").exists()
    assert not (ROOT_DIR / "scripts" / "check_mypy_baseline.py").exists()
    setup_script = (ROOT_DIR / "scripts" / "dev" / "setup.py").read_text(encoding="utf-8")
    assert 'LOCAL_DATABASE_HOST = "127.0.0.1"' in setup_script
    assert "_prepare_native_postgres" in setup_script
    assert "docker compose" not in setup_script
    assert "COMPOSE_FILE" not in setup_script
    assert not (ROOT_DIR / "scripts" / "setup_dev.sh").exists()
    assert not (ROOT_DIR / "scripts" / "setup_dev.ps1").exists()

    for directory in (
        ROOT_DIR / "scripts" / "build",
        ROOT_DIR / "scripts" / "data",
        ROOT_DIR / "scripts" / "ops",
        ROOT_DIR / "scripts" / "release",
        ROOT_DIR / "scripts" / "maintenance",
        ROOT_DIR / "scripts" / "tools",
        ROOT_DIR / "infra" / "ansible",
        ROOT_DIR / "infra" / "terraform",
        ROOT_DIR / "infra" / "kubernetes",
        ROOT_DIR / "infra" / "monitoring",
        ROOT_DIR / "infra" / "backup" / "northstar-quant",
    ):
        assert (directory / "README.md").is_file(), directory


def test_deploy_entrypoint_uses_single_active_env_file() -> None:
    content = (DEPLOY_DIR / "deploy.py").read_text(encoding="utf-8")

    assert 'requested_env_file = args.env_file or Path(".env")' in content
    assert 'env_file.name != ".env"' in content
    assert ".env.production" not in content


@pytest.mark.parametrize(
    "unsafe_name", ("", ".", "..", ".hidden-release", "release/escape", "release id")
)
def test_deploy_safe_name_rejects_empty_or_path_segment_values(unsafe_name: str) -> None:
    """Release-derived paths must never collapse onto an administrative parent."""

    result = _run_bash(
        "-c",
        'source "$1/lib/common.sh"; deploy_assert_safe_name RELEASE_ID "$2"',
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        unsafe_name,
        check=False,
        capture_output=True,
    )

    assert result.returncode != 0


def _run_safety_check(
    env_file: Path,
    *,
    service_mode: str,
    confirmation: str,
) -> subprocess.CompletedProcess[str]:
    command = """
set -euo pipefail
source "$1/lib/common.sh"
source "$1/lib/safety.sh"
deploy_validate_production_env "$2" "$3" "$4"
"""
    return _run_bash(
        *[
            "-c",
            command,
            BASH_EXECUTABLE,
            _bash_path(DEPLOY_DIR),
            _bash_path(env_file),
            service_mode,
            confirmation,
        ],
        check=False,
        capture_output=True,
    )


def test_build_artifact_contains_only_runtime_sources(tmp_path: Path) -> None:
    artifact = build_artifact(
        project_root=ROOT_DIR,
        output_dir=tmp_path,
        revision="test",
        built_at=datetime(2026, 7, 30, tzinfo=UTC),
    )
    artifact_path = artifact.path

    with tarfile.open(artifact_path, "r:gz") as archive:
        names = {name.removeprefix("./") for name in archive.getnames()}

    assert "pyproject.toml" in names
    assert "uv.lock" in names
    assert "src/northstar_quant/application/cli.py" in names
    assert "configs/profiles/offline/cn_futures_daily_trend_offline.yaml" in names
    assert "configs/app.example.yaml" in names
    assert "configs/app.yaml" not in names
    assert ".env" not in names
    assert not any("ntfy.bootstrap" in name for name in names)
    assert ".venv" not in names
    assert not any(Path(name).name.startswith("._") for name in names)
    assert not any(name.startswith("logs/") for name in names)
    assert not any(name.startswith("storage/") for name in names)
    assert not any(name.startswith("reports/") for name in names)
    assert not any(name.startswith("tests/") for name in names)
    assert "configs/app.local.yaml" not in names
    assert "configs/app.local.example.yaml" not in names

    builder = (DEPLOY_DIR / "package.py").read_text(encoding="utf-8")
    assert '"app.example.yaml"' in builder
    assert 'Path("configs/app.yaml")' in builder
    assert 'Path("configs/app.local.yaml")' in builder


def test_health_deploy_accepts_safe_production_environment(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "NORTHSTAR_ENV=production",
                "NORTHSTAR_DATABASE_URL=postgresql+psycopg://northstar:secret@db/northstar",  # secret-scan: allow; reason: disposable test fixture
                "NORTHSTAR_BROKER=paper",
                "NORTHSTAR_LIVE_TRADING_ENABLED=false",
            ]
        ),
        encoding="utf-8",
    )

    result = _run_safety_check(
        env_file,
        service_mode="health",
        confirmation="NO",
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "duplicate_assignment",
    (
        "NORTHSTAR_BROKER=ctp",
        "NORTHSTAR_LIVE_TRADING_ENABLED=true",
        "NORTHSTAR_DATABASE_URL=postgresql+psycopg://northstar:other@db/northstar",  # secret-scan: allow; reason: disposable test fixture
    ),
)
def test_deployment_rejects_duplicate_environment_assignments(
    tmp_path: Path,
    duplicate_assignment: str,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "NORTHSTAR_ENV=production",
                "NORTHSTAR_DATABASE_URL=postgresql+psycopg://northstar:secret@db/northstar",  # secret-scan: allow; reason: disposable test fixture
                "NORTHSTAR_BROKER=paper",
                "NORTHSTAR_LIVE_TRADING_ENABLED=false",
                duplicate_assignment,
            ]
        ),
        encoding="utf-8",
    )

    result = _run_safety_check(env_file, service_mode="health", confirmation="NO")

    assert result.returncode != 0
    assert "重复定义键" in result.stderr


def test_provision_checks_managed_environment_files_via_root_wrapper(tmp_path: Path) -> None:
    """配置目录可由 root:service 读取，SSH 部署身份绝不能加入该服务组。"""

    provision = (DEPLOY_DIR / "provision.sh").read_text(encoding="utf-8")
    production_validation = re.search(
        r"^validate_managed_production_environment\(\) \{.*?^\}\n\n"
        r"(?=validate_candidate_environment_cutover_precondition\(\))",
        provision,
        re.MULTILINE | re.DOTALL,
    )
    assert 'source "${SCRIPT_DIR}/lib/release_environment.sh"' in provision
    assert "managed_environment_file_is_regular()" not in provision
    assert "resolve_managed_active_environment_snapshot()" not in provision
    assert production_validation, "受管环境文件生产门禁必须封装为 root 调用。"

    environment_file = tmp_path / "candidate.env"
    environment_file.write_text(
        "\n".join(
            (
                "NORTHSTAR_ENV=production",
                "NORTHSTAR_DATABASE_URL=postgresql+psycopg://northstar:secret@db/northstar",  # secret-scan: allow; reason: disposable test fixture
                "NORTHSTAR_BROKER=paper",
                "NORTHSTAR_LIVE_TRADING_ENABLED=false",
            )
        ),
        encoding="utf-8",
    )
    calls_file = tmp_path / "root-wrapper-calls.log"
    command = (
        """
set -euo pipefail
CALLS="$1"
SCRIPT_DIR="$2"
ENVIRONMENT_FILE="$3"
SERVICE_MODE=health
CONFIRM_LIVE_DEPLOY=NO
SERVICE_USER=northstar
source "$SCRIPT_DIR/lib/common.sh"
source "$SCRIPT_DIR/lib/release_environment.sh"
deploy_as_root() {
  printf '%s\\n' "$*" >> "$CALLS"
  case "$1" in
    id)
      printf '%s\\n' 4242
      ;;
    stat)
      case "$3" in
        '%u:%a')
          printf '%s\n' 0:755
          ;;
        '%u:%g:%a')
          printf '%s\\n' 0:4242:750
          ;;
        '%u:%g:%a:%h')
          printf '%s\\n' 0:4242:640:1
          ;;
        *)
          return 91
          ;;
      esac
      ;;
    *)
      "$@"
      ;;
  esac
}
"""
        + production_validation.group(0)
        + """
deploy_assert_managed_environment_file "$ENVIRONMENT_FILE"
validate_managed_production_environment "$ENVIRONMENT_FILE"
"""
    )

    result = _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(calls_file),
        _bash_path(DEPLOY_DIR),
        _bash_path(environment_file),
        check=False,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    calls = calls_file.read_text(encoding="utf-8")
    assert "test -f" in calls
    assert "test -L" in calls
    assert "stat -c" in calls
    assert "id -g northstar" in calls
    assert "env -i" in calls


def test_provision_uses_the_shared_root_managed_active_environment_snapshot(tmp_path: Path) -> None:
    """The active pointer is valid only when its shared release chain is exact."""

    provision = (DEPLOY_DIR / "provision.sh").read_text(encoding="utf-8")
    shared_environment = (DEPLOY_DIR / "lib" / "release_environment.sh").read_text(encoding="utf-8")
    regular_check = re.search(
        r"^managed_environment_file_is_regular\(\) \{.*?^\}\n\n"
        r"(?=validate_managed_production_environment\(\))",
        provision,
        re.MULTILINE | re.DOTALL,
    )
    active_resolution = re.search(
        r"^resolve_managed_active_environment_snapshot\(\) \{.*?^\}\n\n"
        r'(?=if \[ "\${SETUP_SERVER\}")',
        provision,
        re.MULTILINE | re.DOTALL,
    )
    assert regular_check is None
    assert active_resolution is None
    assert 'source "${SCRIPT_DIR}/lib/release_environment.sh"' in provision
    assert "deploy_resolve_managed_active_environment_snapshot()" in shared_environment

    calls_file = tmp_path / "root-pointer-calls.log"
    command = (
        """
set -euo pipefail
CALLS="$1"
SCRIPT_DIR="$2"
source "$SCRIPT_DIR/lib/common.sh"
source "$SCRIPT_DIR/lib/release_environment.sh"
APP_ROOT=/opt/northstar
ENV_FILE=/etc/northstar/northstar-quant.env
CURRENT_LINK=/opt/northstar/current
RELEASES_DIR=/opt/northstar/releases
CONFIG_DIR=/etc/northstar
ENV_RELEASES_DIR=/etc/northstar/releases
SERVICE_USER=northstar
deploy_as_root() {
  printf '%s\\n' "$*" >> "$CALLS"
  case "$1" in
    test)
      case "$2:$3" in
        -L:/etc/northstar/northstar-quant.env|\
        -L:/opt/northstar/current|\
        -L:/opt/northstar/current/.env|\
        -d:/etc/northstar/releases|\
        -f:/etc/northstar/releases/release-20260822.env)
          return 0
          ;;
        -L:/etc/northstar/releases|\
        -L:/etc/northstar/releases/release-20260822.env)
          return 1
          ;;
      esac
      return 99
      ;;
    readlink)
      case "${2:-}:${3:-}:${4:-}" in
        --:/etc/northstar/northstar-quant.env:)
          printf '%s\\n' /opt/northstar/current/.env
          ;;
        --:/opt/northstar/current:)
          printf '%s\\n' /opt/northstar/releases/release-20260822
          ;;
        --:/opt/northstar/current/.env:)
          printf '%s\\n' /etc/northstar/releases/release-20260822.env
          ;;
        -f:--:/etc/northstar/northstar-quant.env)
          printf '%s\\n' /etc/northstar/releases/release-20260822.env
          ;;
        *)
          return 98
          ;;
      esac
      ;;
    id)
      printf '%s\\n' 4242
      ;;
    stat)
      case "$3" in
        '%u:%g:%a')
          printf '%s\\n' 0:4242:750
          ;;
        '%u:%g:%a:%h')
          printf '%s\\n' 0:4242:640:1
          ;;
        *)
          return 96
          ;;
      esac
      ;;
    *)
      return 97
      ;;
  esac
}
deploy_as_root() {
  printf '%s\\n' "$*" >> "$CALLS"
  case "$1" in
    test)
      case "$2:$3" in
        -d:/|-d:/opt|-d:/etc|\
        -d:/opt/northstar|\
        -d:/opt/northstar/releases|\
        -d:/opt/northstar/releases/release-20260822|\
        -d:/etc/northstar|\
        -d:/etc/northstar/releases|\
        -L:/etc/northstar/northstar-quant.env|\
        -L:/opt/northstar/current|\
        -L:/opt/northstar/current/.env|\
        -f:/etc/northstar/releases/release-20260822.env)
          return 0
          ;;
        -L:/|-L:/opt|-L:/etc|\
        -L:/opt/northstar|\
        -L:/opt/northstar/releases|\
        -L:/opt/northstar/releases/release-20260822|\
        -L:/etc/northstar|\
        -L:/etc/northstar/releases|\
        -L:/etc/northstar/releases/release-20260822.env)
          return 1
          ;;
        *)
          return 99
          ;;
      esac
      ;;
    readlink)
      case "${2:-}:${3:-}:${4:-}" in
        --:/etc/northstar/northstar-quant.env:)
          printf '%s\\n' /opt/northstar/current/.env
          ;;
        --:/opt/northstar/current:)
          printf '%s\\n' /opt/northstar/releases/release-20260822
          ;;
        --:/opt/northstar/current/.env:)
          printf '%s\\n' /etc/northstar/releases/release-20260822.env
          ;;
        -f:--:/etc/northstar/northstar-quant.env)
          printf '%s\\n' /etc/northstar/releases/release-20260822.env
          ;;
        *)
          return 98
          ;;
      esac
      ;;
    id)
      printf '%s\\n' 4242
      ;;
    stat)
      if [ "$3" = '%u:%g:%a:%h' ]; then
        [ "$5" = /etc/northstar/releases/release-20260822.env ] || return 96
        printf '%s\\n' 0:4242:640:1
        return 0
      fi
      if [ "$3" = '%u:%a' ]; then
        case "$5" in
          /|/opt|/etc|/opt/northstar|/opt/northstar/releases|\
          /opt/northstar/releases/release-20260822|/etc/northstar|/etc/northstar/releases)
            printf '%s\\n' 0:755
            ;;
          *)
            return 96
            ;;
        esac
        return 0
      fi
      [ "$3" = '%u:%g:%a' ] || return 96
      case "$5" in
        /opt/northstar|/opt/northstar/releases|/opt/northstar/releases/release-20260822)
          printf '%s\\n' 0:0:755
          ;;
        /etc/northstar|/etc/northstar/releases)
          printf '%s\\n' 0:4242:750
          ;;
        *)
          return 96
          ;;
      esac
      ;;
    *)
      return 97
      ;;
  esac
}
"""
        + "\ndeploy_resolve_managed_active_environment_snapshot\n"
    )

    result = _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(calls_file),
        _bash_path(DEPLOY_DIR),
        check=False,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "/etc/northstar/releases/release-20260822.env\n"
    calls = calls_file.read_text(encoding="utf-8")
    assert "readlink -- /etc/northstar/northstar-quant.env" in calls
    assert "readlink -f -- /etc/northstar/northstar-quant.env" in calls
    assert "test -f /etc/northstar/releases/release-20260822.env" in calls


def test_candidate_upload_validates_or_requires_a_completely_absent_active_chain(
    tmp_path: Path,
) -> None:
    """A partial previous release is never silently treated as a first deployment."""

    provision = (DEPLOY_DIR / "provision.sh").read_text(encoding="utf-8")
    precondition_start = provision.index("validate_candidate_environment_cutover_precondition() {")
    precondition_end = provision.index('\n}\n\nif [ "${SETUP_SERVER}"', precondition_start)
    precondition = provision[precondition_start : precondition_end + 2]
    candidate_branch = provision.index('if [ -n "${ENV_FILE_PATH}" ];')
    identity_check = provision.index("if ! deploy_assert_canonical_service_identity; then")
    candidate_precondition = provision.index(
        'if [ -n "${ENV_FILE_PATH}" ] || [ "${UPLOAD_NTFY_BOOTSTRAP}" = "1" ]; then',
    )
    environment_handoff = provision.index("  handoff_unprivileged_upload", candidate_branch)

    assert 'source "${SCRIPT_DIR}/lib/release_environment.sh"' in provision
    assert 'source "${SCRIPT_DIR}/lib/service_identity.sh"' in provision
    assert "managed_environment_file_is_regular()" not in provision
    assert "resolve_managed_active_environment_snapshot()" not in provision
    assert provision.index('if ! id "${SERVICE_USER}" >/dev/null 2>&1; then') < identity_check
    assert identity_check < candidate_precondition < candidate_branch < environment_handoff

    command = (
        """
set -euo pipefail
WORK_ROOT="$1"
STATE="$2"
ENV_FILE="${WORK_ROOT}/northstar-quant.env"
CURRENT_LINK="${WORK_ROOT}/current"
RESOLVER_MARKER="${WORK_ROOT}/resolver-called"
case "${STATE}" in
  partial-environment)
    : > "${ENV_FILE}"
    ;;
  partial-current)
    mkdir -- "${CURRENT_LINK}"
    ;;
  absent)
    ;;
  probe-failure)
    ;;
  *)
    exit 27
    ;;
esac
deploy_as_root() {
  if [ "${STATE}" = "probe-failure" ] && [ "$1" = env ]; then
    return 97
  fi
  "$@"
}
deploy_resolve_managed_active_environment_snapshot() {
  : > "${RESOLVER_MARKER}"
  return 1
}
"""
        + precondition
        + """
case "${STATE}" in
  absent)
    validate_candidate_environment_cutover_precondition
    [ ! -e "${RESOLVER_MARKER}" ]
    ;;
  probe-failure)
    if validate_candidate_environment_cutover_precondition; then
      exit 29
    fi
    [ ! -e "${RESOLVER_MARKER}" ]
    ;;
  *)
    if validate_candidate_environment_cutover_precondition; then
      exit 28
    fi
    [ -f "${RESOLVER_MARKER}" ]
    ;;
esac
"""
    )
    for state in ("absent", "partial-environment", "partial-current", "probe-failure"):
        state_root = tmp_path / state
        state_root.mkdir()
        result = _run_bash(
            "-c",
            command,
            BASH_EXECUTABLE,
            _bash_path(state_root),
            state,
            check=False,
            capture_output=True,
        )
        assert result.returncode == 0, f"{state}: {result.stderr}"


def test_candidate_ntfy_identity_cannot_change_before_main_release_cutover(
    tmp_path: Path,
) -> None:
    """NTFY cannot diverge from an old app release when a later install fails."""

    provision = (DEPLOY_DIR / "provision.sh").read_text(encoding="utf-8")
    helper_start = provision.index(
        "assert_candidate_ntfy_configuration_matches_active_snapshot() {"
    )
    helper_end = provision.index(
        "\n}\n\nvalidate_candidate_environment_cutover_precondition", helper_start
    )
    helper = provision[helper_start : helper_end + 2]
    active_chain_guard = provision.index(
        'if [ -n "${ENV_FILE_PATH}" ] || [ "${UPLOAD_NTFY_BOOTSTRAP}" = "1" ]; then'
    )
    candidate_branch = provision.index('if [ -n "${ENV_FILE_PATH}" ];')
    active_bootstrap_rejection = provision.index(
        'if [ -n "${ACTIVE_ENV_SNAPSHOT}" ] && [ "${UPLOAD_NTFY_BOOTSTRAP}" = "1" ]; then'
    )
    environment_handoff = provision.index("  handoff_unprivileged_upload", candidate_branch)
    ntfy_bootstrap_handoff = provision.index(
        "  handoff_unprivileged_upload", environment_handoff + 1
    )
    ntfy_identity_guard = provision.index(
        'if [ -n "${CANDIDATE_ENV_FILE}" ] && [ -n "${ACTIVE_ENV_SNAPSHOT}" ] &&'
    )
    ntfy_root_consumer = provision.index('APP_ENV_FILE="${NTFY_APP_ENV_FILE}"')

    for key in (
        "NORTHSTAR_ALERT_MODE",
        "NORTHSTAR_NTFY_BASE_URL",
        "NORTHSTAR_NTFY_TOPIC",
        "NORTHSTAR_NTFY_TOKEN",
    ):
        assert key in helper
    assert "deploy_read_env_value" in helper
    assert "printf" not in helper
    assert "deploy_log" not in helper
    assert (
        active_chain_guard
        < active_bootstrap_rejection
        < environment_handoff
        < ntfy_bootstrap_handoff
    )
    assert ntfy_identity_guard < ntfy_root_consumer

    active_environment = tmp_path / "active.env"
    candidate_environment = tmp_path / "candidate.env"
    token = "secret-token-must-not-appear-in-output"  # secret-scan: allow; reason: disposable test fixture
    active_environment.write_text(
        "\n".join(
            (
                "NORTHSTAR_ALERT_MODE=ntfy",
                "NORTHSTAR_NTFY_BASE_URL=https://ntfy.example.test",
                "NORTHSTAR_NTFY_TOPIC=northstar-alerts",
                f"NORTHSTAR_NTFY_TOKEN={token}",
            )
        ),
        encoding="utf-8",
    )
    candidate_environment.write_text(
        active_environment.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    command = (
        """
set -euo pipefail
SCRIPT_DIR="$1"
deploy_as_root() {
  "$@"
}
"""
        + helper
        + """
assert_candidate_ntfy_configuration_matches_active_snapshot "$2" "$3"
"""
    )
    matching = _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        _bash_path(candidate_environment),
        _bash_path(active_environment),
        check=False,
        capture_output=True,
    )
    assert matching.returncode == 0, matching.stderr

    candidate_environment.write_text(
        active_environment.read_text(encoding="utf-8").replace(
            token, "candidate-token-must-not-appear-in-output"
        ),
        encoding="utf-8",
    )
    mismatched = _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        _bash_path(candidate_environment),
        _bash_path(active_environment),
        check=False,
        capture_output=True,
    )
    assert mismatched.returncode != 0
    for secret in (token, "candidate-token-must-not-appear-in-output"):
        assert secret not in mismatched.stdout
        assert secret not in mismatched.stderr


def test_provision_lock_rejects_all_preexisting_root_state_paths(tmp_path: Path) -> None:
    """A root-state directory lock must never reuse a pre-existing object."""

    provision = (DEPLOY_DIR / "provision.sh").read_text(encoding="utf-8")
    lock_function = re.search(
        r"^acquire_deployment_lock\(\) \{.*?^\}\n\n(?=retain_deployment_lock\(\))",
        provision,
        re.MULTILINE | re.DOTALL,
    )
    assert lock_function

    remote_tmp = tmp_path / "remote-tmp"
    remote_tmp.mkdir(mode=0o700)
    unsafe_remote_tmp = tmp_path / "unsafe-remote-tmp"
    unsafe_remote_tmp.mkdir(mode=0o700)
    (unsafe_remote_tmp / "deployment.lock").write_text(
        "not-a-directory",
        encoding="utf-8",
    )
    preexisting_directory_tmp = tmp_path / "preexisting-directory-tmp"
    preexisting_directory_tmp.mkdir(mode=0o700)
    (preexisting_directory_tmp / "deployment.lock").mkdir(mode=0o700)
    command = (
        """
set -euo pipefail
DEPLOY_STATE_DIR="$1"
DEPLOY_LOCK_PATH="${DEPLOY_STATE_DIR}/deployment.lock"
DEPLOY_LOCK_ACQUIRED=0
DEPLOY_LOCK_METADATA=""
DEPLOY_LOCK_RELEASE_ID=""
DEPLOY_LOCK_RELEASE_ALLOWED=0
RELEASE_ID=release-20260822
deploy_fail() {
  printf '%s\\n' "$1" >&2
  exit 1
}
deploy_as_root() {
  "$@"
}
assert_deployment_lock_parent() {
  :
}
mkdir() {
  [ "$1" = "-m" ]
  [ "$2" = "0700" ]
  [ "$3" = "--" ]
  command mkdir "$4"
}
stat() {
  case "$2" in
    '%u:%g:%a:%d:%i')
      printf '%s\\n' 0:0:700:12:34
      ;;
    *)
      return 92
      ;;
  esac
}
"""
        + lock_function.group(0)
        + "\nacquire_deployment_lock\n"
        + '[ -d "${DEPLOY_LOCK_PATH}" ]\n'
        + '[ ! -L "${DEPLOY_LOCK_PATH}" ]\n'
    )

    safe_result = _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(remote_tmp),
        check=False,
        capture_output=True,
    )
    unsafe_result = _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(unsafe_remote_tmp),
        check=False,
        capture_output=True,
    )
    preexisting_directory_result = _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(preexisting_directory_tmp),
        check=False,
        capture_output=True,
    )

    assert safe_result.returncode == 0, safe_result.stderr
    assert unsafe_result.returncode != 0
    assert preexisting_directory_result.returncode != 0
    assert unsafe_result.stderr
    assert preexisting_directory_result.stderr


def test_health_deploy_rejects_live_environment(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "NORTHSTAR_ENV=production",
                "NORTHSTAR_DATABASE_URL=postgresql+psycopg://northstar:secret@db/northstar",  # secret-scan: allow; reason: disposable test fixture
                "NORTHSTAR_BROKER=ctp",
                "NORTHSTAR_LIVE_TRADING_ENABLED=true",
            ]
        ),
        encoding="utf-8",
    )

    result = _run_safety_check(
        env_file,
        service_mode="health",
        confirmation="YES",
    )

    assert result.returncode != 0
    assert "health 模式要求" in result.stderr


def test_non_paper_scheduler_requires_explicit_confirmation(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "NORTHSTAR_ENV=production",
                "NORTHSTAR_DATABASE_URL=postgresql+psycopg://northstar:secret@db/northstar",  # secret-scan: allow; reason: disposable test fixture
                "NORTHSTAR_BROKER=ctp",
                "NORTHSTAR_LIVE_TRADING_ENABLED=true",
            ]
        ),
        encoding="utf-8",
    )

    rejected = _run_safety_check(
        env_file,
        service_mode="scheduler",
        confirmation="NO",
    )
    accepted = _run_safety_check(
        env_file,
        service_mode="scheduler",
        confirmation="YES",
    )

    assert rejected.returncode != 0
    assert "CONFIRM_LIVE_DEPLOY=YES" in rejected.stderr
    assert accepted.returncode == 0, accepted.stderr


def test_runtime_output_paths_are_configurable_and_consistent() -> None:
    config_example = (ROOT_DIR / "deploy.env.example").read_text(encoding="utf-8")
    config_loader = (DEPLOY_DIR / "inventory.py").read_text(encoding="utf-8")
    runtime_paths = (DEPLOY_DIR / "lib" / "runtime_paths.sh").read_text(encoding="utf-8")
    deploy_control = (DEPLOY_DIR / "deploy.py").read_text(encoding="utf-8")
    provision_script = (DEPLOY_DIR / "provision.sh").read_text(encoding="utf-8")
    runtime_install_script = (DEPLOY_DIR / "install-runtime.sh").read_text(encoding="utf-8")
    release_script = (DEPLOY_DIR / "install-release.sh").read_text(encoding="utf-8")
    layout_script = (DEPLOY_DIR / "lib" / "layout.sh").read_text(encoding="utf-8")
    run_release_command = release_script.split("run_release_command() {", maxsplit=1)[1].split(
        "\n}\n\nsystemd_snapshot_path", maxsplit=1
    )[0]

    assert "SERVICE_HOME=" not in config_example
    assert "SERVICE_HOME" not in config_loader
    for expected_path in (
        "/opt/northstar",
        "/etc/northstar",
        "/var/lib/northstar",
        "/var/cache/northstar",
        "/var/log/northstar",
    ):
        assert expected_path in layout_script

    for key in RUNTIME_PATH_KEYS:
        assert f"{key}=" in config_example
        assert key in config_loader
        assert key in deploy_control
        assert key in provision_script
        assert key in release_script

    for template_name in ("health.service.in", "scheduler.service.in"):
        template = (SYSTEMD_DIR / template_name).read_text(encoding="utf-8")
        assert "Environment=NORTHSTAR_STORAGE_DIR=" not in template
        assert "Environment=NORTHSTAR_DOWNLOADS_DIR=" not in template
        assert "Environment=NORTHSTAR_REPORTS_DIR=" not in template
        assert "Environment=NORTHSTAR_LOG_DIR=" not in template
        assert "Environment=PYTHONDONTWRITEBYTECODE=1" in template
        assert "Environment=UV_CACHE_DIR=@UV_CACHE_DIR@" in template
        assert "Environment=XDG_CACHE_HOME=@RUNTIME_CACHE_DIR@" in template
        assert "Environment=MPLCONFIGDIR=@RUNTIME_MATPLOTLIB_DIR@" in template
        assert (
            "ReadWritePaths=@RUNTIME_STORAGE_DIR@ @RUNTIME_DOWNLOADS_DIR@ "
            "@RUNTIME_REPORTS_DIR@ @RUNTIME_LOG_DIR@ @RUNTIME_CACHE_DIR@ "
            "@RUNTIME_MATPLOTLIB_DIR@ @UV_CACHE_DIR@"
        ) in template
        assert "@SHARED_DIR@" not in template
        assert "@RUNTIME_LOG_DIR@" in template
        assert "EnvironmentFile=@CURRENT_LINK@/.env" in template
        assert "@ENV_FILE@" not in template

    health_template = (SYSTEMD_DIR / "health.service.in").read_text(encoding="utf-8")
    assert (
        "ExecStart=@CURRENT_LINK@/.venv/bin/northstar health --fail-on-blocked" in health_template
    )

    active_config_write = (
        "deploy_write_active_app_config \\\n"
        '  "${STAGE_DIR}/configs/app.example.yaml" \\\n'
        '  "${STAGE_DIR}/configs/app.yaml" \\\n'
        '  "${SERVICE_USER}"'
    )
    assert active_config_write in release_script
    assert (
        'deploy_write_runtime_config "${STAGE_DIR}/configs/app.local.yaml" "${SERVICE_USER}"'
        not in release_script
    )
    assert (
        'ln -s "${APP_LOCAL_CONFIG_FILE}" "${STAGE_DIR}/configs/app.local.yaml"'
        not in release_script
    )
    assert '"${SHARED_DIR}/config/app.local.yaml"' not in release_script
    assert "deploy_write_active_app_config" not in runtime_install_script
    assert release_script.index(active_config_write) < release_script.index(
        'run_release_command "${STAGE_DIR}" "${STAGE_DIR}/.venv/bin/northstar" init-db'
    )
    assert (
        'run_release_command "${STAGE_DIR}" "${STAGE_DIR}/.venv/bin/northstar" '
        "health --fail-on-blocked"
    ) in release_script
    activate_service = release_script.split("activate_service() {", maxsplit=1)[1].split(
        "\n}\n\nrollback_release", maxsplit=1
    )[0]
    assert 'systemctl is-active --quiet "${SYSTEMD_SERVICE_NAME}.service" || return 1' in (
        activate_service
    )
    assert (
        'run_release_command "${RELEASE_DIR}" "${RELEASE_DIR}/.venv/bin/northstar" '
        "health --fail-on-blocked"
    ) in activate_service
    assert "backup_systemd_unit" not in release_script
    assert "shared/incoming" not in release_script
    assert "restore_systemd_unit()" in release_script
    assert "systemd_snapshot_path()" in release_script
    assert "render_systemd_snapshot()" in release_script
    assert "assert_managed_unit_snapshot()" in release_script
    assert "assert_no_unit_dropins()" in release_script
    dropin_check = release_script.split("assert_no_unit_dropins() {", maxsplit=1)[1].split(
        "\n}\n\nassert_managed_unit_snapshot", maxsplit=1
    )[0]
    for expected_dropin_root in (
        "/etc/systemd/system.control/",
        "/run/systemd/system.control/",
        "/run/systemd/transient/",
        "/run/systemd/generator.early/",
        "/run/systemd/generator/",
        "/etc/systemd/system.attached/",
        "/run/systemd/system.attached/",
        "/usr/local/lib/systemd/system/",
        "/usr/local/share/systemd/system/",
        "/usr/share/systemd/system/",
        "/run/systemd/generator.late/",
    ):
        assert expected_dropin_root in dropin_check
    assert 'systemctl show -p DropInPaths --value "${service_name}.service"' in dropin_check
    assert "|| true" not in dropin_check
    assert "prepare_systemd_rollback()" in release_script
    assert "prepare_dashboard_systemd_transition()" in release_script
    assert 'assert_no_unit_dropins "${SYSTEMD_SERVICE_NAME}"' in release_script
    assert 'assert_no_unit_dropins "${DASHBOARD_SERVICE_NAME}"' in release_script
    assert "render_systemd_unit()" in release_script
    assert "install_rendered_systemd_unit()" in release_script
    assert "prepare_release_environment_snapshot()" in release_script
    assert "bind_staged_release_to_environment_snapshot()" in release_script
    assert "ensure_active_environment_pointer()" in release_script
    assert "promote_candidate_environment" not in release_script
    assert "restore_previous_environment" not in release_script
    assert "CANDIDATE_ENV_FILE" in release_script
    assert 'RELEASE_ENV_FILE="${ENV_RELEASES_DIR}/${RELEASE_ID}.env"' in release_script
    assert 'ln -s "${RELEASE_ENV_FILE}" "${release_link_temp}"' in release_script
    assert 'readlink -- "${ENV_FILE}"' in release_script
    assert '"${CURRENT_LINK}/.env"' in release_script
    assert release_script.index("seal_staged_release ||") < release_script.rindex(
        'deploy_as_user "${SERVICE_USER}" env'
    )
    assert release_script.index("render_systemd_unit ||") < release_script.index(
        'deploy_as_root mv "${STAGE_DIR}" "${RELEASE_DIR}"'
    )
    assert (
        release_script.index('deploy_as_root mv "${STAGE_DIR}" "${RELEASE_DIR}"')
        < release_script.index("\nprepare_systemd_rollback\n")
        < release_script.index("if ! stop_current_service; then")
    )
    assert (
        release_script.index("if ! stop_current_service; then")
        < release_script.index("if ! install_rendered_systemd_unit; then")
        < release_script.index("if ! switch_current_release; then")
        < release_script.index("if ! ensure_active_environment_pointer; then")
        < release_script.index("if ! activate_service; then")
    )
    assert "recover_interrupted_cutover()" in release_script
    assert "trap 'recover_interrupted_cutover $?' ERR" in release_script
    rollback = release_script.split("rollback_release() {", maxsplit=1)[1].split(
        "\n}\n\nprune_old_releases", maxsplit=1
    )[0]
    assert (
        rollback.index("restore_systemd_unit")
        < rollback.index('mv -Tf "${rollback_link}" "${CURRENT_LINK}"')
        < rollback.index('systemctl restart "${SYSTEMD_SERVICE_NAME}.service"')
    )
    assert "release_environment_snapshot_is_published()" in release_script
    assert (
        'release_environment_link_target="$(deploy_as_root readlink -- "${RELEASE_DIR}/.env")"'
        in release_script
    )
    assert "if release_environment_snapshot_is_published; then" in release_script
    assert (
        'RELEASE_ENV_FILE_CREATED=true\n  RELEASE_ENV_TEMP_FILE="$(deploy_as_root mktemp'
        in release_script
    )
    assert (
        'deploy_as_root mv "${STAGE_DIR}" "${RELEASE_DIR}"\nSTAGE_DIR=""\nRELEASE_ENV_FILE_CREATED=false'
        in release_script
    )
    assert (
        'deploy_as_root rm -f -- "${ENV_RELEASES_DIR}/${pruned_release_id}.env"' in release_script
    )
    assert 'deploy_assert_root_owned_directory "${RELEASES_DIR}" 0 755' in release_script
    assert 'assert_root_owned_tree_without_mounts "${release_dir}" || return 1' in release_script
    assert 'python3 -I "${SCRIPT_DIR}/mount_safety.py" "${tree_path}"' in release_script
    assert 'rm -rf --one-file-system -- "${release_dir}"' in release_script
    assert 'ln -s "${RUNTIME_LOG_DIR}" "${STAGE_DIR}/logs"' in release_script
    for environment_name in (
        "NORTHSTAR_STORAGE_DIR",
        "NORTHSTAR_DOWNLOADS_DIR",
        "NORTHSTAR_REPORTS_DIR",
        "NORTHSTAR_LOG_DIR",
    ):
        assert environment_name not in run_release_command
    assert "deploy_render_active_app_config()" in runtime_paths
    assert 'mv -Tf "${target_temp}" "${config_file}"' in runtime_paths
    assert not (DEPLOY_DIR / "systemd").exists()
    assert SYSTEMD_DIR.is_dir()
    assert 'local template_file="${release_dir}/infra/systemd/${template_name}"' in release_script
    assert "SYSTEMD_TEMPLATE_DIR" not in release_script
    root_release_runner = (DEPLOY_DIR / "root_release_runner.py").read_text(encoding="utf-8")
    assert "build_control_artifact(" in deploy_control
    assert "build_manifest(" in deploy_control
    assert "write_submission(" in deploy_control
    assert "_deployment_lock_path" not in deploy_control
    assert "DEPLOY_LOCK_PATH" not in deploy_control
    assert 'DEPLOY_LOCK_PATH: Final = DEPLOY_STATE_DIR / "release-gate.lock"' in root_release_runner
    assert "_exclusive_deploy_lock()" in root_release_runner
    assert "fcntl.flock" in root_release_runner
    assert 'DEPLOY_LOCK_PATH="${DEPLOY_STATE_DIR}/deployment.lock"' in provision_script
    assert 'if ! deploy_as_root mkdir -m 0700 -- "${DEPLOY_LOCK_PATH}" 2>/dev/null; then' in (
        provision_script
    )


def test_release_venv_is_imported_through_a_root_side_validator() -> None:
    """Dependency hooks run unprivileged; root never recursively seals their tree."""

    release_script = (DEPLOY_DIR / "install-release.sh").read_text(encoding="utf-8")
    receiver = (DEPLOY_DIR / "venv_archive.py").read_text(encoding="utf-8")

    assert 'env -i \\' in release_script
    assert '"${MANAGED_BOOTSTRAP_PYTHON}" -I "${STAGE_DIR}/scripts/ci/bootstrap_pep517.py"' in release_script
    assert "uv python find --no-config --no-project --managed-python" in release_script
    assert '--profile release --project-root "${STAGE_DIR}" --venv "${VENV_BUILD_DIR}"' in (
        release_script
    )
    assert '--managed-python-dir "${UV_PYTHON_INSTALL_DIR}"' in release_script
    assert 'deploy_as_user "${SERVICE_USER}" tar --create --dereference --hard-dereference' in (
        release_script
    )
    assert '/usr/bin/python3 -I "${SCRIPT_DIR}/venv_archive.py"' in release_script
    assert '--target-dir "${STAGE_DIR}/.venv"' in release_script
    assert '--temporary-dir "${DEPLOY_STATE_DIR}"' in release_script
    assert '--service-group "${SERVICE_USER}"' in release_script
    assert "chown -R root" not in release_script
    assert 'chown root:"${SERVICE_USER}" -- {} +' in release_script
    assert "chmod 0750 -- {} +" in release_script
    assert "chmod 0640 -- {} +" in release_script
    assert 'deploy_as_user "${SERVICE_USER}" rm -rf -- "${VENV_BUILD_DIR}"' in release_script
    assert "must be a regular file or directory" in receiver
    assert "unsupported virtual-environment root" in receiver
    assert "O_NOFOLLOW" in receiver


def test_cleanup_preserves_a_published_release_snapshot_but_removes_an_unpublished_one(
    tmp_path: Path,
) -> None:
    """An interrupt must not leave a published release with a dangling .env link."""

    release_script = (DEPLOY_DIR / "install-release.sh").read_text(encoding="utf-8")
    published_start = release_script.index("release_environment_snapshot_is_published() {")
    published_end = release_script.index("\n}\n\ncleanup_stage()", published_start)
    published_check = release_script[published_start : published_end + 2]
    cleanup_start = release_script.index("cleanup_stage() {")
    cleanup_end = release_script.index("\n}\ntrap cleanup_stage EXIT", cleanup_start)
    cleanup = release_script[cleanup_start : cleanup_end + 2]

    command = "\n".join(
        (
            "set -euo pipefail",
            'WORK_ROOT="$1"',
            'RELEASES_ROOT="${WORK_ROOT}/releases"',
            'ENV_ROOT="${WORK_ROOT}/environments"',
            'deploy_as_root() { "$@"; }',
            'deploy_as_user() { shift; "$@"; }',
            "deploy_assert_root_owned_directory() {",
            '  [ -d "$1" ] && [ ! -L "$1" ]',
            "}",
            "deploy_assert_managed_environment_file() {",
            '  [ -f "$1" ] && [ ! -L "$1" ]',
            "}",
            published_check,
            cleanup,
            "assert_root_owned_tree_without_mounts() { return 0; }",
            'mkdir -p "${RELEASES_ROOT}" "${ENV_ROOT}"',
            "# Simulate a signal immediately after stage -> release rename, before shell flags update.",
            'RELEASE_DIR="${RELEASES_ROOT}/published"',
            'RELEASE_ENV_FILE="${ENV_ROOT}/published.env"',
            'STAGE_DIR="${RELEASES_ROOT}/.published.stage.no-longer-present"',
            'VENV_BUILD_DIR=""',
            "RELEASE_ENV_FILE_CREATED=true",
            'RELEASE_ENV_TEMP_FILE=""',
            "CANDIDATE_ENV_UPLOADED=false",
            'CANDIDATE_ENV_FILE=""',
            'mkdir -p "${RELEASE_DIR}"',
            'printf published > "${RELEASE_ENV_FILE}"',
            'ln -s "${RELEASE_ENV_FILE}" "${RELEASE_DIR}/.env"',
            "cleanup_stage",
            '[ -f "${RELEASE_ENV_FILE}" ]',
            '[ "$(readlink -- "${RELEASE_DIR}/.env")" = "${RELEASE_ENV_FILE}" ]',
            '[ "${RELEASE_ENV_FILE_CREATED}" = false ]',
            "# Before publication, cleanup must still remove the stage and its private snapshot.",
            'RELEASE_DIR="${RELEASES_ROOT}/unpublished"',
            'RELEASE_ENV_FILE="${ENV_ROOT}/unpublished.env"',
            'STAGE_DIR="${RELEASES_ROOT}/.unpublished.stage"',
            'VENV_BUILD_DIR=""',
            "RELEASE_ENV_FILE_CREATED=true",
            'RELEASE_ENV_TEMP_FILE=""',
            "CANDIDATE_ENV_UPLOADED=false",
            'CANDIDATE_ENV_FILE=""',
            'mkdir -p "${STAGE_DIR}"',
            'printf unpublished > "${RELEASE_ENV_FILE}"',
            'ln -s "${RELEASE_ENV_FILE}" "${STAGE_DIR}/.env"',
            "cleanup_stage",
            '[ ! -e "${STAGE_DIR}" ]',
            '[ ! -e "${RELEASE_ENV_FILE}" ]',
            '[ ! -e "${RELEASE_DIR}" ]',
        )
    )

    result = _run_bash(
        "-c",
        command,
        "release-snapshot-cleanup-contract",
        _bash_path(tmp_path),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_active_environment_pointer_is_only_the_dynamic_current_release_link(
    tmp_path: Path,
) -> None:
    """A legacy regular file cannot quietly bypass release-bound configuration."""

    release_script = (DEPLOY_DIR / "install-release.sh").read_text(encoding="utf-8")
    pointer_match = re.search(
        r"^ensure_active_environment_pointer\(\) \{.*?^}\n",
        release_script,
        re.MULTILINE | re.DOTALL,
    )
    assert pointer_match
    command = "\n".join(
        (
            "set -euo pipefail",
            'WORK_ROOT="$1"',
            'CONFIG_DIR="${WORK_ROOT}/etc"',
            'CURRENT_LINK="${WORK_ROOT}/opt/current"',
            'ENV_FILE="${CONFIG_DIR}/northstar-quant.env"',
            'RELEASE_ID="release-contract"',
            'APP_NAME="northstar-quant"',
            'deploy_as_root() { "$@"; }',
            'mkdir -p "${CONFIG_DIR}" "${WORK_ROOT}/opt/release-contract"',
            'printf active > "${WORK_ROOT}/opt/release-contract/.env"',
            'ln -s "${WORK_ROOT}/opt/release-contract" "${CURRENT_LINK}"',
            pointer_match.group(0),
            "ensure_active_environment_pointer",
            '[ -L "${ENV_FILE}" ]',
            '[ "$(readlink -- "${ENV_FILE}")" = "${CURRENT_LINK}/.env" ]',
            'rm -f -- "${ENV_FILE}"',
            'printf legacy > "${ENV_FILE}"',
            "if ensure_active_environment_pointer; then exit 18; fi",
        )
    )

    result = _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(tmp_path),
        check=False,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr


def test_systemd_snapshot_checks_fail_closed_for_uninspectable_or_overridden_units() -> None:
    release_script = (DEPLOY_DIR / "install-release.sh").read_text(encoding="utf-8")
    validation_start = release_script.index("assert_no_foreign_unit_fragment() {")
    validation_end = release_script.index("\n\nassert_managed_unit_snapshot()", validation_start)
    validation_functions = release_script[validation_start:validation_end]
    command = "\n".join(
        (
            "set -euo pipefail",
            "deploy_as_root() {",
            '  if [ "$1" = "test" ]; then return 1; fi',
            '  "$@"',
            "}",
            validation_functions,
            "# First install permits only an explicit, queryable not-found state.",
            "systemctl() {",
            '  if [ "$1" != "show" ]; then return 97; fi',
            '  case "$3" in',
            '    LoadState) printf "%s\\n" "not-found"; return 0 ;;',
            # Correct not-found branches must not query FragmentPath or
            # DropInPaths, because systemctl may reject either property for
            # an absent unit.
            "    DropInPaths) return 88 ;;",
            "    FragmentPath) return 89 ;;",
            "    *) return 98 ;;",
            "  esac",
            "}",
            (
                'if ! assert_no_foreign_unit_fragment "northstar-quant" '
                '"/etc/systemd/system/northstar-quant.service"; then exit 8; fi'
            ),
            'if ! assert_no_unit_dropins "northstar-quant"; then exit 9; fi',
            (
                'if ! assert_no_foreign_unit_fragment "northstar-quant-dashboard" '
                '"/etc/systemd/system/northstar-quant-dashboard.service"; then exit 10; fi'
            ),
            'if ! assert_no_unit_dropins "northstar-quant-dashboard"; then exit 11; fi',
            "# A LoadState query failure is not evidence that the unit is absent.",
            "systemctl() { return 1; }",
            (
                'if assert_no_foreign_unit_fragment "northstar-quant" '
                '"/etc/systemd/system/northstar-quant.service"; then exit 12; fi'
            ),
            (
                'if assert_no_foreign_unit_fragment "northstar-quant-dashboard" '
                '"/etc/systemd/system/northstar-quant-dashboard.service"; then exit 13; fi'
            ),
            'if assert_no_unit_dropins "northstar-quant"; then exit 14; fi',
            'if assert_no_unit_dropins "northstar-quant-dashboard"; then exit 15; fi',
            "systemctl() {",
            '  if [ "$1" = "show" ]; then printf "%s\\n" "/unmanaged/drop-in.conf"; return 0; fi',
            "  return 1",
            "}",
            'if assert_no_unit_dropins "northstar-quant"; then exit 16; fi',
            'if assert_no_unit_dropins "northstar-quant-dashboard"; then exit 17; fi',
            "blocked_path=",
            "deploy_as_root() {",
            '  if [ "$1" = "test" ]; then',
            '    if [ "$2" = "-e" ] && [ "$3" = "$blocked_path" ]; then return 0; fi',
            "    return 1",
            "  fi",
            '  "$@"',
            "}",
            'blocked_path="/etc/systemd/system.attached/northstar-quant.service.d"',
            'if assert_no_unit_dropins "northstar-quant"; then exit 18; fi',
            'blocked_path="/run/systemd/system.attached/northstar-quant-dashboard.service.d"',
            'if assert_no_unit_dropins "northstar-quant-dashboard"; then exit 19; fi',
        )
    )

    result = _run_bash(
        "-c",
        command,
        "systemd-snapshot-contract",
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_systemd_allows_only_the_exact_managed_unit_fragment() -> None:
    """升级可保留当前受管 unit，但不得接受其它 systemd 搜索根。"""

    release_script = (DEPLOY_DIR / "install-release.sh").read_text(encoding="utf-8")
    foreign_check_start = release_script.index("assert_no_foreign_unit_fragment() {")
    foreign_check_end = release_script.index("\n\nassert_no_unit_dropins()", foreign_check_start)
    foreign_check = release_script[foreign_check_start:foreign_check_end]
    command = "\n".join(
        (
            "set -euo pipefail",
            "present_path=",
            "managed_path=",
            "deploy_as_root() {",
            '  if [ "$1" = "test" ]; then',
            '    if [ "$2" = "-e" ] && [ "$3" = "$present_path" ]; then return 0; fi',
            "    return 1",
            "  fi",
            '  "$@"',
            "}",
            "systemctl() {",
            '  if [ "$1" != "show" ]; then return 97; fi',
            '  case "$3" in',
            '    LoadState) printf "%s\\n" "loaded"; return 0 ;;',
            '    FragmentPath) printf "%s\\n" "$managed_path"; return 0 ;;',
            "    *) return 98 ;;",
            "  esac",
            "}",
            foreign_check,
            'managed_path="/etc/systemd/system/northstar-quant.service"',
            'present_path="$managed_path"',
            (
                'if ! assert_no_foreign_unit_fragment "northstar-quant" '
                '"$managed_path"; then exit 30; fi'
            ),
            'managed_path="/etc/systemd/system/northstar-quant-dashboard.service"',
            'present_path="$managed_path"',
            (
                'if ! assert_no_foreign_unit_fragment "northstar-quant-dashboard" '
                '"$managed_path"; then exit 31; fi'
            ),
            'managed_path="/etc/systemd/system/northstar-quant.service"',
            'present_path="/usr/lib/systemd/system/northstar-quant.service"',
            (
                'if assert_no_foreign_unit_fragment "northstar-quant" '
                '"$managed_path"; then exit 32; fi'
            ),
            'managed_path="/etc/systemd/system/northstar-quant-dashboard.service"',
            'present_path="/run/systemd/system/northstar-quant-dashboard.service"',
            (
                'if assert_no_foreign_unit_fragment "northstar-quant-dashboard" '
                '"$managed_path"; then exit 33; fi'
            ),
        )
    )

    result = _run_bash(
        "-c",
        command,
        "managed-systemd-fragment-contract",
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_rollback_restores_exact_enablement_and_rejects_sticky_disable() -> None:
    """回退必须清除新版本残留的 enable，并验证恢复后的精确状态。"""

    release_script = (DEPLOY_DIR / "install-release.sh").read_text(encoding="utf-8")
    enablement_state_start = release_script.index("managed_service_enablement_state() {")
    enablement_state_end = release_script.index(
        "\n}\n\nreset_managed_service_to_disabled()", enablement_state_start
    )
    enablement_state = release_script[enablement_state_start : enablement_state_end + 2]
    reset_start = release_script.index("reset_managed_service_to_disabled() {")
    reset_end = release_script.index("\n}\n\ndisable_current_service()", reset_start)
    reset_enablement = release_script[reset_start : reset_end + 2]
    restore_start = release_script.index("restore_previous_service_enablement() {")
    restore_end = release_script.index("\n}\n\nactivate_service()", restore_start)
    restore_enablement = release_script[restore_start : restore_end + 2]
    rollback_start = release_script.index("rollback_release() {")
    rollback_end = release_script.index("\n}\n\nrecover_interrupted_cutover()", rollback_start)
    rollback = release_script[rollback_start:rollback_end]
    assert "if ! restore_previous_service_enablement; then" in rollback
    command = "\n".join(
        (
            "set -euo pipefail",
            'SYSTEMD_SERVICE_NAME="northstar-quant"',
            'EVENT_LOG="$(mktemp)"',
            "trap 'rm -f -- \"$EVENT_LOG\"' EXIT",
            'deploy_as_root() { "$@"; }',
            'enable_managed_unit() { systemctl enable "$1.service"; }',
            "systemctl() {",
            '  printf "%s\\n" "$1" >> "$EVENT_LOG"',
            '  case "$1" in',
            "    disable)",
            '      if [ "${DISABLE_MODE}" != "sticky" ]; then ENABLED=false; fi',
            "      return 0",
            "      ;;",
            "    enable)",
            "      ENABLED=true",
            "      return 0",
            "      ;;",
            "    is-enabled)",
            '      if [ "${ENABLED}" = true ]; then printf "enabled\\n"; return 0; fi',
            '      printf "disabled\\n"',
            "      return 1",
            "      ;;",
            "  esac",
            "  return 97",
            "}",
            enablement_state,
            reset_enablement,
            restore_enablement,
            "assert_events() {",
            '  [ "$(tr "\\n" " " < "$EVENT_LOG")" = "$1" ]',
            "}",
            "# A partially successful new enable must not survive a rollback to disabled.",
            "PREVIOUS_SERVICE_ENABLED=false",
            "ENABLED=true",
            "DISABLE_MODE=normal",
            ': > "$EVENT_LOG"',
            "restore_previous_service_enablement",
            '[ "${ENABLED}" = false ]',
            'assert_events "disable is-enabled "',
            "# An enabled previous release is reset, then re-enabled and verified.",
            "PREVIOUS_SERVICE_ENABLED=true",
            "ENABLED=true",
            "DISABLE_MODE=normal",
            ': > "$EVENT_LOG"',
            "restore_previous_service_enablement",
            '[ "${ENABLED}" = true ]',
            'assert_events "disable is-enabled enable is-enabled "',
            "# If disable returns success but leaves the unit enabled, fail closed before enable.",
            "PREVIOUS_SERVICE_ENABLED=false",
            "ENABLED=true",
            "DISABLE_MODE=sticky",
            ': > "$EVENT_LOG"',
            "if restore_previous_service_enablement; then exit 41; fi",
            '[ "${ENABLED}" = true ]',
            'assert_events "disable is-enabled "',
        )
    )

    result = _run_bash(
        "-c",
        command,
        "rollback-enablement-contract",
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_first_install_rollback_clears_partial_enablement_before_unit_removal(
    tmp_path: Path,
) -> None:
    """首次部署回退也必须清除新 unit 留下的 enable 链接。"""

    release_script = (DEPLOY_DIR / "install-release.sh").read_text(encoding="utf-8")
    enablement_state_start = release_script.index("managed_service_enablement_state() {")
    enablement_state_end = release_script.index(
        "\n}\n\nreset_managed_service_to_disabled()", enablement_state_start
    )
    enablement_state = release_script[enablement_state_start : enablement_state_end + 2]
    reset_start = release_script.index("reset_managed_service_to_disabled() {")
    reset_end = release_script.index("\n}\n\ndisable_current_service()", reset_start)
    reset_enablement = release_script[reset_start : reset_end + 2]
    restore_start = release_script.index("restore_systemd_unit() {")
    restore_end = release_script.index(
        "\n}\n\nprepare_release_environment_snapshot()", restore_start
    )
    restore_unit = release_script[restore_start : restore_end + 2]
    command = "\n".join(
        (
            "set -euo pipefail",
            'SYSTEMD_SERVICE_NAME="northstar-quant"',
            'SYSTEMD_UNIT_FILE="$1"',
            'EVENT_LOG="$(mktemp)"',
            "trap 'rm -f -- \"$EVENT_LOG\"' EXIT",
            'deploy_as_root() { "$@"; }',
            "systemctl() {",
            '  printf "%s\\n" "$1" >> "$EVENT_LOG"',
            '  case "$1" in',
            "    disable)",
            '      if [ "${DISABLE_MODE}" != "sticky" ]; then ENABLED=false; fi',
            "      return 0",
            "      ;;",
            "    is-enabled)",
            '      if [ "${ENABLED}" = true ]; then printf "enabled\\n"; return 0; fi',
            '      printf "disabled\\n"',
            "      return 1",
            "      ;;",
            "    daemon-reload) return 0 ;;",
            "  esac",
            "  return 97",
            "}",
            enablement_state,
            reset_enablement,
            restore_unit,
            "assert_events() {",
            '  [ "$(tr "\\n" " " < "$EVENT_LOG")" = "$1" ]',
            "}",
            "# A new unit may have been enabled before first-install activation fails.",
            'PREVIOUS_RELEASE=""',
            "ENABLED=true",
            "DISABLE_MODE=normal",
            'printf "[Unit]\\n" > "$SYSTEMD_UNIT_FILE"',
            ': > "$EVENT_LOG"',
            "restore_systemd_unit",
            '[ "${ENABLED}" = false ]',
            '[ ! -e "$SYSTEMD_UNIT_FILE" ]',
            'assert_events "disable is-enabled daemon-reload "',
            "# A false-success disable must stop the rollback before unit removal.",
            "ENABLED=true",
            "DISABLE_MODE=sticky",
            'printf "[Unit]\\n" > "$SYSTEMD_UNIT_FILE"',
            ': > "$EVENT_LOG"',
            "if restore_systemd_unit; then exit 51; fi",
            '[ "${ENABLED}" = true ]',
            '[ -e "$SYSTEMD_UNIT_FILE" ]',
            'assert_events "disable is-enabled "',
        )
    )

    result = _run_bash(
        "-c",
        command,
        "first-install-rollback-enablement-contract",
        _bash_path(tmp_path / "northstar-quant.service"),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_active_app_config_renders_runtime_paths_from_full_template(tmp_path: Path) -> None:
    template_file = tmp_path / "app.example.yaml"
    template_file.write_text(
        (ROOT_DIR / "configs" / "app.example.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    command = """
set -euo pipefail
source "$1/lib/common.sh"
source "$1/lib/runtime_paths.sh"
RUNTIME_STORAGE_DIR=/mnt/northstar-quant/market-storage
RUNTIME_DOWNLOADS_DIR=/data/northstar-quant/download-cache
RUNTIME_REPORTS_DIR=/mnt/northstar-quant/reports
RUNTIME_LOG_DIR=/var/log/northstar-quant
deploy_render_active_app_config "$2"
"""

    result = _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        _bash_path(template_file),
        check=False,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    template = yaml.safe_load(template_file.read_text(encoding="utf-8"))
    assert set(rendered) == set(template)
    assert rendered["logging"] == template["logging"]
    assert rendered["runtime"] == {
        "storage_dir": "/mnt/northstar-quant/market-storage",
        "downloads_dir": "/data/northstar-quant/download-cache",
        "reports_dir": "/mnt/northstar-quant/reports",
        "log_dir": "/var/log/northstar-quant",
    }


def test_active_app_config_staging_never_uses_an_inherited_tmpdir() -> None:
    """Root rendering cannot redirect through deployment-user TMPDIR state."""

    runtime_paths = (DEPLOY_DIR / "lib" / "runtime_paths.sh").read_text(encoding="utf-8")
    write_function = runtime_paths.split(
        "deploy_write_active_app_config() {", maxsplit=1
    )[1].split("\n}\n", maxsplit=1)[0]

    assert "${TMPDIR" not in write_function
    assert '[ "${EUID}" -ne 0 ]' in write_function
    assert "deploy_state_metadata" in write_function
    assert 'deploy_as_root mktemp "${DEPLOY_STATE_DIR}/.northstar-app-config.XXXXXX"' in (
        write_function
    )
    assert 'deploy_as_root rm -f -- "${source_temp}"' in write_function


def test_runtime_output_path_defaults_and_custom_values() -> None:
    command = """
set -euo pipefail
source "$1/lib/common.sh"
source "$1/lib/runtime_paths.sh"
RUNTIME_STORAGE_DIR="$2"
RUNTIME_DOWNLOADS_DIR="$3"
RUNTIME_REPORTS_DIR="$4"
RUNTIME_LOG_DIR="$5"
RUNTIME_CACHE_DIR="$6"
RUNTIME_MATPLOTLIB_DIR="$7"
deploy_configure_runtime_paths /opt/northstar /var/lib/northstar /var/cache/northstar /var/log/northstar
printf '%s|%s|%s|%s|%s|%s\\n' "$RUNTIME_STORAGE_DIR" "$RUNTIME_DOWNLOADS_DIR" "$RUNTIME_REPORTS_DIR" "$RUNTIME_LOG_DIR" "$RUNTIME_CACHE_DIR" "$RUNTIME_MATPLOTLIB_DIR"
"""

    default_result = _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        "",
        "",
        "",
        "",
        "",
        "",
        check=False,
        capture_output=True,
    )

    assert default_result.returncode == 0, default_result.stderr
    assert default_result.stdout.strip().split("|") == [
        "/var/lib/northstar/storage",
        "/var/lib/northstar/downloads",
        "/var/lib/northstar/reports",
        "/var/log/northstar/app",
        "/var/cache/northstar/runtime",
        "/var/cache/northstar/matplotlib",
    ]

    external_storage_default_downloads_result = _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        "/mnt/northstar-quant/storage",
        "",
        "",
        "",
        "",
        "",
        check=False,
        capture_output=True,
    )

    assert external_storage_default_downloads_result.returncode == 0, (
        external_storage_default_downloads_result.stderr
    )
    assert external_storage_default_downloads_result.stdout.strip().split("|")[:2] == [
        "/mnt/northstar-quant/storage",
        "/var/lib/northstar/downloads",
    ]

    custom_result = _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        "/mnt/northstar-quant/storage",
        "/mnt/northstar-quant/downloads",
        "/mnt/northstar-quant/reports",
        "/var/log/northstar/custom",
        "/var/cache/northstar/custom",
        "/var/cache/northstar/custom-matplotlib",
        check=False,
        capture_output=True,
    )

    assert custom_result.returncode == 0, custom_result.stderr
    assert custom_result.stdout.strip().split("|") == [
        "/mnt/northstar-quant/storage",
        "/mnt/northstar-quant/downloads",
        "/mnt/northstar-quant/reports",
        "/var/log/northstar/custom",
        "/var/cache/northstar/custom",
        "/var/cache/northstar/custom-matplotlib",
    ]


def test_runtime_output_paths_reject_unsafe_location() -> None:
    relative_path_command = """
set -euo pipefail
source "$1/lib/common.sh"
source "$1/lib/runtime_paths.sh"
RUNTIME_STORAGE_DIR=relative/storage
deploy_configure_runtime_paths /opt/northstar /var/lib/northstar /var/cache/northstar /var/log/northstar
"""

    relative_path_result = _run_bash(
        "-c",
        relative_path_command,
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        check=False,
        capture_output=True,
    )

    assert relative_path_result.returncode != 0
    assert "RUNTIME_STORAGE_DIR" in relative_path_result.stderr

    release_path_command = """
set -euo pipefail
source "$1/lib/common.sh"
source "$1/lib/runtime_paths.sh"
RUNTIME_STORAGE_DIR=/opt/northstar/releases/storage
deploy_configure_runtime_paths /opt/northstar /var/lib/northstar /var/cache/northstar /var/log/northstar
"""
    release_path_result = _run_bash(
        "-c",
        release_path_command,
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        check=False,
        capture_output=True,
    )

    assert release_path_result.returncode != 0
    assert "RUNTIME_STORAGE_DIR" in release_path_result.stderr

    nested_runtime_path_command = """
set -euo pipefail
source "$1/lib/common.sh"
source "$1/lib/runtime_paths.sh"
RUNTIME_STORAGE_DIR=/mnt/northstar-quant/storage
RUNTIME_DOWNLOADS_DIR=/mnt/northstar-quant/storage/downloads
deploy_configure_runtime_paths /opt/northstar /var/lib/northstar /var/cache/northstar /var/log/northstar
"""
    nested_runtime_path_result = _run_bash(
        "-c",
        nested_runtime_path_command,
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        check=False,
        capture_output=True,
    )

    assert nested_runtime_path_result.returncode != 0
    assert "RUNTIME_DOWNLOADS_DIR" in nested_runtime_path_result.stderr

    noncanonical_runtime_path_command = """
set -euo pipefail
source "$1/lib/common.sh"
source "$1/lib/runtime_paths.sh"
RUNTIME_STORAGE_DIR=/mnt/northstar-quant//storage
deploy_configure_runtime_paths /opt/northstar /var/lib/northstar /var/cache/northstar /var/log/northstar
"""
    noncanonical_runtime_path_result = _run_bash(
        "-c",
        noncanonical_runtime_path_command,
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        check=False,
        capture_output=True,
    )

    assert noncanonical_runtime_path_result.returncode != 0
    assert "RUNTIME_STORAGE_DIR" in noncanonical_runtime_path_result.stderr

    reserved_runtime_path_command = """
set -euo pipefail
source "$1/lib/common.sh"
source "$1/lib/runtime_paths.sh"
RUNTIME_CACHE_DIR=/var/cache/northstar/dashboard
deploy_configure_runtime_paths /opt/northstar /var/lib/northstar /var/cache/northstar /var/log/northstar
"""
    reserved_runtime_path_result = _run_bash(
        "-c",
        reserved_runtime_path_command,
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        check=False,
        capture_output=True,
    )

    assert reserved_runtime_path_result.returncode != 0
    assert "RUNTIME_CACHE_DIR" in reserved_runtime_path_result.stderr


def test_runtime_leaf_helpers_fail_closed_and_never_repair_existing_leaf() -> None:
    runtime_paths = (DEPLOY_DIR / "lib" / "runtime_paths.sh").read_text(encoding="utf-8")
    runtime_install = (DEPLOY_DIR / "install-runtime.sh").read_text(encoding="utf-8")
    release_install = (DEPLOY_DIR / "install-release.sh").read_text(encoding="utf-8")

    assert "deploy_prepare_runtime_parent_directory()" in runtime_paths
    assert "deploy_prepare_runtime_leaf_directory()" in runtime_paths
    assert "deploy_assert_runtime_leaf_directory()" in runtime_paths
    assert 'if [ "${requested_path}" != "${normalized_path}" ]; then' in runtime_paths
    assert "deploy_assert_runtime_paths_do_not_overlap_reserved_leaves" in runtime_paths
    assert 'deploy_as_root mkdir -m 0750 -- "${runtime_dir}"' in runtime_paths
    assert "Existing service-writable leaves are never chmod/chown repaired" in runtime_paths
    existing_leaf_branch = runtime_paths.split(
        'if deploy_as_root test -e "${runtime_dir}" || deploy_as_root test -L "${runtime_dir}"; then',
        maxsplit=1,
    )[1].split("\n  fi\n\n  if ! deploy_as_root mkdir", maxsplit=1)[0]
    assert "deploy_as_root chown" not in existing_leaf_branch
    assert "deploy_as_root chmod" not in existing_leaf_branch
    assert 'install -d -o "${SERVICE_USER}"' not in runtime_install
    assert (
        'deploy_prepare_runtime_leaf_directory "${runtime_dir}" "${SERVICE_USER}"'
        in runtime_install
    )
    assert 'DASHBOARD_HOME_DIR="${CACHE_DIR}/dashboard"' in release_install
    assert 'VENV_BUILD_ROOT="${CACHE_DIR}/venv-build"' in release_install
    assert (
        'deploy_prepare_runtime_parent_directory "${runtime_parent_dir}" "${SERVICE_USER}"'
        in release_install
    )
    assert (
        '"${UV_CACHE_DIR}" "${VENV_BUILD_ROOT}"; do\n'
        '  if ! deploy_prepare_runtime_leaf_directory "${runtime_dir}" "${SERVICE_USER}"; then'
        in release_install
    )
    assert (
        'deploy_prepare_runtime_leaf_directory "${DASHBOARD_HOME_DIR}" "${SERVICE_USER}"'
        in release_install
    )
    assert 'deploy_as_user "${SERVICE_USER}" install -d -m 0700 "${VENV_BUILD_ROOT}"' not in (
        release_install
    )
    assert 'deploy_as_user "${SERVICE_USER}" test -d "${VENV_BUILD_ROOT}"' in release_install


def test_runtime_leaf_helper_does_not_chown_or_chmod_an_existing_leaf(tmp_path: Path) -> None:
    """The existing-leaf branch is intentionally validation-only."""

    existing_leaf = tmp_path / "existing-leaf"
    existing_leaf.mkdir()
    mutation_marker = tmp_path / "unexpected-mutation"
    command = """
set -euo pipefail
source "$1/lib/common.sh"
source "$1/lib/runtime_paths.sh"
MARKER="$2"
deploy_runtime_canonical_parent_for_leaf() { printf '%s\\n' /runtime-parent; }
deploy_prepare_runtime_parent_directory() { return 0; }
deploy_assert_runtime_leaf_directory() { return 0; }
deploy_as_root() { "$@"; }
chown() { : > "${MARKER}"; return 91; }
chmod() { : > "${MARKER}"; return 92; }
deploy_prepare_runtime_leaf_directory "$3" northstar
[ ! -e "${MARKER}" ]
"""

    result = _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        _bash_path(mutation_marker),
        _bash_path(existing_leaf),
        check=False,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert not mutation_marker.exists()


def test_privileged_layout_paths_validate_every_ancestor_and_fail_closed(
    tmp_path: Path,
) -> None:
    """A writable intermediate or symlink must stop creation below it."""

    command = r"""
set -euo pipefail
source "$1/lib/common.sh"
source "$1/lib/privileged_paths.sh"
ROOT="$2"
SAFE_PARENT="${ROOT}/safe-parent"
SAFE_TARGET="${SAFE_PARENT}/child"
INSECURE_PARENT="${ROOT}/insecure-parent"
SYMLINK_PARENT="${ROOT}/symlink-parent"

mkdir -p "${SAFE_PARENT}" "${INSECURE_PARENT}"

deploy_as_root() {
  local command_name="$1"
  local format
  local path
  shift
  case "${command_name}" in
    test)
      # Model an existing directory symlink at this intermediate component
      # without mutating an ancestor during this fail-closed contract test.
      case "${1:-}:${2:-}" in
        -e:"${SYMLINK_PARENT}"|-d:"${SYMLINK_PARENT}"|-L:"${SYMLINK_PARENT}")
          return 0
          ;;
      esac
      test "$@"
      ;;
    stat)
      format="$2"
      path=""
      for path in "$@"; do :; done
      case "${format}" in
        %u:%a)
          case "${path}" in
            "${INSECURE_PARENT}"|"${INSECURE_PARENT}"/*) printf '0:777\n' ;;
            *) printf '0:755\n' ;;
          esac
          ;;
        %u:%G:%a)
          case "${path}" in
            "${INSECURE_PARENT}"|"${INSECURE_PARENT}"/*) printf '0:root:777\n' ;;
            *) printf '0:root:755\n' ;;
          esac
          ;;
        *) return 99 ;;
      esac
      ;;
    chown|chmod)
      # Ownership is modeled by stat above; this contract does not alter the
      # local test process ownership.
      return 0
      ;;
    *)
      "${command_name}" "$@"
      ;;
  esac
}

deploy_prepare_root_controlled_directory "${SAFE_TARGET}" root 755
[ -d "${SAFE_TARGET}" ]

if deploy_prepare_root_controlled_directory "${INSECURE_PARENT}/child" root 755; then
  exit 40
fi
[ ! -e "${INSECURE_PARENT}/child" ]

if deploy_prepare_root_controlled_directory "${SYMLINK_PARENT}/child" root 755; then
  exit 41
fi
[ ! -e "${SYMLINK_PARENT}/child" ]
"""

    result = _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        _bash_path(tmp_path),
        check=False,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr


def test_privileged_layout_roots_use_the_full_chain_helper_before_installer_writes() -> None:
    privileged_paths = (DEPLOY_DIR / "lib" / "privileged_paths.sh").read_text(
        encoding="utf-8"
    )
    layout = (DEPLOY_DIR / "lib" / "layout.sh").read_text(encoding="utf-8")
    release_environment = (DEPLOY_DIR / "lib" / "release_environment.sh").read_text(
        encoding="utf-8"
    )
    runtime_install = (DEPLOY_DIR / "install-runtime.sh").read_text(encoding="utf-8")
    release_install = (DEPLOY_DIR / "install-release.sh").read_text(encoding="utf-8")

    assert "deploy_assert_root_controlled_directory_chain()" in privileged_paths
    assert "deploy_assert_root_controlled_directory /" in privileged_paths
    assert 'deploy_as_root test -L "${directory_path}"' in privileged_paths
    assert "privileged directory must not be group/other writable" in privileged_paths
    assert "deploy_as_root mkdir -m" in privileged_paths
    assert "mkdir -p" not in privileged_paths
    assert "Existing privileged paths are validation-only" in privileged_paths

    assert "deploy_prepare_fixed_privileged_layout()" in layout
    for expected_root in (
        'deploy_prepare_root_controlled_directory "${APP_ROOT}" root 755',
        'deploy_prepare_root_controlled_directory "${RELEASES_DIR}" root 755',
        'deploy_prepare_root_controlled_directory "${CONFIG_DIR}" "${service_group}" 750',
        'deploy_prepare_root_controlled_directory "${ENV_RELEASES_DIR}" "${service_group}" 750',
        'deploy_prepare_root_controlled_directory "${SYSTEMD_UNIT_DIR}" root 755',
        'deploy_prepare_root_controlled_directory "${STATE_DIR}" "${service_group}" 750',
        'deploy_prepare_root_controlled_directory "${CACHE_DIR}" "${service_group}" 750',
        'deploy_prepare_root_controlled_directory "${LOG_DIR}" "${service_group}" 750',
        'deploy_prepare_root_controlled_directory "${DEPLOY_STATE_DIR}" root 700',
        'deploy_prepare_root_controlled_directory "${UV_BOOTSTRAP_CACHE_DIR}" root 700',
        'deploy_prepare_root_controlled_directory "${UV_PYTHON_INSTALL_DIR}" root 755',
    ):
        assert expected_root in layout

    assert 'source "${SCRIPT_DIR}/lib/privileged_paths.sh"' in runtime_install
    assert 'source "${SCRIPT_DIR}/lib/privileged_paths.sh"' in release_install
    assert "--no-create-home" in runtime_install
    assert 'if ! deploy_prepare_fixed_privileged_layout "${SERVICE_USER}"; then' in runtime_install
    assert 'if ! deploy_prepare_fixed_privileged_layout "${SERVICE_USER}"; then' in release_install
    assert release_install.index(
        'if ! deploy_prepare_fixed_privileged_layout "${SERVICE_USER}"; then'
    ) < release_install.index("ACTIVE_ENV_SNAPSHOT=")

    assert "privileged_paths.sh" in release_environment
    assert (
        'deploy_assert_root_controlled_directory_chain "${directory_path}"'
        in release_environment
    )


def test_private_ntfy_deploy_defaults_closed_and_uses_nonsecret_whitelist(
    tmp_path: Path,
) -> None:
    """私有 ntfy 必须默认关闭，且 deploy.env 不能承载身份或令牌。"""

    example_config = ROOT_DIR / "deploy.env.example"
    example_content = example_config.read_text(encoding="utf-8")
    assert "NTFY_DEPLOY_ENABLED=0" in example_content

    default_values = load_inventory(example_config).values
    assert default_values["NTFY_DEPLOY_ENABLED"] == "0"
    assert default_values["NTFY_PUBLIC_HOST"] == ""
    assert default_values["NTFY_ACME_EMAIL"] == ""
    assert default_values["NTFY_IMAGE"] == "binwiederhier/ntfy:v2.27.0"
    assert default_values["NTFY_CADDY_IMAGE"] == "caddy:2.10.2-alpine"
    assert default_values["NTFY_CONFIG_DIR"] == "/etc/northstar-ntfy"
    assert default_values["NTFY_DATA_DIR"] == "/var/lib/northstar-ntfy"
    assert default_values["NTFY_CACHE_DURATION"] == "24h"

    enabled_config = tmp_path / "deploy.env"
    enabled_config.write_text(
        "\n".join(
            [
                "DEPLOY_HOST=ntfy.example.test",
                "NTFY_DEPLOY_ENABLED=1",
                "NTFY_PUBLIC_HOST=ntfy.example.test",
                "NTFY_ACME_EMAIL=ops@example.test",
                "NTFY_IMAGE=binwiederhier/ntfy:v2.28.0",
                "NTFY_CADDY_IMAGE=caddy:2.10.3-alpine",
                "NTFY_CONFIG_DIR=/etc/northstar-ntfy",
                "NTFY_DATA_DIR=/var/lib/northstar-ntfy",
                "NTFY_CACHE_DURATION=12h",
            ]
        ),
        encoding="utf-8",
    )
    enabled_values = load_inventory(enabled_config).values
    assert [enabled_values[key] for key in NTFY_DEPLOY_KEYS] == [
        "1",
        "ntfy.example.test",
        "ops@example.test",
        "binwiederhier/ntfy:v2.28.0",
        "caddy:2.10.3-alpine",
        "/etc/northstar-ntfy",
        "/var/lib/northstar-ntfy",
        "12h",
    ]

    secret_in_deploy_config = tmp_path / "deploy-with-secret.env"
    secret_in_deploy_config.write_text(
        "\n".join(
            [
                "DEPLOY_HOST=ntfy.example.test",
                "NTFY_DEPLOY_ENABLED=1",
                "NTFY_ADMIN_PASSWORD=must-not-be-accepted-here",  # secret-scan: allow; reason: disposable test fixture
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(InventoryError, match="不支持或机密字段"):
        load_inventory(secret_in_deploy_config)

    config_loader = (DEPLOY_DIR / "inventory.py").read_text(encoding="utf-8")
    for key in NTFY_DEPLOY_KEYS:
        assert key in config_loader
    for secret_key in (
        "NTFY_ADMIN_USERNAME",
        "NTFY_ADMIN_PASSWORD",
        "NTFY_READER_USERNAME",
        "NTFY_READER_PASSWORD",
    ):
        assert secret_key not in config_loader


def test_signed_release_gate_rejects_ntfy_mutation_and_never_transports_bootstrap_secrets() -> None:
    """App release signing cannot authorize ntfy configuration or credentials."""

    deploy_control = (DEPLOY_DIR / "deploy.py").read_text(encoding="utf-8")

    assert "NTFY_DEPLOY_ENABLED=1 is not supported by the signed root release gate" in deploy_control
    assert '"ntfy_deploy_enabled": "0"' in deploy_control
    assert "separate root-operated ntfy workflow" in deploy_control
    for key in NTFY_DEPLOY_KEYS[1:]:
        assert key not in deploy_control
    for obsolete_bootstrap_flag in (
        "UPLOAD_NTFY_BOOTSTRAP",
        "NTFY_BOOTSTRAP_PATH",
        "--upload-ntfy-bootstrap",
        "--confirm-ntfy-bootstrap",
    ):
        assert obsolete_bootstrap_flag not in deploy_control
    for secret_key in (
        "NTFY_ADMIN_USERNAME",
        "NTFY_ADMIN_PASSWORD",
        "NTFY_READER_USERNAME",
        "NTFY_READER_PASSWORD",
    ):
        assert secret_key not in deploy_control


def test_private_ntfy_templates_keep_ntfy_behind_caddy_with_strict_server_policy() -> None:
    """ntfy 只可经 Caddy 的 80/443 端口访问，服务端默认拒绝访问。"""

    compose_template = NTFY_DIR / "compose.yaml.in"
    caddy_template = NTFY_DIR / "Caddyfile.in"
    server_template = NTFY_DIR / "server.yml.in"
    for template in (compose_template, caddy_template, server_template):
        assert template.is_file(), f"缺少 ntfy 部署模板：{template.relative_to(ROOT_DIR)}"

    compose = yaml.safe_load(compose_template.read_text(encoding="utf-8"))
    assert isinstance(compose, dict)
    services = compose.get("services")
    assert isinstance(services, dict)
    assert {"caddy", "ntfy"}.issubset(services)
    caddy = services["caddy"]
    ntfy = services["ntfy"]
    assert isinstance(caddy, dict)
    assert isinstance(ntfy, dict)
    caddy_ports = caddy.get("ports")
    assert isinstance(caddy_ports, list)
    assert {str(port).split("/", maxsplit=1)[0] for port in caddy_ports} == {
        "80:80",
        "443:443",
    }
    assert "ports" not in ntfy
    for service_name, service in services.items():
        if service_name != "caddy":
            assert not service.get("ports"), f"仅 Caddy 可映射主机端口：{service_name}"

    caddy_content = caddy_template.read_text(encoding="utf-8")
    assert "reverse_proxy" in caddy_content
    assert re.search(r"reverse_proxy\s+ntfy(?::\d+)?", caddy_content)

    server_config = yaml.safe_load(server_template.read_text(encoding="utf-8"))
    assert isinstance(server_config, dict)
    assert server_config.get("auth-default-access") == "deny-all"
    assert server_config.get("behind-proxy") is True
    assert server_config.get("enable-signup") is False
    for key in ("auth-file", "cache-file"):
        assert server_config.get(key), f"server.yml.in 必须设置持久化 {key}"
    # 交易告警不允许携带附件：不能创建附件目录，也不能放开附件配额。
    assert server_config.get("attachment-cache-dir", "") == ""
    for limit_key in ("attachment-file-size-limit", "attachment-total-size-limit"):
        configured_limit = server_config.get(limit_key)
        if configured_limit is not None:
            assert configured_limit in {0, "0", "0B"}, f"server.yml.in 不得放开 {limit_key}"


def test_private_ntfy_persistent_state_is_root_owned_and_not_part_of_release() -> None:
    """顶层持久目录由 root 管理，仅 ntfy 专用子目录可由服务账户写入。"""

    ntfy_provision = (NTFY_DIR / "provision-ntfy.sh").read_text(encoding="utf-8")
    all_ntfy_sources = "\n".join(
        source.read_text(encoding="utf-8") for source in NTFY_DIR.iterdir() if source.is_file()
    )

    assert "NTFY_CONFIG_DIR" in ntfy_provision
    assert "NTFY_DATA_DIR" in ntfy_provision
    assert "ntfy_require_canonical_directories" in ntfy_provision
    assert "ntfy_ensure_managed_directory()" in ntfy_provision
    assert "拒绝修改未受管的 ntfy 目录" in ntfy_provision
    assert '"${NTFY_DATA_DIR}" root root 0 0 750' in ntfy_provision
    assert '"${NTFY_DATA_DIR}/ntfy" \\' in ntfy_provision
    assert '"${NTFY_SERVICE_UID}" \\' in ntfy_provision
    assert '"${NTFY_SERVICE_GID}" \\' in ntfy_provision
    release_path_result = _run_bash(
        "-c",
        """
set -euo pipefail
source "$1/lib/common.sh"
source "$1/ntfy/lib.sh"
ntfy_normalize_path NTFY_DATA_DIR /srv/northstar/northstar-quant/releases/ntfy data
""",
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        check=False,
        capture_output=True,
    )
    assert release_path_result.returncode != 0
    # The fixed P6 layout rejects every data root outside /var/lib before it
    # needs to inspect legacy release-path components.
    assert "/var/lib" in release_path_result.stderr
    assert "docker compose down -v" not in all_ntfy_sources
    assert "docker-compose down -v" not in all_ntfy_sources
    assert 'rm -rf "${NTFY_CONFIG_DIR}' not in all_ntfy_sources
    assert 'rm -rf "${NTFY_DATA_DIR}' not in all_ntfy_sources


def test_private_ntfy_refuses_to_repair_unmanaged_existing_directory(tmp_path: Path) -> None:
    """An extant host path must fail closed before any chown or chmod."""

    ntfy_provision = (NTFY_DIR / "provision-ntfy.sh").read_text(encoding="utf-8")
    directory_matcher = re.search(
        r"^ntfy_directory_matches_contract\(\) \{.*?^\}\n\n"
        r"(?=ntfy_ensure_managed_directory\(\))",
        ntfy_provision,
        re.MULTILINE | re.DOTALL,
    )
    directory_ensurer = re.search(
        r"^ntfy_ensure_managed_directory\(\) \{.*?^\}\n\n"
        r"(?=ntfy_ensure_directories\(\))",
        ntfy_provision,
        re.MULTILINE | re.DOTALL,
    )
    assert directory_matcher
    assert directory_ensurer

    unmanaged_directory = tmp_path / "unmanaged-directory"
    unmanaged_directory.mkdir()
    mutation_marker = tmp_path / "ownership-or-mode-was-mutated"
    command = (
        """
set -euo pipefail
MUTATION_MARKER="$1"
deploy_fail() {
  printf '%s\\n' "拒绝修改未受管目录" >&2
  exit 1
}
chown() {
  printf '%s\\n' chown > "${MUTATION_MARKER}"
  return 97
}
chmod() {
  printf '%s\\n' chmod > "${MUTATION_MARKER}"
  return 97
}
"""
        + directory_matcher.group(0)
        + "\n"
        + directory_ensurer.group(0)
        + """
ntfy_ensure_managed_directory "$2" root root 0 0 750
"""
    )

    result = _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(mutation_marker),
        _bash_path(unmanaged_directory),
        check=False,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "未受管" in result.stderr
    assert not mutation_marker.exists()


@pytest.mark.parametrize(
    ("account_record", "group_names", "expected_success"),
    (
        (
            "northstar-ntfy:x:4242:4242::/nonexistent:/usr/sbin/nologin",
            "northstar-ntfy",
            True,
        ),
        (
            "northstar-ntfy:x:0:0::/nonexistent:/usr/sbin/nologin",
            "northstar-ntfy",
            False,
        ),
        (
            "northstar-ntfy:x:4242:4242::/nonexistent:/bin/bash",
            "northstar-ntfy",
            False,
        ),
        (
            "northstar-ntfy:x:4242:4242::/nonexistent:/usr/sbin/nologin",
            "northstar-ntfy docker",
            False,
        ),
    ),
)
def test_private_ntfy_service_identity_must_remain_dedicated(
    account_record: str,
    group_names: str,
    expected_success: bool,
) -> None:
    """A pre-existing service account cannot carry root-like identity or groups."""

    ntfy_provision = (NTFY_DIR / "provision-ntfy.sh").read_text(encoding="utf-8")
    identity_check = re.search(
        r"^ntfy_assert_canonical_service_account\(\) \{.*?^\}\n\n"
        r"(?=ntfy_directory_matches_contract\(\))",
        ntfy_provision,
        re.MULTILINE | re.DOTALL,
    )
    identity_ensure = re.search(
        r"^ntfy_ensure_service_account\(\) \{.*?^\}\n\n"
        r"(?=ntfy_assert_canonical_service_account\(\))",
        ntfy_provision,
        re.MULTILINE | re.DOTALL,
    )
    assert identity_check
    assert identity_ensure
    assert "if ! ntfy_assert_canonical_service_account; then" in identity_ensure.group(0)

    command = (
        """
set -euo pipefail
NTFY_SERVICE_ACCOUNT=northstar-ntfy
ACCOUNT_RECORD="$1"
GROUP_NAMES="$2"
getent() {
  case "${1:-}:${2:-}" in
    passwd:northstar-ntfy|passwd:) # secret-scan: allow; reason: disposable test fixture
      printf '%s\\n' "${ACCOUNT_RECORD}"
      ;;
    group:northstar-ntfy)
      printf '%s\\n' 'northstar-ntfy:x:4242:'
      ;;
    *)
      return 95
      ;;
  esac
}
id() {
  case "${1:-}:${2:-}" in
    -gn:northstar-ntfy)
      printf '%s\\n' northstar-ntfy
      ;;
    -Gn:northstar-ntfy)
      printf '%s\\n' "${GROUP_NAMES}"
      ;;
    *)
      return 96
      ;;
  esac
}
"""
        + identity_check.group(0)
        + """
ntfy_assert_canonical_service_account
"""
    )

    result = _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        account_record,
        group_names,
        check=False,
        capture_output=True,
    )

    assert (result.returncode == 0) is expected_success, result.stderr


@pytest.mark.parametrize(
    ("ntfy_config_dir", "ntfy_data_dir", "runtime_storage_dir", "expected_success"),
    (
        ("/etc/northstar", "/var/lib/northstar-ntfy", "/var/lib/northstar/storage", False),
        ("/etc/northstar-ntfy", "/var/lib/northstar", "/var/lib/northstar/storage", False),
        ("/etc/ssh", "/var/lib/northstar-ntfy", "/var/lib/northstar/storage", False),
        (
            "/etc/northstar-ntfy",
            "/var/lib/postgresql",
            "/var/lib/northstar/storage",
            False,
        ),
        (
            "/etc/northstar-ntfy",
            "/var/lib/northstar/storage",
            "/var/lib/northstar/storage",
            False,
        ),
        (
            "/etc/northstar-ntfy",
            "/mnt/northstar-quant/storage/ntfy",
            "/mnt/northstar-quant/storage",
            False,
        ),
        (
            "/etc/northstar-ntfy",
            "/var/lib/northstar-ntfy",
            "/var/lib/northstar/storage",
            True,
        ),
    ),
)
def test_private_ntfy_path_validator_separates_northstar_boundaries(
    ntfy_config_dir: str,
    ntfy_data_dir: str,
    runtime_storage_dir: str,
    expected_success: bool,
) -> None:
    command = """
set -euo pipefail
source "$1/lib/common.sh"
source "$1/ntfy/lib.sh"
NTFY_DEPLOY_ENABLED=1
NTFY_PUBLIC_HOST=ntfy.example.test
NTFY_ACME_EMAIL=ops@example.test
NTFY_CONFIG_DIR="$2"
NTFY_DATA_DIR="$3"
RUNTIME_STORAGE_DIR="$4"
RUNTIME_DOWNLOADS_DIR=/var/lib/northstar/downloads
RUNTIME_REPORTS_DIR=/var/lib/northstar/reports
RUNTIME_LOG_DIR=/var/log/northstar/app
RUNTIME_CACHE_DIR=/var/cache/northstar/runtime
RUNTIME_MATPLOTLIB_DIR=/var/cache/northstar/matplotlib
ntfy_validate_deployment_config
"""
    result = _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        ntfy_config_dir,
        ntfy_data_dir,
        runtime_storage_dir,
        check=False,
        capture_output=True,
    )

    assert (result.returncode == 0) is expected_success, result.stderr
    if not expected_success:
        assert "NTFY_" in result.stderr


def test_private_ntfy_normal_release_never_rewrites_existing_identity() -> None:
    """未显式上传 bootstrap 时，普通发布只能验证既有身份，不能重建它。"""

    ntfy_provision = (NTFY_DIR / "provision-ntfy.sh").read_text(encoding="utf-8")

    assert re.search(
        r'if \[ "\$\{UPLOAD_NTFY_BOOTSTRAP\}" = "0" \]; then\s+'
        r"ntfy_assert_existing_server_config\s+fi",
        ntfy_provision,
    )
    bootstrap_render = re.search(
        r'if \[ "\$\{UPLOAD_NTFY_BOOTSTRAP\}" = "1" \]; then\s+'
        r"SERVER_TMP=.*?\s+ntfy_render_server_config\s+fi",
        ntfy_provision,
    )
    assert bootstrap_render, "仅显式 UPLOAD_NTFY_BOOTSTRAP=1 可生成 server.yml"
    render_calls = re.findall(r"^\s+ntfy_render_server_config\s*$", ntfy_provision, re.MULTILINE)
    assert len(render_calls) == 1
    assert "docker compose down -v" not in ntfy_provision
    assert "docker-compose down -v" not in ntfy_provision


def _run_existing_ntfy_server_config_validator(
    tmp_path: Path,
    server_config: str,
) -> subprocess.CompletedProcess[str]:
    """在本地 Bash 中运行既有 server.yml 校验器，不依赖 root、Docker 或真实身份。"""

    provision_source = (NTFY_DIR / "provision-ntfy.sh").read_text(encoding="utf-8")
    function_match = re.search(
        r"^ntfy_assert_existing_server_config\(\) \{.*?^\}\n\n(?=ntfy_read_bootstrap_file\(\))",
        provision_source,
        re.MULTILINE | re.DOTALL,
    )
    assert function_match, "无法从 ntfy 部署脚本提取既有 server.yml 校验器"

    server_file = tmp_path / "server.yml"
    # 生产服务器上的 YAML 使用 LF，避免行尾差异掩盖校验器本身的策略行为。
    server_file.write_bytes(server_config.encode("utf-8"))
    validator_script = tmp_path / "run-validator.sh"
    validator_script.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
deploy_fail() {
  printf '%s\\n' 'server.yml 校验拒绝了不安全配置' >&2
  return 1
}
stat() {
  case "$2" in
    '%U') printf '%s\\n' root ;;
    '%G') printf '%s\\n' northstar-ntfy ;;
    '%a') printf '%s\\n' 640 ;;
    *) return 1 ;;
  esac
}
"""
        + function_match.group(0)
        + """
SERVER_FILE="$1"
NTFY_PUBLIC_HOST=ntfy.example.test
NTFY_CACHE_DURATION=24h
NORTHSTAR_NTFY_TOPIC=northstar_alerts
NORTHSTAR_NTFY_TOKEN=test-token # secret-scan: allow; reason: disposable test fixture
NTFY_SERVICE_ACCOUNT=northstar-ntfy
ntfy_assert_existing_server_config
""",
        encoding="utf-8",
    )

    return _run_bash(
        _bash_path(validator_script),
        _bash_path(server_file),
        check=False,
        capture_output=True,
    )


def test_private_ntfy_existing_server_config_validator_fails_closed_for_unmanaged_policy(
    tmp_path: Path,
) -> None:
    """普通发布必须拒绝可能扩大权限、外发或附件能力的既有 server.yml。"""

    baseline_config = """\
base-url: "https://ntfy.example.test"
listen-http: ":80"
cache-file: "/var/lib/ntfy/cache.db"
cache-duration: "24h"
auth-file: "/var/lib/ntfy/auth.db"
auth-default-access: "deny-all"
behind-proxy: true
enable-login: true
enable-signup: false
enable-metrics: false
log-level: "info"
log-format: "json"
auth-users:
  - "admin-user:$2b$12$adminhash:admin"
  - "reader-user:$2b$12$readerhash:user"
  - "northstar-publisher:$2b$12$publisherhash:user"
auth-access:
  - "reader-user:northstar_alerts:read-only"
  - "northstar-publisher:northstar_alerts:write-only"
auth-tokens:
  - "northstar-publisher:test-token:Northstar Quant publisher"
"""
    baseline_result = _run_existing_ntfy_server_config_validator(tmp_path, baseline_config)
    assert baseline_result.returncode == 0, baseline_result.stderr

    extra_user_config = baseline_config.replace(
        "auth-access:",
        '  - "unexpected-admin:$2b$12$unexpectedhash:admin"\nauth-access:',
    )
    extra_user_result = _run_existing_ntfy_server_config_validator(tmp_path, extra_user_config)
    assert extra_user_result.returncode != 0

    for prohibited_key, value in (
        ("upstream-base-url", '"https://relay.example.test"'),
        ("attachment-cache-dir", '""'),
        ("smtp-server-list", '"smtp.example.test"'),
        ("firebase-key-file", '"/run/ntfy/firebase.json"'),
    ):
        unsafe_result = _run_existing_ntfy_server_config_validator(
            tmp_path,
            f"{baseline_config}{prohibited_key}: {value}\n",
        )
        assert unsafe_result.returncode != 0, prohibited_key


def test_private_ntfy_bootstrap_stays_out_of_artifacts_and_logs() -> None:
    """一次性身份文件不可入库、不可进入应用制品，也不可被部署日志回显。"""

    gitignore = (ROOT_DIR / ".gitignore").read_text(encoding="utf-8")
    assert "ntfy.bootstrap.env" in gitignore
    assert "!ntfy.bootstrap.env.example" in gitignore
    assert (ROOT_DIR / "ntfy.bootstrap.env.example").is_file()

    builder = (DEPLOY_DIR / "package.py").read_text(encoding="utf-8")
    # 制品白名单只包含运行源码目录，根目录的一次性身份文件没有进入路径。
    assert "ntfy.bootstrap" not in builder

    deployment_sources = [
        DEPLOY_DIR / "deploy.py",
        DEPLOY_DIR / "provision.sh",
        NTFY_DIR / "provision-ntfy.sh",
    ]
    for source in deployment_sources:
        content = source.read_text(encoding="utf-8")
        assert "set -x" not in content
        for line in content.splitlines():
            is_output = "deploy_log" in line or re.search(r"\b(?:echo|printf)\b", line)
            if is_output:
                assert "PASSWORD" not in line
                assert "TOKEN" not in line


def test_signed_release_gate_never_uses_deployment_user_owned_remote_staging() -> None:
    """Release bytes go only to the fixed root gate stdin protocol."""

    deploy_control = (DEPLOY_DIR / "deploy.py").read_text(encoding="utf-8")

    assert "write_submission(process.stdin, submission)" in deploy_control
    assert 'f"sudo -n {ROOT_RUNNER_PATH} submit"' in deploy_control
    assert "environment_signature=environment_signature" in deploy_control
    for forbidden_deployment_user_staging_reference in (
        "work_dir",
        "active_env",
        "remote_paths",
        "/tmp",
        "UPLOAD_NTFY_BOOTSTRAP",
        "NTFY_BOOTSTRAP_PATH",
    ):
        assert forbidden_deployment_user_staging_reference not in deploy_control


def test_private_dashboard_deploy_is_explicit_opt_in_and_keeps_main_service_mode() -> None:
    """Dashboard 是独立观察服务，默认关闭，不能替换 health/scheduler 主服务。"""

    example_config = ROOT_DIR / "deploy.env.example"
    example_content = example_config.read_text(encoding="utf-8")
    assert "DASHBOARD_DEPLOY_ENABLED=0" in example_content

    assert load_inventory(example_config).values["DASHBOARD_DEPLOY_ENABLED"] == "0"

    deploy_control = (DEPLOY_DIR / "deploy.py").read_text(encoding="utf-8")
    provision_script = (DEPLOY_DIR / "provision.sh").read_text(encoding="utf-8")
    release_script = (DEPLOY_DIR / "install-release.sh").read_text(encoding="utf-8")
    config_loader = (DEPLOY_DIR / "inventory.py").read_text(encoding="utf-8")
    dashboard_template = (SYSTEMD_DIR / "dashboard.service.in").read_text(encoding="utf-8")

    for source in (provision_script, release_script, config_loader):
        assert "DASHBOARD_DEPLOY_ENABLED" in source
    assert "DASHBOARD_DEPLOY_ENABLED" not in deploy_control
    assert '"dashboard_deploy_enabled": "1" if inventory.dashboard_deploy_enabled else "0"' in (
        deploy_control
    )
    assert 'DASHBOARD_SERVICE_NAME="${SYSTEMD_SERVICE_NAME}-dashboard"' in release_script
    assert 'DASHBOARD_UNIT_FILE="${SYSTEMD_UNIT_DIR}/${DASHBOARD_SERVICE_NAME}.service"' in (
        release_script
    )
    assert 'case "${SERVICE_MODE}" in\n  health|scheduler)' in release_script
    assert '"health", "scheduler"' in (DEPLOY_DIR / "inventory.py").read_text(encoding="utf-8")

    assert "NORTHSTAR_DASHBOARD_HOST" not in dashboard_template
    assert "0.0.0.0" not in dashboard_template
    assert "ExecStart=@CURRENT_LINK@/.venv/bin/northstar dashboard run" in dashboard_template
    assert "EnvironmentFile=@CURRENT_LINK@/.env" in dashboard_template
    assert "@ENV_FILE@" not in dashboard_template
    assert (
        "ReadWritePaths=@DASHBOARD_HOME_DIR@ @RUNTIME_CACHE_DIR@ "
        "@RUNTIME_MATPLOTLIB_DIR@ @RUNTIME_LOG_DIR@ @UV_CACHE_DIR@"
    ) in dashboard_template

    activate_main_service = release_script.index("if ! activate_service; then")
    configure_dashboard = release_script.index("if ! configure_dashboard_systemd_unit; then")
    assert activate_main_service < configure_dashboard
    assert "fail_closed_dashboard_systemd_unit()" in release_script
    assert "prepare_dashboard_systemd_transition()" in release_script
    assert 'render_systemd_snapshot "${STAGE_DIR}" "dashboard.service.in"' in release_script
    assert 'render_systemd_snapshot "${RELEASE_DIR}" "dashboard.service.in"' not in release_script
    assert 'DASHBOARD_DEPLOY_STATUS="disabled_after_failure"' in release_script
    assert (
        "私网 Dashboard 配置或启动失败，已关闭该观察服务；"
        "主 health/scheduler 服务保持本次发布版本。"
    ) in release_script
    assert 'printf "dashboard=%s\\n" "${DASHBOARD_DEPLOY_STATUS}"' in release_script
    assert 'print(f"mode={inventory.service_mode}")' in deploy_control

    disable_dashboard_start = release_script.index("disable_dashboard_systemd_unit() {")
    disable_dashboard_end = release_script.index(
        "\n}\n\nconfigure_dashboard_systemd_unit()", disable_dashboard_start
    )
    disable_dashboard = release_script[disable_dashboard_start:disable_dashboard_end]
    assert "disable_dashboard_and_verify" in disable_dashboard
    assert 'systemctl disable --now "${DASHBOARD_SERVICE_NAME}.service"' in release_script
    assert "dashboard_service_enablement_state()" in release_script
    assert 'systemctl is-enabled "${DASHBOARD_SERVICE_NAME}.service"' in release_script
    assert '"${enablement_state}" != "disabled"' in release_script


def test_dashboard_disable_requires_exact_disabled_enablement_before_unit_removal(
    tmp_path: Path,
) -> None:
    """Dashboard 的停用与失败关闭不得把未知 enablement 当成安全状态。"""

    release_script = (DEPLOY_DIR / "install-release.sh").read_text(encoding="utf-8")
    helpers_start = release_script.index("dashboard_unit_file_exists() {")
    helpers_end = release_script.index("\n}\n\nconfigure_dashboard_systemd_unit()", helpers_start)
    dashboard_disable_helpers = release_script[helpers_start : helpers_end + 2]
    fail_closed_start = release_script.index("fail_closed_dashboard_systemd_unit() {")
    fail_closed_end = release_script.index("\n}\n\nswitch_current_release()", fail_closed_start)
    fail_closed = release_script[fail_closed_start : fail_closed_end + 2]
    command = "\n".join(
        (
            "set -euo pipefail",
            'DASHBOARD_SERVICE_NAME="northstar-quant-dashboard"',
            'DASHBOARD_UNIT_FILE="$1"',
            'EVENT_LOG="$(mktemp)"',
            "trap 'rm -f -- \"$EVENT_LOG\"' EXIT",
            'deploy_as_root() { "$@"; }',
            "systemctl() {",
            '  printf "%s\\n" "$1" >> "$EVENT_LOG"',
            '  case "$1" in',
            "    disable)",
            '      [ "${DISABLE_MODE}" != "error" ] || return 76',
            '      ACTIVE_STATE="inactive"',
            '      if [ "${DISABLE_MODE}" = "normal" ]; then ENABLEMENT="disabled"; fi',
            "      return 0",
            "      ;;",
            "    show)",
            '      [ "${SHOW_MODE}" != "error" ] || return 77',
            '      printf "%s\\n" "${ACTIVE_STATE}"',
            "      return 0",
            "      ;;",
            "    is-enabled)",
            '      printf "%s\\n" "${ENABLEMENT}"',
            '      [ "${ENABLEMENT}" = "enabled" ] && return 0',
            '      [ "${ENABLEMENT}" = "disabled" ] && return 1',
            "      return 1",
            "      ;;",
            "    daemon-reload) return 0 ;;",
            "  esac",
            "  return 97",
            "}",
            dashboard_disable_helpers,
            fail_closed,
            "assert_events() {",
            '  [ "$(tr "\\n" " " < "$EVENT_LOG")" = "$1" ]',
            "}",
            "# A managed dashboard unit is removed only after exact disabled confirmation.",
            'printf "[Unit]\\n" > "$DASHBOARD_UNIT_FILE"',
            'DISABLE_MODE="normal"',
            'SHOW_MODE="normal"',
            'ACTIVE_STATE="active"',
            'ENABLEMENT="enabled"',
            ': > "$EVENT_LOG"',
            "disable_dashboard_systemd_unit",
            '[ ! -e "$DASHBOARD_UNIT_FILE" ]',
            'assert_events "disable show is-enabled daemon-reload show is-enabled "',
            "# A false-success disable that leaves enablement enabled must preserve the unit.",
            'printf "[Unit]\\n" > "$DASHBOARD_UNIT_FILE"',
            'DISABLE_MODE="sticky"',
            'SHOW_MODE="normal"',
            'ACTIVE_STATE="active"',
            'ENABLEMENT="enabled"',
            ': > "$EVENT_LOG"',
            "if disable_dashboard_systemd_unit; then exit 51; fi",
            '[ -e "$DASHBOARD_UNIT_FILE" ]',
            'assert_events "disable show is-enabled "',
            "# Unknown enablement and systemctl errors are likewise fail-closed before removal.",
            'DISABLE_MODE="unknown"',
            'SHOW_MODE="normal"',
            'ACTIVE_STATE="active"',
            'ENABLEMENT="static"',
            ': > "$EVENT_LOG"',
            "if fail_closed_dashboard_systemd_unit; then exit 52; fi",
            '[ -e "$DASHBOARD_UNIT_FILE" ]',
            'assert_events "disable show is-enabled "',
            'DISABLE_MODE="error"',
            'SHOW_MODE="normal"',
            'ACTIVE_STATE="active"',
            'ENABLEMENT="enabled"',
            ': > "$EVENT_LOG"',
            "if fail_closed_dashboard_systemd_unit; then exit 53; fi",
            '[ -e "$DASHBOARD_UNIT_FILE" ]',
            'assert_events "disable "',
            "# A missing unit with a stale enabled wants-link is not safe to ignore.",
            'rm -f -- "$DASHBOARD_UNIT_FILE"',
            'SHOW_MODE="normal"',
            'ACTIVE_STATE="inactive"',
            'ENABLEMENT="enabled"',
            ': > "$EVENT_LOG"',
            "if disable_dashboard_systemd_unit; then exit 54; fi",
            'assert_events "show is-enabled "',
            "# First deployment may have no dashboard unit; only exact absent/disabled is safe.",
            'SHOW_MODE="normal"',
            'ACTIVE_STATE="inactive"',
            'ENABLEMENT="not-found"',
            ': > "$EVENT_LOG"',
            "disable_dashboard_systemd_unit",
            'assert_events "show is-enabled daemon-reload show is-enabled "',
        )
    )

    result = _run_bash(
        "-c",
        command,
        "dashboard-disable-enablement-contract",
        _bash_path(tmp_path / "northstar-quant-dashboard.service"),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_private_ntfy_does_not_grant_docker_access_to_northstar_and_fails_closed() -> None:
    """应用服务用户不得接触 Docker；ntfy 缺 Docker 时必须在调用前失败。"""

    deploy_sources = "\n".join(
        source.read_text(encoding="utf-8")
        for source in DEPLOY_DIR.rglob("*")
        if source.is_file() and source.suffix in {".sh", ".in", ".yaml", ".yml"}
    )
    ntfy_provision = (NTFY_DIR / "provision-ntfy.sh").read_text(encoding="utf-8")

    assert "/var/run/docker.sock" not in deploy_sources
    assert "docker.sock" not in deploy_sources
    assert not re.search(
        r"(?:usermod|adduser)[^\n]*\bdocker\b[^\n]*(?:SERVICE_USER|northstar)", deploy_sources
    )
    assert not re.search(
        r"(?:usermod|adduser)[^\n]*(?:SERVICE_USER|northstar)[^\n]*\bdocker\b", deploy_sources
    )

    docker_preflight = re.search(
        r"(?:deploy_need_cmd\s+docker|command\s+-v\s+docker)", ntfy_provision
    )
    assert docker_preflight, "私有 ntfy 部署必须显式检查 Docker"
    first_docker_compose = re.search(r"\bdocker\s+compose\b", ntfy_provision)
    assert first_docker_compose, "私有 ntfy 部署必须通过 Docker Compose 运行"
    assert docker_preflight.start() < first_docker_compose.start(), (
        "Docker 缺失时必须在首次 docker compose 调用前失败"
    )
