from __future__ import annotations

import tarfile
from datetime import UTC, datetime
from pathlib import Path

from scripts.deploy.control_bundle import CONTROL_ENTRYPOINT, build_control_artifact
from scripts.deploy.package import build_artifact


def _write_file(project_root: Path, relative_path: str, contents: str = "safe\n") -> Path:
    path = project_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return path


def _write_runtime_project(project_root: Path) -> None:
    project_root.mkdir()
    for relative_path in ("pyproject.toml", "README.md", "uv.lock", "alembic.ini", ".env.example"):
        _write_file(project_root, relative_path)
    for relative_path in (
        "alembic",
        "configs",
        "src",
        "templates",
        "ontology",
        "datasets",
        "infra/systemd",
    ):
        (project_root / relative_path).mkdir(parents=True, exist_ok=True)
    _write_file(project_root, "configs/app.example.yaml")
    _write_file(project_root, "src/northstar_quant/__init__.py")
    _write_file(project_root, "infra/systemd/health.service.in")
    _write_file(project_root, "scripts/ci/check_dependency_policy.py")
    _write_file(project_root, "scripts/ci/bootstrap_pep517.py")


def _archive_names(path: Path) -> set[str]:
    with tarfile.open(path, "r:gz") as archive:
        return {name.removeprefix("./") for name in archive.getnames()}


def test_runtime_artifact_excludes_nested_credentials_and_keeps_public_templates(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _write_runtime_project(project_root)

    excluded_paths = (
        "configs/.env",
        "configs/ntfy.bootstrap.env",
        "src/runtime/.env.production",
        "src/runtime/service.env",
        "src/runtime/.env/cache.py",
        "templates/certificates/service.PEM",
        "ontology/signing.key",
        "datasets/credentials/database.json",
        "infra/systemd/secrets/token.txt",
        "alembic/credentials.json",
        "src/private/backup.p12",
        "templates/private-key.pfx",
    )
    for relative_path in excluded_paths:
        _write_file(project_root, relative_path, "must not be archived\n")
    included_templates = (
        "configs/.env.example",
        "configs/ntfy.bootstrap.env.example",
        "templates/certificates/service.key.example",
        "ontology/signing.pem.example",
    )
    for relative_path in included_templates:
        _write_file(project_root, relative_path, "reviewed public template\n")

    artifact = build_artifact(
        project_root=project_root,
        output_dir=tmp_path / "dist",
        revision="test",
        built_at=datetime(2026, 8, 22, tzinfo=UTC),
    )
    names = _archive_names(artifact.path)

    assert not any(relative_path in names for relative_path in excluded_paths)
    assert set(included_templates).issubset(names)


def test_signed_control_bundle_uses_the_same_credential_exclusion_policy(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    control_root = project_root / "scripts/deploy"
    control_root.mkdir(parents=True)
    _write_file(project_root, "scripts/deploy/deploy.py")
    _write_file(project_root, CONTROL_ENTRYPOINT, "#!/bin/bash -p\n")

    excluded_paths = (
        "scripts/deploy/.env",
        "scripts/deploy/ntfy.bootstrap.env",
        "scripts/deploy/remote/.env.local",
        "scripts/deploy/remote/service.env",
        "scripts/deploy/remote/.env/runtime.sh",
        "scripts/deploy/credentials/api.json",
        "scripts/deploy/secrets/token.txt",
        "scripts/deploy/tls/server.pem",
        "scripts/deploy/tls/server.key",
        "scripts/deploy/tls/client.p12",
        "scripts/deploy/tls/client.pfx",
    )
    for relative_path in excluded_paths:
        _write_file(project_root, relative_path, "must not be archived\n")
    included_templates = (
        "scripts/deploy/.env.example",
        "scripts/deploy/service.env.example",
        "scripts/deploy/tls/server.key.example",
    )
    for relative_path in included_templates:
        _write_file(project_root, relative_path, "reviewed public template\n")

    control = build_control_artifact(
        project_root=project_root,
        output_dir=tmp_path / "dist",
        release_id="test-release",
        built_at=datetime(2026, 8, 23, tzinfo=UTC),
    )
    names = _archive_names(control.path)

    assert "scripts/deploy/deploy.py" in names
    assert CONTROL_ENTRYPOINT in names
    assert "DEPLOY_CONTROL_META.json" in names
    assert not any(relative_path in names for relative_path in excluded_paths)
    assert set(included_templates).issubset(names)
