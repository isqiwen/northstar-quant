"""Linux x86_64 fail-closed contracts for local deployment components."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import subprocess
import sys
from typing import cast
from unittest.mock import Mock

import pytest

from scripts.deploy import archive_policy, control_bundle, deploy, package, platform_support, preflight
from scripts.deploy.inventory import DeploymentInventory, InventoryError, load_inventory
from scripts.deploy.venv_archive import VenvArchiveError, receive_venv_archive
from tests.helpers.paths import PROJECT_ROOT


@pytest.mark.parametrize(
    ("system_name", "machine"),
    (("Linux", "x86_64"), ("Linux", "AMD64")),
)
def test_deployment_platform_contract_accepts_linux_x86_64_aliases(
    system_name: str,
    machine: str,
) -> None:
    platform_support.require_linux_x86_64(system_name=system_name, machine=machine)


@pytest.mark.parametrize(
    ("system_name", "machine"),
    (("Windows", "AMD64"), ("Linux", "aarch64"), ("Darwin", "arm64")),
)
def test_deployment_platform_contract_rejects_unsupported_hosts(
    system_name: str,
    machine: str,
) -> None:
    with pytest.raises(platform_support.PlatformSupportError, match="Linux x86_64"):
        platform_support.require_linux_x86_64(system_name=system_name, machine=machine)


def test_local_deployment_components_reject_unsupported_host_before_io(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(platform_support.platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform_support.platform, "machine", lambda: "AMD64")
    project_root = tmp_path / "unread-project"
    output_dir = tmp_path / "must-not-exist"

    with pytest.raises(package.PackageError, match="Linux x86_64"):
        package.build_artifact(project_root=project_root, output_dir=output_dir)
    with pytest.raises(control_bundle.ControlBundleError, match="Linux x86_64"):
        control_bundle.build_control_artifact(
            project_root=project_root,
            output_dir=output_dir,
            release_id="release-1",
        )
    with pytest.raises(archive_policy.DeploymentArtifactPolicyError, match="Linux x86_64"):
        archive_policy.validate_deployment_artifact(tmp_path / "unread-artifact.tar.gz")
    with pytest.raises(InventoryError, match="Linux x86_64"):
        load_inventory(tmp_path / "unread-deploy.env")
    with pytest.raises(preflight.PreflightError, match="Linux x86_64"):
        preflight.run_preflight(
            project_root=project_root,
            inventory=cast(DeploymentInventory, None),
            upload_env=False,
            env_file=None,
            apply=False,
            confirm_live_deploy="NO",
        )
    with pytest.raises(VenvArchiveError, match="Linux x86_64"):
        receive_venv_archive(
            BytesIO(b"unread"),
            target_dir=tmp_path / "must-not-create-venv",
            temporary_dir=tmp_path,
        )

    assert not output_dir.exists()
    assert not (tmp_path / "must-not-create-venv").exists()


def test_deploy_entrypoint_rejects_unsupported_host_before_inventory_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform_support.platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform_support.platform, "machine", lambda: "AMD64")
    inventory_loader = Mock()
    monkeypatch.setattr(deploy, "load_inventory", inventory_loader)
    monkeypatch.setattr(sys, "argv", ["deploy.py", "--inventory", "unread-deploy.env"])

    assert deploy.main() == 1
    inventory_loader.assert_not_called()


@pytest.mark.parametrize(
    "relative_path",
    ("scripts/deploy/archive_policy.py", "scripts/deploy/venv_archive.py"),
)
def test_signed_control_entrypoints_can_import_the_host_guard_in_isolated_mode(
    relative_path: str,
) -> None:
    result = subprocess.run(
        [sys.executable, "-I", str(PROJECT_ROOT / relative_path), "--help"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
