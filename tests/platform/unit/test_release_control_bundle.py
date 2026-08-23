from __future__ import annotations

import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.deploy.control_bundle import (
    CONTROL_BUNDLE_FORMAT,
    CONTROL_ENTRYPOINT,
    ControlBundleError,
    build_control_artifact,
)


def _write(project_root: Path, relative: str, content: str = "safe\n") -> None:
    path = project_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_control_bundle_is_no_overwrite_and_contains_only_reviewable_control_files(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _write(project_root, CONTROL_ENTRYPOINT, "#!/bin/bash -p\n")
    _write(project_root, "scripts/deploy/release_transaction.py")
    _write(project_root, "scripts/deploy/.env", "must never enter control bundle\n")

    artifact = build_control_artifact(
        project_root=project_root,
        output_dir=tmp_path / "dist",
        release_id="abc123-20260823",
        built_at=datetime(2026, 8, 23, tzinfo=UTC),
    )

    with tarfile.open(artifact.path, "r:gz") as archive:
        names = set(archive.getnames())
        metadata = archive.extractfile("DEPLOY_CONTROL_META.json")
        assert metadata is not None
        assert CONTROL_BUNDLE_FORMAT.encode("utf-8") in metadata.read()
    assert CONTROL_ENTRYPOINT in names
    assert "scripts/deploy/.env" not in names
    assert artifact.size_bytes == artifact.path.stat().st_size

    with pytest.raises(ControlBundleError, match="overwrite"):
        build_control_artifact(
            project_root=project_root,
            output_dir=tmp_path / "dist",
            release_id="abc123-20260823",
            built_at=datetime(2026, 8, 23, tzinfo=UTC),
        )


def test_control_bundle_requires_the_fixed_root_gate_entrypoint(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    (project_root / "scripts/deploy").mkdir(parents=True)

    with pytest.raises(ControlBundleError, match="entrypoint"):
        build_control_artifact(
            project_root=project_root,
            output_dir=tmp_path / "dist",
            release_id="abc123-20260823",
        )
