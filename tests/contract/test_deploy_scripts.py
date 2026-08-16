"""Linux 部署脚本的制品与安全门槛测试。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

from tests.support.paths import PROJECT_ROOT

ROOT_DIR = PROJECT_ROOT
DEPLOY_DIR = ROOT_DIR / "scripts" / "deploy"
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
    """返回可实际执行的 Bash，避免 Windows 优先命中无发行版的 WSL 占位程序。"""

    if os.name == "nt":
        git_executable = shutil.which("git")
        if git_executable:
            git_bash = Path(git_executable).resolve().parent.parent / "bin" / "bash.exe"
            if git_bash.is_file():
                return str(git_bash)

    bash_executable = shutil.which("bash")
    if bash_executable:
        return bash_executable

    pytest.skip("部署脚本契约测试需要可执行的 Bash；请安装 Git Bash 或配置 WSL 发行版。")


BASH_EXECUTABLE = _resolve_bash_executable()


def _bash_path(path: Path) -> str:
    """将 Windows 路径转换为 Git Bash 可识别的挂载路径。"""

    if os.name != "nt":
        return str(path)

    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if len(drive) != 1:
        raise ValueError(f"无法转换为 Git Bash 路径：{resolved}")
    return f"/{drive}{resolved.as_posix()[2:]}"


def _run_bash(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
    """以 UTF-8 捕获部署脚本输出，兼容 Linux Bash 与 Windows Git Bash。"""

    return subprocess.run(
        [BASH_EXECUTABLE, *args],
        encoding="utf-8",
        errors="replace",
        **kwargs,
    )


def test_top_level_deploy_entrypoint_forwards_help() -> None:
    result = _run_bash(
        _bash_path(ROOT_DIR / "scripts" / "deploy.sh"),
        "--help",
        cwd=ROOT_DIR,
        check=False,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "首次部署" in result.stdout
    assert "scripts/deploy.sh" in result.stdout


def test_deploy_entrypoint_uses_single_active_env_file() -> None:
    content = (DEPLOY_DIR / "deploy.sh").read_text(encoding="utf-8")

    assert "ENV_FILE=.env" in content
    assert 'ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env}"' in content
    assert '"${ENV_FILE##*/}" != ".env"' in content
    assert 'ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env.production}"' not in content


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
    artifact_path = tmp_path / "northstar-quant-test.tar.gz"
    env = {
        **os.environ,
        "ARTIFACT_DIR": _bash_path(tmp_path),
        "ARTIFACT_NAME": artifact_path.name,
        "ARTIFACT_PATH": _bash_path(artifact_path),
        "REVISION": "test",
        "STAMP": "20260730000000",
    }

    _run_bash(
        _bash_path(DEPLOY_DIR / "build-artifact.sh"),
        cwd=ROOT_DIR,
        env=env,
        check=True,
        capture_output=True,
    )

    with tarfile.open(artifact_path, "r:gz") as archive:
        names = {name.removeprefix("./") for name in archive.getnames()}

    assert "pyproject.toml" in names
    assert "uv.lock" in names
    assert "src/northstar_quant/cli.py" in names
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

    builder = (DEPLOY_DIR / "build-artifact.sh").read_text(encoding="utf-8")
    assert "configs/app.example.yaml" in builder
    assert "--exclude='configs/app.yaml'" in builder
    assert "--exclude='configs/app.local.yaml'" in builder


def test_health_deploy_accepts_safe_production_environment(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "NORTHSTAR_ENV=production",
                "NORTHSTAR_DATABASE_URL=postgresql+psycopg://northstar:secret@db/northstar",
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


def test_health_deploy_rejects_live_environment(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "NORTHSTAR_ENV=production",
                "NORTHSTAR_DATABASE_URL=postgresql+psycopg://northstar:secret@db/northstar",
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
                "NORTHSTAR_DATABASE_URL=postgresql+psycopg://northstar:secret@db/northstar",
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
    config_loader = (DEPLOY_DIR / "lib" / "config.sh").read_text(encoding="utf-8")
    runtime_paths = (DEPLOY_DIR / "lib" / "runtime_paths.sh").read_text(encoding="utf-8")
    deploy_script = (DEPLOY_DIR / "deploy.sh").read_text(encoding="utf-8")
    provision_script = (DEPLOY_DIR / "provision.sh").read_text(encoding="utf-8")
    runtime_install_script = (DEPLOY_DIR / "install-runtime.sh").read_text(encoding="utf-8")
    release_script = (DEPLOY_DIR / "install-release.sh").read_text(encoding="utf-8")
    run_release_command = release_script.split("run_release_command() {", maxsplit=1)[1].split(
        "\n}\n\nrender_systemd_unit", maxsplit=1
    )[0]

    for key in RUNTIME_PATH_KEYS:
        assert f"{key}=" in config_example
        assert key in config_loader
        assert key in deploy_script
        assert key in provision_script
        assert key in release_script

    for template_name in ("health.service.in", "scheduler.service.in"):
        template = (DEPLOY_DIR / "systemd" / template_name).read_text(encoding="utf-8")
        assert "Environment=NORTHSTAR_STORAGE_DIR=" not in template
        assert "Environment=NORTHSTAR_DOWNLOADS_DIR=" not in template
        assert "Environment=NORTHSTAR_REPORTS_DIR=" not in template
        assert "Environment=NORTHSTAR_LOG_DIR=" not in template
        assert "Environment=XDG_CACHE_HOME=@RUNTIME_CACHE_DIR@" in template
        assert "Environment=MPLCONFIGDIR=@RUNTIME_MATPLOTLIB_DIR@" in template
        assert "ReadWritePaths=@SHARED_DIR@ @RUNTIME_STORAGE_DIR@" in template
        assert "@RUNTIME_LOG_DIR@" in template

    health_template = (DEPLOY_DIR / "systemd" / "health.service.in").read_text(encoding="utf-8")
    assert "ExecStart=@CURRENT_LINK@/.venv/bin/northstar health --fail-on-blocked" in health_template

    active_config_write = (
        'deploy_write_active_app_config \\\n'
        '  "${STAGE_DIR}/configs/app.example.yaml" \\\n'
        '  "${STAGE_DIR}/configs/app.yaml" \\\n'
        '  "${SERVICE_USER}"'
    )
    assert active_config_write in release_script
    assert 'deploy_write_runtime_config "${STAGE_DIR}/configs/app.local.yaml" "${SERVICE_USER}"' not in release_script
    assert 'ln -s "${APP_LOCAL_CONFIG_FILE}" "${STAGE_DIR}/configs/app.local.yaml"' not in release_script
    assert '"${SHARED_DIR}/config/app.local.yaml"' not in release_script
    assert "deploy_write_active_app_config" not in runtime_install_script
    assert release_script.index(active_config_write) < release_script.index(
        'run_release_command "${STAGE_DIR}" "${STAGE_DIR}/.venv/bin/northstar" init-db'
    )
    assert (
        'run_release_command "${STAGE_DIR}" "${STAGE_DIR}/.venv/bin/northstar" '
        'health --fail-on-blocked'
    ) in release_script
    cutover = release_script.split('deploy_log "停止当前服务，准备原子切换 current"', maxsplit=1)[1]
    assert cutover.index('systemctl stop "${SYSTEMD_SERVICE_NAME}.service"') < cutover.index(
        "switch_current_release"
    )
    assert "backup_systemd_unit()" in release_script
    assert "restore_systemd_unit()" in release_script
    assert "render_systemd_unit()" in release_script
    assert "install_rendered_systemd_unit()" in release_script
    activation = release_script.split('deploy_log "渲染新版本 systemd 服务配置"', maxsplit=1)[1]
    assert activation.index("render_systemd_unit || ! backup_systemd_unit") < activation.index(
        'systemctl stop "${SYSTEMD_SERVICE_NAME}.service"'
    )
    assert activation.index('systemctl stop "${SYSTEMD_SERVICE_NAME}.service"') < activation.index(
        "install_rendered_systemd_unit"
    ) < activation.index("switch_current_release")
    assert "recover_interrupted_cutover()" in release_script
    assert "trap 'recover_interrupted_cutover $?' ERR" in release_script
    rollback = release_script.split("rollback_release() {", maxsplit=1)[1].split(
        "\n}\n\nprune_old_releases", maxsplit=1
    )[0]
    assert rollback.index("restore_systemd_unit") < rollback.index(
        'systemctl restart "${SYSTEMD_SERVICE_NAME}.service"'
    )
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
deploy_configure_runtime_paths /srv/northstar/northstar-quant
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
        "/srv/northstar/northstar-quant/shared/storage",
        "/srv/northstar/northstar-quant/shared/storage/downloads",
        "/srv/northstar/northstar-quant/shared/reports",
        "/srv/northstar/northstar-quant/shared/logs",
        "/srv/northstar/northstar-quant/shared/cache",
        "/srv/northstar/northstar-quant/shared/matplotlib",
    ]

    derived_downloads_result = _run_bash(
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

    assert derived_downloads_result.returncode == 0, derived_downloads_result.stderr
    assert derived_downloads_result.stdout.strip().split("|")[:2] == [
        "/mnt/northstar-quant/storage",
        "/mnt/northstar-quant/storage/downloads",
    ]

    custom_result = _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        "/mnt/northstar-quant/storage",
        "/mnt/northstar-quant/downloads",
        "/mnt/northstar-quant/reports",
        "/var/log/northstar-quant",
        "/var/cache/northstar-quant",
        "/var/cache/northstar-quant/matplotlib",
        check=False,
        capture_output=True,
    )

    assert custom_result.returncode == 0, custom_result.stderr
    assert custom_result.stdout.strip().split("|") == [
        "/mnt/northstar-quant/storage",
        "/mnt/northstar-quant/downloads",
        "/mnt/northstar-quant/reports",
        "/var/log/northstar-quant",
        "/var/cache/northstar-quant",
        "/var/cache/northstar-quant/matplotlib",
    ]


def test_runtime_output_paths_reject_unsafe_location() -> None:
    relative_path_command = """
set -euo pipefail
source "$1/lib/common.sh"
source "$1/lib/runtime_paths.sh"
RUNTIME_STORAGE_DIR=relative/storage
deploy_configure_runtime_paths /srv/northstar/northstar-quant
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
RUNTIME_STORAGE_DIR=/srv/northstar/northstar-quant/releases/storage
deploy_configure_runtime_paths /srv/northstar/northstar-quant
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
    assert "releases" in release_path_result.stderr


def _load_ntfy_deploy_config(config_file: Path) -> subprocess.CompletedProcess[str]:
    """加载部署配置并输出 ntfy 的非机密字段。"""

    command = """
set -euo pipefail
source "$1/lib/common.sh"
source "$1/lib/config.sh"
for key in \
  NTFY_DEPLOY_ENABLED \
  NTFY_PUBLIC_HOST \
  NTFY_ACME_EMAIL \
  NTFY_IMAGE \
  NTFY_CADDY_IMAGE \
  NTFY_CONFIG_DIR \
  NTFY_DATA_DIR \
  NTFY_CACHE_DURATION; do
  unset "${key}" || true
done
deploy_load_config "$2"
printf '%s|%s|%s|%s|%s|%s|%s|%s\\n' \
  "${NTFY_DEPLOY_ENABLED:-}" \
  "${NTFY_PUBLIC_HOST:-}" \
  "${NTFY_ACME_EMAIL:-}" \
  "${NTFY_IMAGE:-}" \
  "${NTFY_CADDY_IMAGE:-}" \
  "${NTFY_CONFIG_DIR:-}" \
  "${NTFY_DATA_DIR:-}" \
  "${NTFY_CACHE_DURATION:-}"
"""
    return _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        _bash_path(config_file),
        check=False,
        capture_output=True,
    )


def test_private_ntfy_deploy_defaults_closed_and_uses_nonsecret_whitelist(
    tmp_path: Path,
) -> None:
    """私有 ntfy 必须默认关闭，且 deploy.env 不能承载身份或令牌。"""

    example_config = ROOT_DIR / "deploy.env.example"
    example_content = example_config.read_text(encoding="utf-8")
    assert "NTFY_DEPLOY_ENABLED=0" in example_content

    default_result = _load_ntfy_deploy_config(example_config)
    assert default_result.returncode == 0, default_result.stderr
    default_values = default_result.stdout.strip().split("|")
    assert default_values[0] == "0"
    assert default_values[1:3] == ["", ""]
    assert default_values[3] == "binwiederhier/ntfy:v2.27.0"
    assert default_values[4] == "caddy:2.10.2-alpine"
    assert default_values[5:] == [
        "/etc/northstar-ntfy",
        "/var/lib/northstar-ntfy",
        "24h",
    ]

    enabled_config = tmp_path / "deploy.env"
    enabled_config.write_text(
        "\n".join(
            [
                "DEPLOY_HOST=ntfy.example.test",
                "NTFY_DEPLOY_ENABLED=1",
                "NTFY_PUBLIC_HOST=ntfy.example.test",
                "NTFY_ACME_EMAIL=ops@example.test",
                "NTFY_IMAGE=example.test/ntfy:v1",
                "NTFY_CADDY_IMAGE=example.test/caddy:v1",
                "NTFY_CONFIG_DIR=/etc/example-ntfy",
                "NTFY_DATA_DIR=/var/lib/example-ntfy",
                "NTFY_CACHE_DURATION=12h",
            ]
        ),
        encoding="utf-8",
    )
    enabled_result = _load_ntfy_deploy_config(enabled_config)
    assert enabled_result.returncode == 0, enabled_result.stderr
    assert enabled_result.stdout.strip().split("|") == [
        "1",
        "ntfy.example.test",
        "ops@example.test",
        "example.test/ntfy:v1",
        "example.test/caddy:v1",
        "/etc/example-ntfy",
        "/var/lib/example-ntfy",
        "12h",
    ]

    secret_in_deploy_config = tmp_path / "deploy-with-secret.env"
    secret_in_deploy_config.write_text(
        "\n".join(
            [
                "DEPLOY_HOST=ntfy.example.test",
                "NTFY_DEPLOY_ENABLED=1",
                "NTFY_ADMIN_PASSWORD=must-not-be-accepted-here",
            ]
        ),
        encoding="utf-8",
    )
    rejected_result = _load_ntfy_deploy_config(secret_in_deploy_config)
    assert rejected_result.returncode != 0
    assert "不支持的字段" in rejected_result.stderr

    config_loader = (DEPLOY_DIR / "lib" / "config.sh").read_text(encoding="utf-8")
    for key in NTFY_DEPLOY_KEYS:
        assert key in config_loader
    for secret_key in (
        "NTFY_ADMIN_USERNAME",
        "NTFY_ADMIN_PASSWORD",
        "NTFY_READER_USERNAME",
        "NTFY_READER_PASSWORD",
    ):
        assert secret_key not in config_loader


def test_private_ntfy_parameters_propagate_without_sending_bootstrap_secrets() -> None:
    """部署入口只转发非机密 ntfy 参数，身份文件另走显式的一次性上传。"""

    deploy_script = (DEPLOY_DIR / "deploy.sh").read_text(encoding="utf-8")
    provision_script = (DEPLOY_DIR / "provision.sh").read_text(encoding="utf-8")

    remote_command = deploy_script.split('REMOTE_COMMAND="sudo env"', maxsplit=1)[1].split(
        'deploy_log "执行远程部署"', maxsplit=1
    )[0]
    for key in NTFY_DEPLOY_KEYS:
        assert key in deploy_script
        assert key in remote_command
        assert key in provision_script

    assert "UPLOAD_NTFY_BOOTSTRAP" in deploy_script
    assert "NTFY_BOOTSTRAP_FILE" in deploy_script
    assert "NTFY_BOOTSTRAP_FILE" in provision_script
    for secret_key in (
        "NTFY_ADMIN_USERNAME",
        "NTFY_ADMIN_PASSWORD",
        "NTFY_READER_USERNAME",
        "NTFY_READER_PASSWORD",
    ):
        assert secret_key not in deploy_script
        assert secret_key not in provision_script


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
            assert configured_limit in {0, "0", "0B"}, (
                f"server.yml.in 不得放开 {limit_key}"
            )


def test_private_ntfy_persistent_state_is_root_owned_and_not_part_of_release() -> None:
    """顶层持久目录由 root 管理，仅 ntfy 专用子目录可由服务账户写入。"""

    ntfy_provision = (NTFY_DIR / "provision-ntfy.sh").read_text(encoding="utf-8")
    all_ntfy_sources = "\n".join(
        source.read_text(encoding="utf-8") for source in NTFY_DIR.iterdir() if source.is_file()
    )

    assert "NTFY_CONFIG_DIR" in ntfy_provision
    assert "NTFY_DATA_DIR" in ntfy_provision
    assert re.search(
        r'install\s+-d\s+-o\s+root\s+-g\s+root\s+-m\s+0750\s+"\$\{NTFY_DATA_DIR\}"',
        ntfy_provision,
    ), "NTFY_DATA_DIR 顶层目录必须是 root:root 0750"
    assert re.search(
        r'install\s+-d\s+-o\s+"\$\{NTFY_SERVICE_ACCOUNT\}"\s+-g\s+"\$\{NTFY_SERVICE_ACCOUNT\}"'
        r'\s+-m\s+0750\s+\\?\s*"\$\{NTFY_DATA_DIR\}/ntfy"',
        ntfy_provision,
    ), "仅 ntfy 数据子目录可授予专用服务账户写权限"
    assert re.search(r"install\s+-d[^\n]*-o\s+root[^\n]*-g\s+root", ntfy_provision)
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
    assert "releases" in release_path_result.stderr
    assert "docker compose down -v" not in all_ntfy_sources
    assert "docker-compose down -v" not in all_ntfy_sources
    assert 'rm -rf "${NTFY_CONFIG_DIR}' not in all_ntfy_sources
    assert 'rm -rf "${NTFY_DATA_DIR}' not in all_ntfy_sources


def test_private_ntfy_normal_release_never_rewrites_existing_identity() -> None:
    """未显式上传 bootstrap 时，普通发布只能验证既有身份，不能重建它。"""

    ntfy_provision = (NTFY_DIR / "provision-ntfy.sh").read_text(encoding="utf-8")

    assert re.search(
        r'if \[ "\$\{UPLOAD_NTFY_BOOTSTRAP\}" = "0" \]; then\s+'
        r'ntfy_assert_existing_server_config\s+fi',
        ntfy_provision,
    )
    bootstrap_render = re.search(
        r'if \[ "\$\{UPLOAD_NTFY_BOOTSTRAP\}" = "1" \]; then\s+'
        r'SERVER_TMP=.*?\s+ntfy_render_server_config\s+fi',
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
    # 生产服务器上的 YAML 使用 LF；Git Bash 对 Windows CRLF 的 read 保留 \r，
    # 会掩盖校验器本身的策略行为。
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
NORTHSTAR_NTFY_TOKEN=test-token
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

    builder = (DEPLOY_DIR / "build-artifact.sh").read_text(encoding="utf-8")
    # 制品白名单只包含运行源码目录，根目录的一次性身份文件没有进入路径。
    assert "ntfy.bootstrap" not in builder

    deployment_sources = [
        DEPLOY_DIR / "deploy.sh",
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


def test_private_ntfy_bootstrap_uses_private_remote_staging_directory() -> None:
    """活动 .env 与 bootstrap 只能短暂存在于权限为 0700 的远端工作目录。"""

    deploy_script = (DEPLOY_DIR / "deploy.sh").read_text(encoding="utf-8")

    assert re.search(
        r'REMOTE_ENV="\$\{REMOTE_WORK_DIR\}/[^"\n]+\.env"', deploy_script
    ), "远端活动 .env 不得直接落在 /tmp 根目录"
    assert re.search(
        r'REMOTE_NTFY_BOOTSTRAP="\$\{REMOTE_WORK_DIR\}/[^"\n]+ntfy\.bootstrap\.env"',
        deploy_script,
    ), "远端 bootstrap 文件必须位于 REMOTE_WORK_DIR"
    assert not re.search(r'REMOTE_(?:ENV|NTFY_BOOTSTRAP)="\$\{REMOTE_TMP\}/', deploy_script)
    assert re.search(
        r'(?:install\s+-d\s+-m\s+0700|mkdir\s+-m\s+0700)[^\n]*REMOTE_WORK_DIR',
        deploy_script,
    ), "远端临时工作目录必须以 0700 创建"
    assert 'deploy_scp "${NTFY_BOOTSTRAP_FILE}" "${DEPLOY_HOST}:${REMOTE_NTFY_BOOTSTRAP}"' in deploy_script


def _load_dashboard_deploy_config(config_file: Path) -> subprocess.CompletedProcess[str]:
    """加载 Dashboard 的非敏感部署开关。"""

    command = """
set -euo pipefail
source "$1/lib/common.sh"
source "$1/lib/config.sh"
unset DASHBOARD_DEPLOY_ENABLED || true
deploy_load_config "$2"
printf '%s\\n' "${DASHBOARD_DEPLOY_ENABLED:-}"
"""
    return _run_bash(
        "-c",
        command,
        BASH_EXECUTABLE,
        _bash_path(DEPLOY_DIR),
        _bash_path(config_file),
        check=False,
        capture_output=True,
    )


def test_private_dashboard_deploy_is_explicit_opt_in_and_keeps_main_service_mode() -> None:
    """Dashboard 是独立观察服务，默认关闭，不能替换 health/scheduler 主服务。"""

    example_config = ROOT_DIR / "deploy.env.example"
    example_content = example_config.read_text(encoding="utf-8")
    assert "DASHBOARD_DEPLOY_ENABLED=0" in example_content

    default_result = _load_dashboard_deploy_config(example_config)
    assert default_result.returncode == 0, default_result.stderr
    assert default_result.stdout.strip() == "0"

    deploy_script = (DEPLOY_DIR / "deploy.sh").read_text(encoding="utf-8")
    provision_script = (DEPLOY_DIR / "provision.sh").read_text(encoding="utf-8")
    release_script = (DEPLOY_DIR / "install-release.sh").read_text(encoding="utf-8")
    config_loader = (DEPLOY_DIR / "lib" / "config.sh").read_text(encoding="utf-8")
    dashboard_template = (DEPLOY_DIR / "systemd" / "dashboard.service.in").read_text(
        encoding="utf-8"
    )

    for source in (deploy_script, provision_script, release_script, config_loader):
        assert "DASHBOARD_DEPLOY_ENABLED" in source
    assert 'DASHBOARD_SERVICE_NAME="${SYSTEMD_SERVICE_NAME}-dashboard"' in release_script
    assert 'DASHBOARD_UNIT_FILE="/etc/systemd/system/${DASHBOARD_SERVICE_NAME}.service"' in (
        release_script
    )
    assert 'case "${SERVICE_MODE}" in\n  health|scheduler)' in release_script
    assert 'case "${SERVICE_MODE}" in\n  health|scheduler)' in deploy_script

    assert "Environment=NORTHSTAR_DASHBOARD_HOST=127.0.0.1" in dashboard_template
    assert "0.0.0.0" not in dashboard_template
    assert "ExecStart=@CURRENT_LINK@/.venv/bin/northstar dashboard run" in dashboard_template
    assert "EnvironmentFile=@ENV_FILE@" in dashboard_template
    assert (
        "ReadWritePaths=@DASHBOARD_HOME_DIR@ @RUNTIME_CACHE_DIR@ "
        "@RUNTIME_MATPLOTLIB_DIR@ @RUNTIME_LOG_DIR@"
    ) in dashboard_template

    activate_main_service = release_script.index("if ! activate_service; then")
    configure_dashboard = release_script.index("if ! configure_dashboard_systemd_unit; then")
    assert activate_main_service < configure_dashboard
    assert "fail_closed_dashboard_systemd_unit()" in release_script
    assert 'DASHBOARD_DEPLOY_STATUS="disabled_after_failure"' in release_script
    assert (
        "私网 Dashboard 配置或启动失败，已关闭该观察服务；"
        "主 health/scheduler 服务保持本次发布版本。"
    ) in release_script
    assert 'printf "dashboard=%s\\n" "${DASHBOARD_DEPLOY_STATUS}"' in release_script
    assert 'printf "dashboard_requested=%s\\n"' in deploy_script

    disable_dashboard_start = release_script.index("disable_dashboard_systemd_unit() {")
    disable_dashboard_end = release_script.index(
        "\n}\n\nconfigure_dashboard_systemd_unit()", disable_dashboard_start
    )
    disable_dashboard = release_script[disable_dashboard_start:disable_dashboard_end]
    # 即使 unit 文件被人工删除，也要尝试停止仍可能已加载的旧 Dashboard 进程。
    assert (
        'systemctl disable --now "${DASHBOARD_SERVICE_NAME}.service" '
        '>/dev/null 2>&1 || true'
    ) in disable_dashboard
    assert 'systemctl is-active --quiet "${DASHBOARD_SERVICE_NAME}.service"' in (
        disable_dashboard
    )


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
    assert not re.search(r"(?:usermod|adduser)[^\n]*\bdocker\b[^\n]*(?:SERVICE_USER|northstar)", deploy_sources)
    assert not re.search(r"(?:usermod|adduser)[^\n]*(?:SERVICE_USER|northstar)[^\n]*\bdocker\b", deploy_sources)

    docker_preflight = re.search(
        r"(?:deploy_need_cmd\s+docker|command\s+-v\s+docker)", ntfy_provision
    )
    assert docker_preflight, "私有 ntfy 部署必须显式检查 Docker"
    first_docker_compose = re.search(r"\bdocker\s+compose\b", ntfy_provision)
    assert first_docker_compose, "私有 ntfy 部署必须通过 Docker Compose 运行"
    assert docker_preflight.start() < first_docker_compose.start(), (
        "Docker 缺失时必须在首次 docker compose 调用前失败"
    )
