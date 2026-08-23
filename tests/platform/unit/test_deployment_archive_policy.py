from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
import tarfile

import pytest

from scripts.deploy import archive_policy
from scripts.deploy.package import build_artifact


def _add_regular_member(
    archive: tarfile.TarFile,
    name: str,
    payload: bytes = b"safe\n",
    *,
    mode: int = 0o644,
) -> None:
    member = tarfile.TarInfo(name)
    member.mode = mode
    member.size = len(payload)
    archive.addfile(member, BytesIO(payload))


def _write_archive(path: Path, add_members: Callable[[tarfile.TarFile], object]) -> Path:
    with tarfile.open(path, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
        add_members(archive)
    return path


def test_deployment_artifact_policy_accepts_a_regular_allowed_member(tmp_path: Path) -> None:
    archive_path = _write_archive(
        tmp_path / "artifact.tar.gz",
        lambda archive: _add_regular_member(archive, "pyproject.toml"),
    )

    archive_policy.validate_deployment_artifact(archive_path)


def test_deployment_artifact_policy_accepts_the_artifact_emitted_by_the_packager(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    for file_name in ("pyproject.toml", "README.md", "uv.lock", "alembic.ini", ".env.example"):
        (project_root / file_name).write_text("safe\n", encoding="utf-8")
    for directory_name in (
        "alembic",
        "configs",
        "src",
        "templates",
        "ontology",
        "datasets",
        "infra/systemd",
    ):
        (project_root / directory_name).mkdir(parents=True, exist_ok=True)
    (project_root / "configs/app.example.yaml").write_text("safe: true\n", encoding="utf-8")
    (project_root / "src/northstar_quant.py").write_text("pass\n", encoding="utf-8")
    for file_name in ("check_dependency_policy.py", "bootstrap_pep517.py"):
        path = project_root / "scripts" / "ci" / file_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("pass\n", encoding="utf-8")

    artifact = build_artifact(
        project_root=project_root,
        output_dir=tmp_path / "dist",
        revision="test",
        built_at=datetime(2026, 8, 22, tzinfo=UTC),
    )

    archive_policy.validate_deployment_artifact(artifact.path)


@pytest.mark.parametrize("unsafe_name", ("/absolute", "src/../escape.py", "src//escape.py"))
def test_deployment_artifact_policy_rejects_ambiguous_or_escaping_member_paths(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    archive_path = _write_archive(
        tmp_path / "unsafe-path.tar.gz",
        lambda archive: _add_regular_member(archive, unsafe_name),
    )

    with pytest.raises(archive_policy.DeploymentArtifactPolicyError, match="member path"):
        archive_policy.validate_deployment_artifact(archive_path)


def test_deployment_artifact_policy_rejects_a_regular_file_with_a_directory_name(
    tmp_path: Path,
) -> None:
    archive_path = _write_archive(
        tmp_path / "regular-with-slash.tar.gz",
        lambda archive: _add_regular_member(archive, "src/entry.py/"),
    )

    with pytest.raises(archive_policy.DeploymentArtifactPolicyError, match="regular-file path"):
        archive_policy.validate_deployment_artifact(archive_path)


def test_deployment_artifact_policy_rejects_links_and_devices_before_extraction(
    tmp_path: Path,
) -> None:
    def add_members(archive: tarfile.TarFile) -> None:
        link = tarfile.TarInfo("src/link.py")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../etc/passwd"
        archive.addfile(link)

    link_archive = _write_archive(tmp_path / "link.tar.gz", add_members)
    with pytest.raises(archive_policy.DeploymentArtifactPolicyError, match="link member"):
        archive_policy.validate_deployment_artifact(link_archive)

    def add_device(archive: tarfile.TarFile) -> None:
        device = tarfile.TarInfo("src/device")
        device.type = tarfile.CHRTYPE
        device.devmajor = 1
        device.devminor = 3
        archive.addfile(device)

    device_archive = _write_archive(tmp_path / "device.tar.gz", add_device)
    with pytest.raises(archive_policy.DeploymentArtifactPolicyError, match="device or special"):
        archive_policy.validate_deployment_artifact(device_archive)


def test_deployment_artifact_policy_rejects_sparse_members_when_tarfile_detects_them() -> None:
    sparse_member = tarfile.TarInfo("src/sparse.py")
    sparse_member.type = tarfile.GNUTYPE_SPARSE
    sparse_member.sparse = [(0, 1)]

    with pytest.raises(archive_policy.DeploymentArtifactPolicyError, match="sparse member"):
        archive_policy._validate_artifact_member(
            sparse_member,
            seen_paths=set(),
            regular_paths=set(),
            descendant_prefixes=set(),
            total_unpacked_bytes=0,
        )


def test_deployment_artifact_policy_rejects_excessive_member_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive_path = _write_archive(
        tmp_path / "many-members.tar.gz",
        lambda archive: (
            _add_regular_member(archive, "src/first.py"),
            _add_regular_member(archive, "src/second.py"),
        ),
    )
    monkeypatch.setattr(archive_policy, "MAX_DEPLOYMENT_ARTIFACT_MEMBERS", 1)

    with pytest.raises(archive_policy.DeploymentArtifactPolicyError, match="member count"):
        archive_policy.validate_deployment_artifact(archive_path)


def test_deployment_artifact_policy_rejects_per_member_and_aggregate_expansion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    large_member_archive = _write_archive(
        tmp_path / "large-member.tar.gz",
        lambda archive: _add_regular_member(archive, "src/large.py", b"1234"),
    )
    monkeypatch.setattr(archive_policy, "MAX_DEPLOYMENT_ARTIFACT_MEMBER_BYTES", 3)
    with pytest.raises(archive_policy.DeploymentArtifactPolicyError, match="member exceeds"):
        archive_policy.validate_deployment_artifact(large_member_archive)

    aggregate_archive = _write_archive(
        tmp_path / "aggregate.tar.gz",
        lambda archive: (
            _add_regular_member(archive, "src/first.py", b"12"),
            _add_regular_member(archive, "src/second.py", b"34"),
        ),
    )
    monkeypatch.setattr(archive_policy, "MAX_DEPLOYMENT_ARTIFACT_MEMBER_BYTES", 4)
    monkeypatch.setattr(archive_policy, "MAX_DEPLOYMENT_ARTIFACT_UNPACKED_BYTES", 3)
    with pytest.raises(archive_policy.DeploymentArtifactPolicyError, match="aggregate unpacked"):
        archive_policy.validate_deployment_artifact(aggregate_archive)


def test_deployment_artifact_policy_rejects_special_modes_and_file_directory_collisions(
    tmp_path: Path,
) -> None:
    special_mode_archive = _write_archive(
        tmp_path / "setuid.tar.gz",
        lambda archive: _add_regular_member(archive, "src/entry.py", mode=0o4755),
    )
    with pytest.raises(archive_policy.DeploymentArtifactPolicyError, match="privileged mode"):
        archive_policy.validate_deployment_artifact(special_mode_archive)

    collision_archive = _write_archive(
        tmp_path / "collision.tar.gz",
        lambda archive: (
            _add_regular_member(archive, "src/module"),
            _add_regular_member(archive, "src/module/entry.py"),
        ),
    )
    with pytest.raises(archive_policy.DeploymentArtifactPolicyError, match="child below a regular"):
        archive_policy.validate_deployment_artifact(collision_archive)
