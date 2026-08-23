"""Tests for the closed, fresh-venv PEP 517 bootstrap boundary."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.ci import bootstrap_pep517 as bootstrap
from scripts.ci import check_dependency_policy as dependency_policy
from tests.helpers.paths import PROJECT_ROOT


class _Response:
    def __init__(self, *, payload: bytes, url: str, status: int = 200) -> None:
        self._payload = payload
        self._offset = 0
        self._url = url
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, size: int) -> bytes:
        result = self._payload[self._offset : self._offset + size]
        self._offset += len(result)
        return result


class _Opener:
    def __init__(self, response: _Response) -> None:
        self._response = response

    def open(self, *_: object, **__: object) -> _Response:
        return self._response


def _source_artifact(payload: bytes) -> dependency_policy.SourceBuildArtifact:
    return dependency_policy.SourceBuildArtifact(
        name="jsonpath",
        version="0.82.2",
        url=(
            "https://files.pythonhosted.org/packages/cf/a1/"
            "jsonpath-0.82.2.tar.gz"
        ),
        sha256=f"sha256:{hashlib.sha256(payload).hexdigest()}",
        size=len(payload),
    )


def test_dependency_policy_exposes_the_closed_source_build_manifest() -> None:
    report = dependency_policy.evaluate_dependency_policy(
        PROJECT_ROOT / "pyproject.toml", PROJECT_ROOT / "uv.lock"
    )

    assert report.source_build_artifacts == (
        dependency_policy.SourceBuildArtifact(
            name="jsonpath",
            version="0.82.2",
            url=(
                "https://files.pythonhosted.org/packages/cf/a1/"
                "693351acd0a9edca4de9153372a65e75398898ea7f8a5c722ab00f464929/"
                "jsonpath-0.82.2.tar.gz"
            ),
            sha256="sha256:d87ef2bcbcded68ee96bc34c1809b69457ecec9b0c4dd471658a12bd391002d1",
            size=10353,
        ),
    )
    assert bootstrap.BUILD_BOOTSTRAP_VERSIONS == {
        "setuptools": "80.9.0",
        "wheel": "0.45.1",
    }


def test_sanitized_environment_removes_ambient_resolver_and_python_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("UV_INDEX", "https://example.invalid/simple")
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", "ambient-managed-python")
    monkeypatch.setenv("PIP_FIND_LINKS", "https://example.invalid/wheels")
    monkeypatch.setenv("PYTHONPATH", "unsafe-path")
    monkeypatch.setenv("PYTHONHOME", "unsafe-home")
    monkeypatch.setenv("VIRTUAL_ENV", "unsafe-venv")
    monkeypatch.setenv("NORTHSTAR_ENV", "test")
    monkeypatch.setenv("NORTHSTAR_DATABASE_URL", "postgresql://secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")

    environment = bootstrap.sanitized_environment(venv=tmp_path / "venv")

    assert "UV_INDEX" not in environment
    assert "UV_PYTHON_INSTALL_DIR" not in environment
    assert "PIP_FIND_LINKS" not in environment
    assert "PYTHONPATH" not in environment
    assert "PYTHONHOME" not in environment
    assert "VIRTUAL_ENV" not in environment
    assert "NORTHSTAR_ENV" not in environment
    assert "NORTHSTAR_DATABASE_URL" not in environment
    assert "HTTPS_PROXY" not in environment
    assert environment["UV_PROJECT_ENVIRONMENT"] == str(tmp_path / "venv")
    managed_environment = bootstrap.sanitized_environment(
        venv=tmp_path / "venv",
        managed_python_dir=tmp_path / "managed-python",
    )
    assert managed_environment["UV_PYTHON_INSTALL_DIR"] == str(tmp_path / "managed-python")


def test_direct_isolated_script_entrypoint_loads_its_exact_sibling_policy() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(PROJECT_ROOT / "scripts" / "ci" / "bootstrap_pep517.py"),
            "--help",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--managed-python-dir" in result.stdout


def test_managed_python_directory_is_rejected_outside_release_profile(tmp_path: Path) -> None:
    with pytest.raises(bootstrap.BootstrapError, match="only valid for release"):
        bootstrap._managed_python_install_dir(
            tmp_path,
            profile=bootstrap._PROFILES["ci"],
        )


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX symlink semantics")
def test_linked_linux_interpreter_must_bind_to_the_managed_python_tree(tmp_path: Path) -> None:
    managed_root = tmp_path / "managed-python"
    home = managed_root / "cpython" / "bin"
    home.mkdir(parents=True)
    managed_python = home / "python"
    managed_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    managed_python.chmod(0o755)
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(managed_python)

    bootstrap._validate_linked_linux_interpreter(
        venv_python,
        {"home": str(home)},
        expected_managed_root=managed_root,
    )

    untrusted_home = tmp_path / "untrusted" / "bin"
    untrusted_home.mkdir(parents=True)
    untrusted_python = untrusted_home / "python"
    untrusted_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    untrusted_python.chmod(0o755)
    venv_python.unlink()
    venv_python.symlink_to(untrusted_python)

    with pytest.raises(bootstrap.BootstrapError, match="outside managed Python"):
        bootstrap._validate_linked_linux_interpreter(
            venv_python,
            {"home": str(untrusted_home)},
            expected_managed_root=managed_root,
        )


def test_managed_python_request_cannot_fall_back_to_a_version_hint(tmp_path: Path) -> None:
    with pytest.raises(bootstrap.BootstrapError, match="absolute regular file"):
        bootstrap._managed_python_request(
            "3.12",
            managed_python_dir=tmp_path,
            target_venv=tmp_path / ".venv",
        )


def test_unmanaged_bootstrap_binds_venv_creation_to_its_base_interpreter(
    tmp_path: Path,
) -> None:
    result = bootstrap._managed_python_request(
        None,
        managed_python_dir=None,
        target_venv=tmp_path / ".venv",
    )

    assert result is not None
    assert Path(result).is_file()
    assert Path(result).resolve(strict=True) != tmp_path / ".venv"


def test_unmanaged_bootstrap_refuses_an_interpreter_inside_the_target_venv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target_venv = tmp_path / ".venv"
    target_python = target_venv / "Scripts" / "python.exe"
    target_python.parent.mkdir(parents=True)
    target_python.write_bytes(b"not an executable interpreter")
    monkeypatch.setattr(bootstrap.sys, "_base_executable", str(target_python), raising=False)
    monkeypatch.setattr(bootstrap.sys, "executable", str(target_python))

    with pytest.raises(bootstrap.BootstrapError, match="outside target venv"):
        bootstrap._managed_python_request(
            None,
            managed_python_dir=None,
            target_venv=target_venv,
        )


def test_development_staging_path_is_a_fresh_sibling(tmp_path: Path) -> None:
    destination = tmp_path / ".venv"
    staged = bootstrap._development_staging_venv(root=tmp_path, destination=destination)

    assert staged.parent == tmp_path
    assert staged != destination
    assert not staged.exists()


def test_development_promotion_keeps_existing_environment_on_switch_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / ".venv"
    destination.mkdir()
    sentinel = destination / "sentinel.txt"
    sentinel.write_text("existing environment", encoding="utf-8")
    staged = tmp_path / ".venv.bootstrap"
    staged.mkdir()
    (staged / "new.txt").write_text("fresh environment", encoding="utf-8")
    original_rename = Path.rename

    def fail_staged_rename(path: Path, target: Path) -> Path:
        if path == staged:
            raise PermissionError("environment is in use")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_staged_rename)

    with pytest.raises(bootstrap.BootstrapError, match="existing environment was preserved"):
        bootstrap._promote_development_venv(staged=staged, destination=destination)

    assert sentinel.read_text(encoding="utf-8") == "existing environment"
    assert staged.is_dir()


def test_verified_source_materializer_streams_and_checks_exact_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"reviewed source archive"
    artifact = _source_artifact(payload)
    monkeypatch.setattr(
        bootstrap,
        "build_opener",
        lambda *_: _Opener(_Response(payload=payload, url=artifact.url)),
    )

    result = bootstrap._download_verified_source(artifact, destination=tmp_path)

    assert result.read_bytes() == payload
    assert result.name == "jsonpath-0.82.2.tar.gz"


def test_verified_source_materializer_rejects_redirect_or_hash_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"reviewed source archive"
    artifact = _source_artifact(payload)
    monkeypatch.setattr(
        bootstrap,
        "build_opener",
        lambda *_: _Opener(_Response(payload=payload, url="https://example.invalid/redirect")),
    )

    with pytest.raises(bootstrap.BootstrapError, match="redirect"):
        bootstrap._download_verified_source(artifact, destination=tmp_path)
    assert not list(tmp_path.iterdir())

    drifted = dependency_policy.SourceBuildArtifact(
        name=artifact.name,
        version=artifact.version,
        url=artifact.url,
        sha256="sha256:" + "0" * 64,
        size=artifact.size,
    )
    monkeypatch.setattr(
        bootstrap,
        "build_opener",
        lambda *_: _Opener(_Response(payload=payload, url=artifact.url)),
    )
    with pytest.raises(bootstrap.BootstrapError, match="SHA-256"):
        bootstrap._download_verified_source(drifted, destination=tmp_path)
    assert not list(tmp_path.iterdir())


def test_ci_venv_refuses_to_reuse_an_existing_environment(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    venv = root / ".venv"
    venv.mkdir()

    with pytest.raises(bootstrap.BootstrapError, match="nonexistent fresh"):
        bootstrap._prepare_venv_path(
            root=root,
            venv=venv,
            profile=bootstrap._PROFILES["ci"],
        )


def test_wheel_stage_and_offline_source_install_have_closed_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        bootstrap,
        "_run",
        lambda command, **_: commands.append(tuple(command)),
    )
    artifact = _source_artifact(b"payload")
    environment = {"UV_PROJECT_ENVIRONMENT": str(tmp_path / "venv")}

    bootstrap._stage_wheel_only_dependencies(
        uv="uv",
        root=tmp_path,
        environment=environment,
        profile=bootstrap._PROFILES["ci"],
        source_artifacts=(artifact,),
        link_mode=None,
    )
    bootstrap._install_offline_source(
        uv="uv",
        root=tmp_path,
        python=tmp_path / "venv" / "python",
        environment=environment,
        source_path=tmp_path / "source.tar.gz",
        editable=False,
    )

    wheel_stage, source_install = commands
    assert "--locked" in wheel_stage
    assert "--no-sources" in wheel_stage
    assert "--no-install-project" in wheel_stage
    assert "--no-build" in wheel_stage
    assert wheel_stage.count("--no-install-package") == 1
    assert wheel_stage[wheel_stage.index("--no-install-package") + 1] == "jsonpath"
    for required in ("--offline", "--no-index", "--no-deps", "--no-build-isolation", "--no-cache"):
        assert required in source_install


def test_final_consistency_check_only_rechecks_the_wheel_stage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        bootstrap,
        "_run",
        lambda command, **_: commands.append(tuple(command)),
    )
    artifact = _source_artifact(b"payload")

    bootstrap._check_final_environment(
        uv="uv",
        root=tmp_path,
        environment={"UV_PROJECT_ENVIRONMENT": str(tmp_path / "venv")},
        profile=bootstrap._PROFILES["release"],
        source_artifacts=(artifact,),
    )

    (command,) = commands
    for required in (
        "--check",
        "--offline",
        "--inexact",
        "--no-install-project",
        "--no-build",
        "--no-install-package",
    ):
        assert required in command
    assert command[command.index("--no-install-package") + 1] == "jsonpath"
