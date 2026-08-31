"""Contracts for hardened Linux deployment shell entrypoints."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.helpers.paths import PROJECT_ROOT


SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ENTRYPOINTS = (
    PROJECT_ROOT / "scripts" / "deploy" / "provision.sh",
    PROJECT_ROOT / "scripts" / "deploy" / "install-runtime.sh",
    PROJECT_ROOT / "scripts" / "deploy" / "install-release.sh",
    PROJECT_ROOT / "scripts" / "deploy" / "ntfy" / "provision-ntfy.sh",
    PROJECT_ROOT / "scripts" / "deploy" / "remote" / "linux" / "common.sh",
    PROJECT_ROOT / "scripts" / "deploy" / "remote" / "linux" / "install.sh",
    PROJECT_ROOT / "scripts" / "deploy" / "remote" / "linux" / "upgrade.sh",
    PROJECT_ROOT / "scripts" / "deploy" / "remote" / "linux" / "rollback.sh",
    PROJECT_ROOT / "scripts" / "deploy" / "remote" / "linux" / "uninstall.sh",
)


@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
def test_privileged_shell_entrypoints_harden_startup_before_path_resolution(
    entrypoint: Path,
) -> None:
    """A direct root invocation cannot inherit Bash hooks or command lookup."""

    source = entrypoint.read_text(encoding="utf-8")

    assert source.startswith("#!/bin/bash -p\n")
    assert not source.startswith("#!/usr/bin/env bash")

    unset_position = source.index("unset BASH_ENV ENV CDPATH")
    path_position = source.index(f'PATH="{SAFE_PATH}"')
    export_position = source.index("export PATH")
    strict_mode_position = source.index("set -euo pipefail")
    first_dirname_position = source.index("$(dirname")
    first_source_match = re.search(r"^source ", source, re.MULTILINE)

    assert first_source_match is not None
    assert unset_position < path_position < export_position < strict_mode_position
    assert export_position < first_dirname_position
    assert export_position < first_source_match.start()


def test_remote_install_and_upgrade_reexec_provision_in_privileged_bash_mode() -> None:
    remote_dir = PROJECT_ROOT / "scripts" / "deploy" / "remote" / "linux"

    install = (remote_dir / "install.sh").read_text(encoding="utf-8")
    upgrade = (remote_dir / "upgrade.sh").read_text(encoding="utf-8")

    assert 'exec env SETUP_SERVER=1 /bin/bash -p "${DEPLOY_DIR}/provision.sh" "$@"' in install
    assert 'exec env SETUP_SERVER=0 /bin/bash -p "${DEPLOY_DIR}/provision.sh" "$@"' in upgrade


def test_every_privileged_deployment_shell_boundary_requires_linux_x86_64() -> None:
    deploy_dir = PROJECT_ROOT / "scripts" / "deploy"
    common = (deploy_dir / "lib" / "common.sh").read_text(encoding="utf-8")

    assert "deploy_require_linux_x86_64() {" in common
    assert 'machine_name="$(uname -m)"' in common
    assert "x86_64|amd64" in common

    for entrypoint in (
        deploy_dir / "provision.sh",
        deploy_dir / "install-runtime.sh",
        deploy_dir / "install-release.sh",
        deploy_dir / "gate_release.sh",
        deploy_dir / "ntfy" / "provision-ntfy.sh",
        deploy_dir / "remote" / "linux" / "common.sh",
    ):
        assert "deploy_require_linux_x86_64" in entrypoint.read_text(encoding="utf-8")


def test_provision_runs_root_shell_children_with_an_empty_environment() -> None:
    """Root child shells cannot inherit deployment-user startup hooks or PATH."""

    provision = (PROJECT_ROOT / "scripts" / "deploy" / "provision.sh").read_text(
        encoding="utf-8"
    )

    assert "deploy_as_root env \\\n" not in provision
    assert provision.count("deploy_as_root env -i \\\n") >= 8
    assert '/bin/bash -p "${SCRIPT_DIR}/install-runtime.sh"' in provision
    assert '/bin/bash -p "${SCRIPT_DIR}/ntfy/provision-ntfy.sh"' in provision
    assert '/bin/bash -p "${SCRIPT_DIR}/install-release.sh"' in provision
