#!/usr/bin/env python3
"""Materialize Northstar's reviewed PEP 517 inputs in a fresh virtualenv.

This is intentionally a standard-library-only entry point.  It is the single
place allowed to download dependencies for local development, CI, and the
Linux release installer.  All later ``uv run`` calls must use ``--no-sync``.

The normal locked sync refuses every source build.  The sole reviewed
source-only dependency is then downloaded directly from the artifact recorded
in ``uv.lock``, size/hash checked while streaming, and installed offline with
the exact build bootstrap already in the new virtual environment.  The local
project is installed in the same offline/no-index/no-isolation boundary.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

def _load_sibling_dependency_policy() -> ModuleType:
    """Load the signed sibling without adding its directory to ``sys.path``.

    ``python -I path/to/bootstrap_pep517.py`` intentionally removes the script
    directory from import search paths.  Release installation uses that form,
    so use the exact sibling file rather than weakening isolated mode.
    """

    runner = Path(__file__)
    policy = runner.with_name("check_dependency_policy.py")
    if runner.is_symlink() or policy.is_symlink() or not policy.is_file():
        raise RuntimeError("hermetic bootstrap dependency policy sibling is unavailable")
    spec = importlib.util.spec_from_file_location(
        "_northstar_bootstrap_dependency_policy", policy
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("hermetic bootstrap dependency policy sibling cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


if __package__:
    from . import check_dependency_policy as dependency_policy
else:  # pragma: no cover - exercised through direct ``python -I`` invocation
    dependency_policy = _load_sibling_dependency_policy()


PROJECT_VENV_NAME: Final = ".venv"
BUILD_BOOTSTRAP_GROUP: Final = dependency_policy.BUILD_BOOTSTRAP_GROUP
BUILD_BOOTSTRAP_VERSIONS: Final = dependency_policy.BUILD_BOOTSTRAP_VERSIONS
_SAFE_ENVIRONMENT_NAMES: Final = frozenset(
    {
        "APPDATA",
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "WINDIR",
    }
)
_DOWNLOAD_CHUNK_BYTES: Final = 64 * 1024
_DOWNLOAD_TIMEOUT_SECONDS: Final = 30


class BootstrapError(RuntimeError):
    """Raised when a hermetic build boundary cannot be established."""


@dataclass(frozen=True)
class BootstrapProfile:
    """The lock selections and local-project form for one environment type."""

    name: str
    extras: tuple[str, ...]
    no_dev: bool
    editable_project: bool


_PROFILES: Final = {
    "development": BootstrapProfile(
        name="development", extras=("dev",), no_dev=False, editable_project=True
    ),
    "ci": BootstrapProfile(name="ci", extras=("dev",), no_dev=False, editable_project=True),
    "release": BootstrapProfile(name="release", extras=(), no_dev=True, editable_project=False),
}


class _NoRedirect(HTTPRedirectHandler):
    """Reject artifact redirects instead of silently trusting another host."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def _project_root(value: Path) -> Path:
    try:
        root = value.resolve(strict=True)
    except OSError as exc:
        raise BootstrapError("project root cannot be resolved") from exc
    if not root.is_dir() or root.is_symlink():
        raise BootstrapError("project root must be a regular directory")
    for required in (root / "pyproject.toml", root / "uv.lock"):
        if not required.is_file() or required.is_symlink():
            raise BootstrapError(f"required project input is unavailable: {required.name}")
    return root


def _resolve_venv_path(*, root: Path, requested: Path | None, profile: BootstrapProfile) -> Path:
    if requested is None:
        if profile.name == "release":
            raise BootstrapError("release bootstrap requires an explicit fresh --venv path")
        return root / PROJECT_VENV_NAME
    candidate = requested if requested.is_absolute() else root / requested
    try:
        return candidate.resolve(strict=False)
    except OSError as exc:
        raise BootstrapError("virtual environment path cannot be resolved") from exc


def _prepare_venv_path(*, root: Path, venv: Path, profile: BootstrapProfile) -> None:
    """Validate the only permitted replacement target before ``uv venv``.

    Development only ever promotes a fully built sibling into the repository
    ``.venv``.  A release venv must be a new, service-owned path prepared by
    the installer; this script never clears an arbitrary supplied directory.
    """

    if venv == root or root not in venv.parents:
        if profile.name == "development" or not venv.is_absolute():
            raise BootstrapError("virtual environment path is outside the permitted boundary")
    if venv.is_symlink():
        raise BootstrapError("virtual environment path must not be a symbolic link")
    if profile.name == "development":
        expected = root / PROJECT_VENV_NAME
        if venv != expected:
            raise BootstrapError("development bootstrap may replace only the repository .venv")
        if venv.exists() and not venv.is_dir():
            raise BootstrapError("repository .venv is not a directory")
        return

    if profile.name == "ci":
        if venv.exists():
            raise BootstrapError("CI bootstrap requires a nonexistent fresh virtual environment")
        parent = venv.parent
        if not parent.is_dir() or parent.is_symlink():
            raise BootstrapError("CI virtual environment parent is not a regular directory")
        return

    if venv.exists():
        raise BootstrapError("release bootstrap requires a nonexistent fresh virtual environment")
    parent = venv.parent
    if not parent.is_dir() or parent.is_symlink():
        raise BootstrapError("release virtual environment parent is not a regular directory")
    return


def _unique_sibling_path(*, destination: Path, label: str) -> Path:
    """Return an unused same-volume sibling without creating or replacing it."""

    for _ in range(32):
        candidate = destination.parent / f".{destination.name}.{label}-{secrets.token_hex(12)}"
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
    raise BootstrapError("cannot allocate a fresh sibling virtual environment path")


def _development_staging_venv(*, root: Path, destination: Path) -> Path:
    if destination.parent != root:
        raise BootstrapError("development staging virtual environment escaped project root")
    return _unique_sibling_path(destination=destination, label="bootstrap")


def _promote_development_venv(*, staged: Path, destination: Path) -> None:
    """Atomically replace the generated development venv only after success."""

    if (
        staged.parent != destination.parent
        or not staged.is_dir()
        or staged.is_symlink()
        or destination.is_symlink()
    ):
        raise BootstrapError("development virtual environment promotion boundary is invalid")

    previous: Path | None = None
    try:
        if destination.exists():
            previous = _unique_sibling_path(destination=destination, label="previous")
            destination.rename(previous)
        staged.rename(destination)
    except OSError as exc:
        if previous is not None and previous.exists() and not destination.exists():
            try:
                previous.rename(destination)
            except OSError as restore_exc:
                raise BootstrapError(
                    "development virtual environment promotion failed and original environment "
                    f"could not be restored; staged environment retained at {staged}"
                ) from restore_exc
        raise BootstrapError(
            "development virtual environment is in use or cannot be atomically promoted; "
            f"existing environment was preserved and staged environment retained at {staged}"
        ) from exc

    if previous is not None:
        try:
            shutil.rmtree(previous)
        except OSError:
            print(
                f"HERMETIC_PEP517_BOOTSTRAP_WARNING previous environment retained at {previous}",
                file=sys.stderr,
            )


def _managed_python_install_dir(
    value: Path | None, *, profile: BootstrapProfile
) -> Path | None:
    """Accept only the installer-owned Python tree for a Linux release."""

    if value is None:
        return None
    if profile.name != "release":
        raise BootstrapError("managed Python directory is only valid for release bootstrap")
    if value.is_symlink():
        raise BootstrapError("managed Python directory must not be a symbolic link")
    try:
        directory = value.resolve(strict=True)
        metadata = directory.stat()
    except OSError as exc:
        raise BootstrapError("managed Python directory cannot be resolved") from exc
    if not directory.is_dir():
        raise BootstrapError("managed Python directory is not a directory")
    if os.name != "nt" and (metadata.st_uid != 0 or metadata.st_mode & 0o022):
        raise BootstrapError("managed Python directory is not root-owned and non-writable")
    return directory


def _managed_python_request(
    value: str | None, *, managed_python_dir: Path | None, target_venv: Path
) -> str | None:
    """Bind release venv creation to one verified executable, not a version hint."""

    if managed_python_dir is None:
        if value is not None:
            return value
        target = target_venv.resolve(strict=False)
        candidates = (getattr(sys, "_base_executable", None), sys.executable)
        for raw_candidate in candidates:
            if not isinstance(raw_candidate, str) or not raw_candidate:
                continue
            try:
                current = Path(raw_candidate).resolve(strict=True)
            except OSError:
                continue
            if not current.is_file() or not os.access(current, os.X_OK):
                continue
            if current == target or target in current.parents:
                continue
            return str(current)
        raise BootstrapError("bootstrap base interpreter cannot be resolved outside target venv")
    if value is None:
        raise BootstrapError("release bootstrap requires an explicit managed Python executable")
    candidate = Path(value)
    if not candidate.is_absolute() or candidate.is_symlink():
        raise BootstrapError("managed Python executable must be an absolute regular file")
    try:
        executable = candidate.resolve(strict=True)
        metadata = executable.stat()
    except OSError as exc:
        raise BootstrapError("managed Python executable cannot be resolved") from exc
    if (
        not executable.is_file()
        or not os.access(executable, os.X_OK)
        or managed_python_dir not in executable.parents
    ):
        raise BootstrapError("managed Python executable is outside the approved directory")
    if os.name != "nt" and (metadata.st_uid != 0 or metadata.st_mode & 0o022):
        raise BootstrapError("managed Python executable is not root-owned and non-writable")
    return str(executable)


def sanitized_environment(
    *, venv: Path, managed_python_dir: Path | None = None
) -> dict[str, str]:
    """Use a minimal OS environment for every resolver and build subprocess."""

    result: dict[str, str] = {}
    for key in _SAFE_ENVIRONMENT_NAMES:
        value = os.environ.get(key)
        if value:
            result[key] = value
    if os.name == "nt":
        system_root = result.get("SYSTEMROOT") or result.get("WINDIR")
        if not system_root:
            raise BootstrapError("Windows bootstrap requires a system root")
        result["PATH"] = os.pathsep.join((str(Path(system_root) / "System32"), system_root))
    else:
        result["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    result["UV_PROJECT_ENVIRONMENT"] = str(venv)
    if managed_python_dir is not None:
        result["UV_PYTHON_INSTALL_DIR"] = str(managed_python_dir)
    return result


def _uv_executable() -> str:
    executable = shutil.which("uv")
    if executable is None:
        raise BootstrapError("uv is required for the hermetic PEP 517 bootstrap")
    return executable


def _venv_configuration(venv: Path) -> dict[str, str]:
    """Read the tiny venv configuration without accepting duplicate keys."""

    configuration = venv / "pyvenv.cfg"
    try:
        values: dict[str, str] = {}
        for raw_line in configuration.read_text(encoding="utf-8").splitlines():
            if "=" not in raw_line:
                continue
            key, value = raw_line.split("=", 1)
            normalized_key = key.strip().casefold()
            if not normalized_key or normalized_key in values:
                raise BootstrapError("fresh virtual environment configuration is invalid")
            values[normalized_key] = value.strip()
    except (OSError, UnicodeError) as exc:
        raise BootstrapError("fresh virtual environment configuration cannot be read") from exc
    return values


def _validate_linked_linux_interpreter(
    candidate: Path,
    configuration: Mapping[str, str],
    *,
    expected_managed_root: Path | None,
) -> None:
    """Permit uv's normal base-Python link without trusting an arbitrary target."""

    if candidate.parent.name != "bin":
        raise BootstrapError("fresh virtual environment interpreter link is invalid")
    home_value = configuration.get("home")
    if not home_value:
        raise BootstrapError("fresh virtual environment interpreter home is unavailable")
    home = Path(home_value)
    if not home.is_absolute() or home.is_symlink():
        raise BootstrapError("fresh virtual environment interpreter home is invalid")
    try:
        resolved_home = home.resolve(strict=True)
        target = candidate.resolve(strict=True)
    except OSError as exc:
        raise BootstrapError("fresh virtual environment interpreter link cannot be resolved") from exc
    if not resolved_home.is_dir() or not target.is_file() or not os.access(target, os.X_OK):
        raise BootstrapError("fresh virtual environment interpreter target is invalid")
    if target.parent != resolved_home:
        raise BootstrapError("fresh virtual environment interpreter target is outside its home")
    if expected_managed_root is not None and expected_managed_root not in target.parents:
        raise BootstrapError("fresh virtual environment interpreter target is outside managed Python")


def _validate_venv_interpreter_identity(
    *,
    candidate: Path,
    venv: Path,
    environment: Mapping[str, str],
    expected_managed_root: Path | None,
) -> None:
    code = (
        "import json, sys; "
        "print(json.dumps({'prefix': sys.prefix, 'base_prefix': sys.base_prefix}))"
    )
    try:
        result = subprocess.run(
            [str(candidate), "-I", "-c", code],
            env=dict(environment),
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        prefix = Path(payload["prefix"]).resolve(strict=True)
        base_prefix = Path(payload["base_prefix"]).resolve(strict=True)
        expected_prefix = venv.resolve(strict=True)
    except (OSError, subprocess.CalledProcessError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise BootstrapError("fresh virtual environment interpreter cannot be verified") from exc
    if prefix != expected_prefix or base_prefix == prefix:
        raise BootstrapError("fresh virtual environment interpreter is not isolated")
    if expected_managed_root is not None and expected_managed_root not in base_prefix.parents:
        raise BootstrapError("fresh virtual environment interpreter is outside managed Python")


def _venv_python(
    *, venv: Path, environment: Mapping[str, str], expected_managed_root: Path | None
) -> Path:
    configuration = _venv_configuration(venv)
    _require_non_system_site_venv(venv)
    candidates = (venv / "Scripts" / "python.exe", venv / "bin" / "python")
    for candidate in candidates:
        if candidate.is_symlink():
            _validate_linked_linux_interpreter(
                candidate,
                configuration,
                expected_managed_root=expected_managed_root,
            )
        elif candidate.is_file() and os.access(candidate, os.X_OK):
            pass
        else:
            continue
        _validate_venv_interpreter_identity(
            candidate=candidate,
            venv=venv,
            environment=environment,
            expected_managed_root=expected_managed_root,
        )
        if candidate.exists():
            return candidate
    raise BootstrapError("fresh virtual environment has no verified Python interpreter")


def _run(command: Sequence[str], *, root: Path, environment: Mapping[str, str]) -> None:
    try:
        subprocess.run(list(command), cwd=root, env=dict(environment), check=True)
    except FileNotFoundError as exc:
        raise BootstrapError(f"bootstrap command is unavailable: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise BootstrapError(f"bootstrap command failed with exit status {exc.returncode}") from exc


def _installed_distributions(*, python: Path, root: Path, environment: Mapping[str, str]) -> dict[str, str]:
    code = (
        "import importlib.metadata as metadata, json; "
        "print(json.dumps({distribution.metadata['Name'].lower().replace('_', '-'): "
        "distribution.version for distribution in metadata.distributions() "
        "if distribution.metadata.get('Name')}, sort_keys=True))"
    )
    try:
        result = subprocess.run(
            [str(python), "-I", "-c", code],
            cwd=root,
            env=dict(environment),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BootstrapError("cannot inspect the fresh virtual environment") from exc
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BootstrapError("fresh virtual environment inventory is invalid") from exc
    if not isinstance(payload, dict) or not all(
        isinstance(name, str) and isinstance(version, str) for name, version in payload.items()
    ):
        raise BootstrapError("fresh virtual environment inventory is invalid")
    return payload


def _require_non_system_site_venv(venv: Path) -> None:
    values = _venv_configuration(venv)
    if values.get("include-system-site-packages", "").casefold() != "false":
        raise BootstrapError("fresh virtual environment must not expose system site packages")


def _validate_stage_inventory(
    *,
    python: Path,
    root: Path,
    venv: Path,
    environment: Mapping[str, str],
    report: dependency_policy.DependencyPolicyReport,
    allow_project: bool,
) -> None:
    _require_non_system_site_venv(venv)
    installed = _installed_distributions(python=python, root=root, environment=environment)
    allowed = {entry.name for entry in report.inventory}
    if allow_project:
        allowed.add("northstar-quant")
    unknown = sorted(set(installed) - allowed)
    if unknown:
        raise BootstrapError("fresh virtual environment contains an unreviewed distribution")
    if not allow_project and "northstar-quant" in installed:
        raise BootstrapError("project must not be installed before offline source installation")
    for name, version in BUILD_BOOTSTRAP_VERSIONS.items():
        if installed.get(name) != version:
            raise BootstrapError("fresh virtual environment lacks the exact locked build bootstrap")


def _artifact_filename(artifact: dependency_policy.SourceBuildArtifact) -> str:
    if not artifact.name.replace("-", "").isalnum() or not artifact.version.replace(
        ".", ""
    ).replace("-", "").isalnum():
        raise BootstrapError("approved source-build artifact has an unsafe identity")
    return f"{artifact.name}-{artifact.version}.tar.gz"


def _download_verified_source(
    artifact: dependency_policy.SourceBuildArtifact, *, destination: Path
) -> Path:
    parsed = urlsplit(artifact.url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "files.pythonhosted.org"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise BootstrapError("approved source-build artifact URL is unsafe")
    output = destination / _artifact_filename(artifact)
    digest = hashlib.sha256()
    total = 0
    opener = build_opener(_NoRedirect(), ProxyHandler({}))
    request = Request(artifact.url, headers={"User-Agent": "northstar-quant-bootstrap/1"})
    try:
        with opener.open(request, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response, output.open(
            "xb"
        ) as handle:
            if getattr(response, "status", None) != 200 or response.geturl() != artifact.url:
                raise BootstrapError("source-build artifact redirect or response was rejected")
            while chunk := response.read(_DOWNLOAD_CHUNK_BYTES):
                total += len(chunk)
                if total > artifact.size:
                    raise BootstrapError("source-build artifact exceeds the locked size")
                digest.update(chunk)
                handle.write(chunk)
    except (HTTPError, URLError, OSError) as exc:
        output.unlink(missing_ok=True)
        raise BootstrapError("source-build artifact download failed") from exc
    except BootstrapError:
        output.unlink(missing_ok=True)
        raise
    if total != artifact.size or f"sha256:{digest.hexdigest()}" != artifact.sha256:
        output.unlink(missing_ok=True)
        raise BootstrapError("source-build artifact does not match locked size or SHA-256")
    return output


def _stage_wheel_only_dependencies(
    *,
    uv: str,
    root: Path,
    environment: Mapping[str, str],
    profile: BootstrapProfile,
    source_artifacts: Sequence[dependency_policy.SourceBuildArtifact],
    link_mode: str | None,
) -> None:
    command = [
        uv,
        "sync",
        "--no-config",
        "--directory",
        str(root),
        "--locked",
        "--no-sources",
        "--no-install-project",
        "--no-build",
        "--no-cache",
        "--group",
        BUILD_BOOTSTRAP_GROUP,
    ]
    if profile.no_dev:
        command.append("--no-dev")
    if not profile.editable_project:
        command.append("--no-editable")
    for extra in profile.extras:
        command.extend(("--extra", extra))
    for artifact in source_artifacts:
        command.extend(("--no-install-package", artifact.name))
    if link_mode is not None:
        command.extend(("--link-mode", link_mode))
    _run(command, root=root, environment=environment)


def _install_offline_source(
    *,
    uv: str,
    root: Path,
    python: Path,
    environment: Mapping[str, str],
    source_path: Path,
    editable: bool,
) -> None:
    command = [
        uv,
        "pip",
        "install",
        "--no-config",
        "--python",
        str(python),
        "--offline",
        "--no-index",
        "--no-deps",
        "--no-build-isolation",
        "--no-cache",
    ]
    if editable:
        command.extend(("--editable", str(source_path)))
    else:
        command.append(str(source_path))
    _run(command, root=root, environment=environment)


def _copy_noneditable_project_source(*, root: Path, destination: Path) -> Path:
    """Copy only declared package-build inputs away from a sealed release tree."""

    project_copy = destination / "northstar-project"
    required_files = ("pyproject.toml", "README.md")
    try:
        project_copy.mkdir(mode=0o700)
        for filename in required_files:
            source = root / filename
            if not source.is_file() or source.is_symlink():
                raise BootstrapError(f"project build input is unavailable: {filename}")
            shutil.copy2(source, project_copy / filename, follow_symlinks=False)
        source_tree = root / "src"
        if not source_tree.is_dir() or source_tree.is_symlink():
            raise BootstrapError("project build input is unavailable: src")
        for candidate in source_tree.rglob("*"):
            if candidate.is_symlink():
                raise BootstrapError("project source tree contains a symbolic link")
        shutil.copytree(source_tree, project_copy / "src", symlinks=False)
    except BootstrapError:
        raise
    except OSError as exc:
        raise BootstrapError("cannot copy sealed project build inputs into private scratch") from exc
    return project_copy


def _check_final_environment(
    *,
    uv: str,
    root: Path,
    environment: Mapping[str, str],
    profile: BootstrapProfile,
    source_artifacts: Sequence[dependency_policy.SourceBuildArtifact],
) -> None:
    """Recheck the lock-governed wheel stage after private source installs.

    The source-only artifact and local project intentionally originate from a
    private verified path, so a whole-environment ``uv sync --check`` would
    incorrectly attempt to replace them with indexed locations.  Their
    provenance is instead enforced by the streamed hash check, offline install
    arguments, and final inventory validation above.
    """

    command = [
        uv,
        "sync",
        "--no-config",
        "--directory",
        str(root),
        "--locked",
        "--no-sources",
        "--check",
        "--offline",
        "--inexact",
        "--no-install-project",
        "--no-build",
        "--group",
        BUILD_BOOTSTRAP_GROUP,
    ]
    if profile.no_dev:
        command.append("--no-dev")
    if not profile.editable_project:
        command.append("--no-editable")
    for extra in profile.extras:
        command.extend(("--extra", extra))
    for artifact in source_artifacts:
        command.extend(("--no-install-package", artifact.name))
    _run(command, root=root, environment=environment)


def bootstrap_environment(
    *,
    project_root: Path,
    profile_name: str,
    requested_venv: Path | None = None,
    link_mode: str | None = None,
    python_request: str | None = None,
    managed_python_dir: Path | None = None,
    run_command: Callable[[Sequence[str], Path, Mapping[str, str]], None] | None = None,
) -> None:
    """Build a fresh trusted venv, or fail before any project/source build.

    ``run_command`` is an intentionally narrow test seam.  Production calls
    retain the real subprocess implementation above.
    """

    try:
        profile = _PROFILES[profile_name]
    except KeyError as exc:
        raise BootstrapError("unknown hermetic bootstrap profile") from exc
    root = _project_root(project_root)
    report = dependency_policy.evaluate_dependency_policy(root / "pyproject.toml", root / "uv.lock")
    source_artifacts = report.source_build_artifacts
    destination_venv = _resolve_venv_path(root=root, requested=requested_venv, profile=profile)
    _prepare_venv_path(root=root, venv=destination_venv, profile=profile)
    venv = (
        _development_staging_venv(root=root, destination=destination_venv)
        if profile.name == "development"
        else destination_venv
    )
    managed_python = _managed_python_install_dir(managed_python_dir, profile=profile)
    trusted_python_request = _managed_python_request(
        python_request,
        managed_python_dir=managed_python,
        target_venv=venv,
    )
    environment = sanitized_environment(venv=venv, managed_python_dir=managed_python)
    uv = _uv_executable()

    def invoke(command: Sequence[str]) -> None:
        if run_command is None:
            _run(command, root=root, environment=environment)
        else:
            run_command(command, root, environment)

    invoke(
        [
            uv,
            "lock",
            "--no-config",
            "--directory",
            str(root),
            "--check",
            "--offline",
        ]
    )

    # Development promotes a sibling to ``.venv`` and release archives a
    # build venv into its final location. Console-script launchers must
    # survive either relocation.
    venv_command = [
        uv,
        "venv",
        "--no-config",
        "--no-project",
        "--no-python-downloads",
        "--relocatable",
    ]
    if trusted_python_request is not None:
        venv_command.extend(("--python", trusted_python_request))
    venv_command.append(str(venv))
    invoke(venv_command)

    if run_command is not None:
        return

    python = _venv_python(
        venv=venv,
        environment=environment,
        expected_managed_root=managed_python,
    )
    _stage_wheel_only_dependencies(
        uv=uv,
        root=root,
        environment=environment,
        profile=profile,
        source_artifacts=source_artifacts,
        link_mode=link_mode,
    )
    _validate_stage_inventory(
        python=python,
        root=root,
        venv=venv,
        environment=environment,
        report=report,
        allow_project=False,
    )
    try:
        with tempfile.TemporaryDirectory(prefix=".northstar-pep517-", dir=venv) as temporary_name:
            temporary_directory = Path(temporary_name)
            for artifact in source_artifacts:
                source_path = _download_verified_source(artifact, destination=temporary_directory)
                _install_offline_source(
                    uv=uv,
                    root=root,
                    python=python,
                    environment=environment,
                    source_path=source_path,
                    editable=False,
                )
            project_source = (
                root
                if profile.editable_project
                else _copy_noneditable_project_source(root=root, destination=temporary_directory)
            )
            _install_offline_source(
                uv=uv,
                root=root,
                python=python,
                environment=environment,
                source_path=project_source,
                editable=profile.editable_project,
            )
    except OSError as exc:
        raise BootstrapError("cannot create private source-build scratch directory") from exc
    _validate_stage_inventory(
        python=python,
        root=root,
        venv=venv,
        environment=environment,
        report=report,
        allow_project=True,
    )
    _check_final_environment(
        uv=uv,
        root=root,
        environment=environment,
        profile=profile,
        source_artifacts=source_artifacts,
    )
    if profile.name == "development":
        _promote_development_venv(staged=venv, destination=destination_venv)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="bootstrap a hermetic Northstar PEP 517 venv")
    parser.add_argument("--profile", choices=tuple(sorted(_PROFILES)), default="development")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--venv", type=Path)
    parser.add_argument("--link-mode", choices=("clone", "copy", "hardlink", "symlink"))
    parser.add_argument("--python", dest="python_request")
    parser.add_argument("--managed-python-dir", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        bootstrap_environment(
            project_root=args.project_root,
            profile_name=args.profile,
            requested_venv=args.venv,
            link_mode=args.link_mode,
            python_request=args.python_request,
            managed_python_dir=args.managed_python_dir,
        )
    except (BootstrapError, dependency_policy.DependencyPolicyError) as exc:
        print(f"HERMETIC_PEP517_BOOTSTRAP_FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"HERMETIC_PEP517_BOOTSTRAP_OK profile={args.profile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
