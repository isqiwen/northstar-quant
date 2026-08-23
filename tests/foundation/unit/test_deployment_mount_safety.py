from __future__ import annotations

from pathlib import Path

import pytest

from scripts.deploy import mount_safety
from tests.helpers.paths import PROJECT_ROOT


def _mountinfo(*mountpoints: str) -> list[str]:
    records: list[str] = []
    for index, mountpoint in enumerate(mountpoints, start=2):
        escaped_mountpoint = mountpoint.replace(" ", "\\040")
        records.append(f"{index} 1 0:{index} / {escaped_mountpoint} rw - tmpfs tmpfs rw\n")
    return records


def test_tree_mount_check_rejects_a_same_device_bind_mount_below_tree(tmp_path: Path) -> None:
    tree = tmp_path / "releases" / "candidate"
    tree.mkdir(parents=True)
    nested_mount = tree / "storage"
    nested_mount.mkdir()

    with pytest.raises(mount_safety.MountSafetyError, match="contains a mount point"):
        mount_safety.assert_tree_has_no_mounts(
            tree,
            mountinfo=_mountinfo(str(nested_mount)),
        )


def test_tree_mount_check_allows_an_ancestor_mount_but_rejects_the_tree_itself(tmp_path: Path) -> None:
    tree = tmp_path / "releases" / "candidate"
    tree.mkdir(parents=True)

    mount_safety.assert_tree_has_no_mounts(tree, mountinfo=_mountinfo(str(tmp_path)))

    with pytest.raises(mount_safety.MountSafetyError, match="contains a mount point"):
        mount_safety.assert_tree_has_no_mounts(tree, mountinfo=_mountinfo(str(tree)))


def test_mountinfo_parser_decodes_escaped_paths() -> None:
    assert list(mount_safety.iter_mountpoints(_mountinfo("/opt/northstar/a b"))) == [
        Path("/opt/northstar/a b")
    ]


def test_release_installer_checks_the_kernel_mount_table_before_root_recursion() -> None:
    release_script = (PROJECT_ROOT / "scripts" / "deploy" / "install-release.sh").read_text(
        encoding="utf-8"
    )

    assert 'python3 -I "${SCRIPT_DIR}/mount_safety.py" "${tree_path}"' in release_script
    assert (
        'assert_root_owned_tree_without_mounts "${STAGE_DIR}" || return 1\n'
        '  deploy_as_root find -P "${STAGE_DIR}"'
    ) in release_script
    assert 'assert_root_owned_tree_without_mounts "${release_dir}" || return 1' in release_script
    assert 'rm -rf --one-file-system -- "${STAGE_DIR}"' in release_script
    assert 'rm -rf --one-file-system -- "${release_dir}"' in release_script


def test_runtime_installer_checks_the_kernel_mount_table_before_root_python_sealing() -> None:
    """A same-device bind mount cannot receive recursive uv metadata changes."""

    runtime_script = (PROJECT_ROOT / "scripts" / "deploy" / "install-runtime.sh").read_text(
        encoding="utf-8"
    )

    mount_check = runtime_script.index("assert_managed_root_tree_has_no_mounts() {")
    bootstrap_preflight = runtime_script.index(
        'if ! assert_managed_root_tree_has_no_mounts "${UV_BOOTSTRAP_CACHE_DIR}" ||'
    )
    python_preflight = runtime_script.index(
        '! assert_managed_root_tree_has_no_mounts "${UV_PYTHON_INSTALL_DIR}"; then',
        bootstrap_preflight,
    )
    uv_install = runtime_script.index('/usr/local/bin/uv python install "${PYTHON_VERSION}"')
    second_preflight = runtime_script.index(
        'if ! assert_managed_root_tree_has_no_mounts "${UV_PYTHON_INSTALL_DIR}"; then',
        python_preflight + 1,
    )
    ownership_seal = runtime_script.index(" -exec chown root:root -- {} +")
    mode_seal = runtime_script.index(" -exec chmod go-w -- {} +")

    assert (
        mount_check
        < bootstrap_preflight
        < python_preflight
        < uv_install
        < second_preflight
        < ownership_seal
        < mode_seal
    )
    assert "chown -R root:root" not in runtime_script
    assert "chmod -R go-w" not in runtime_script
