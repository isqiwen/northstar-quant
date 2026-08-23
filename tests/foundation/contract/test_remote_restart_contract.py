"""Contracts for the privileged, fixed-target Linux restart wrapper."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.helpers.paths import PROJECT_ROOT


RESTART_SCRIPT = PROJECT_ROOT / "scripts" / "deploy" / "remote" / "linux" / "restart.sh"


def _bash_executable() -> str:
    if os.name == "nt":
        git = shutil.which("git")
        if git is not None:
            candidate = Path(git).resolve().parent.parent / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("restart wrapper contract requires Bash")
    return bash


def _extract_function(source: str, name: str, following_name: str) -> str:
    start = source.index(f"{name}() {{")
    end = source.index(f"\n}}\n\n{following_name}() {{", start) + 2
    return source[start:end]


def test_remote_restart_is_a_fixed_canonical_service_operation() -> None:
    restart = RESTART_SCRIPT.read_text(encoding="utf-8")

    assert restart.startswith("#!/bin/bash -p\n")
    assert restart.index('PATH="/usr/sbin:/usr/bin:/sbin:/bin"') < restart.index("SCRIPT_DIR=")
    assert "readonly PATH" in restart
    assert restart.index("unset BASH_ENV ENV CDPATH") < restart.index("SCRIPT_DIR=")
    assert 'source "${DEPLOY_DIR}/lib/service_identity.sh"' in restart
    assert 'source "${DEPLOY_DIR}/lib/release_environment.sh"' in restart
    assert 'readonly CANONICAL_SERVICE_NAME="northstar-quant"' in restart
    assert 'readonly CANONICAL_SERVICE_HOME="/var/lib/northstar"' in restart
    assert 'readonly CANONICAL_CURRENT_LINK="/opt/northstar/current"' in restart
    assert 'readonly CANONICAL_ENV_FILE="/etc/northstar/northstar-quant.env"' in restart
    assert 'readonly CANONICAL_ENV_RELEASES_DIR="/etc/northstar/releases"' in restart
    assert 'readonly CANONICAL_SYSTEMD_UNIT_FILE="/etc/systemd/system/northstar-quant.service"' in (
        restart
    )
    assert 'SERVICE_NAME="${SYSTEMD_SERVICE_NAME:-northstar-quant}"' not in restart
    assert 'assert_canonical_restart_setting "SYSTEMD_SERVICE_NAME" "${CANONICAL_SERVICE_NAME}"' in (
        restart
    )
    assert 'deploy_as_root systemctl restart "${CANONICAL_SERVICE_NAME}.service"' in restart
    assert restart.index("if ! assert_managed_restart_target; then") < restart.index(
        'deploy_as_root systemctl restart "${CANONICAL_SERVICE_NAME}.service"'
    )


def test_remote_restart_rejects_unknown_or_writable_release_snapshot_chain() -> None:
    restart = RESTART_SCRIPT.read_text(encoding="utf-8")

    for expected in (
        'assert_root_controlled_restart_directory "/opt"',
        'assert_root_controlled_restart_directory "${CANONICAL_APP_ROOT}"',
        'assert_root_controlled_restart_directory "${CANONICAL_RELEASES_DIR}"',
        "assert_root_owned_current_restart_link",
        'assert_root_controlled_restart_directory "${release_dir}"',
        'assert_root_controlled_restart_directory "${release_dir}/.northstar"',
        'assert_root_controlled_restart_directory "${release_dir}/.northstar/systemd"',
        'assert_root_controlled_restart_file "${snapshot_file}"',
        'assert_root_controlled_restart_file "${CANONICAL_SYSTEMD_UNIT_FILE}"',
        'assert_managed_restart_environment_snapshot "${release_id}"',
        'systemctl show -p FragmentPath --value "${CANONICAL_SERVICE_NAME}.service"',
        'systemctl show -p DropInPaths --value "${CANONICAL_SERVICE_NAME}.service"',
        'sha256sum "${CANONICAL_SYSTEMD_UNIT_FILE}"',
        'sha256sum "${snapshot_file}"',
    ):
        assert expected in restart

    assert '""|.*|*/*|*[!A-Za-z0-9._-]*)' in restart
    assert '[ "${current_target}" != "${release_dir}" ]' in restart
    assert "2:*|3:*|6:*|7:*|*:2|*:3|*:6|*:7" in restart
    assert "assert_managed_restart_environment_snapshot()" in restart
    assert 'active_snapshot="$(deploy_resolve_managed_active_environment_snapshot)"' in restart
    assert '[ "${active_snapshot}" = "${CANONICAL_ENV_RELEASES_DIR}/${release_id}.env" ]' in (
        restart
    )
    assert restart.index('assert_managed_restart_environment_snapshot "${release_id}"') < (
        restart.index('deploy_as_root systemctl restart "${CANONICAL_SERVICE_NAME}.service"')
    )


@pytest.mark.parametrize(
    ("configured_value", "expected_success"),
    (("northstar-quant", True), ("sshd", False), ("", False)),
)
def test_remote_restart_rejects_any_service_name_override(
    configured_value: str,
    expected_success: bool,
) -> None:
    restart = RESTART_SCRIPT.read_text(encoding="utf-8")
    helper = _extract_function(
        restart,
        "assert_canonical_restart_setting",
        "assert_root_controlled_restart_directory",
    )
    command = "\n".join(
        (
            "set -euo pipefail",
            "deploy_fail() { exit 91; }",
            helper,
            'SYSTEMD_SERVICE_NAME="$1"',
            'assert_canonical_restart_setting "SYSTEMD_SERVICE_NAME" "northstar-quant"',
        )
    )

    result = subprocess.run(
        [_bash_executable(), "-c", command, "restart-service-name-contract", configured_value],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert (result.returncode == 0) is expected_success, result.stderr


@pytest.mark.parametrize(
    ("metadata", "expected_success"),
    (("0:755", True), ("1000:755", False), ("0:775", False)),
)
def test_remote_restart_root_controlled_path_check_rejects_writable_or_nonroot_paths(
    metadata: str,
    expected_success: bool,
) -> None:
    restart = RESTART_SCRIPT.read_text(encoding="utf-8")
    helper = _extract_function(
        restart,
        "assert_root_controlled_restart_directory",
        "assert_root_controlled_restart_file",
    )
    command = "\n".join(
        (
            "set -euo pipefail",
            'RESTART_METADATA="$1"',
            'deploy_as_root() { "$@"; }',
            "stat() {",
            '  [ "$1" = "-c" ] || return 95',
            '  [ "$2" = "%u:%a" ] || return 96',
            '  printf "%s\\n" "${RESTART_METADATA}"',
            "}",
            helper,
            'probe_dir="$(mktemp -d)"',
            "trap 'rm -rf -- \"${probe_dir}\"' EXIT",
            'assert_root_controlled_restart_directory "${probe_dir}"',
        )
    )

    result = subprocess.run(
        [_bash_executable(), "-c", command, "restart-path-contract", metadata],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert (result.returncode == 0) is expected_success, result.stderr
