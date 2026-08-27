"""Verify the offline dependency-integrity policy for ``pyproject.toml`` and ``uv.lock``.

The checker intentionally has no network client and only uses the Python standard
library.  It treats the lockfile as a reviewed supply-chain manifest: every
third-party package must come from an allowlisted registry, have complete artifact
metadata and retain the dependency declarations made by the project.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlsplit


# A registry is deliberately coupled to the hosts from which its reviewed
# artifacts may be downloaded.  Adding another registry or mirror requires a
# source review and an explicit change here.
ALLOWED_REGISTRY_ARTIFACT_HOSTS: Mapping[str, frozenset[str]] = {
    "https://pypi.org/simple": frozenset({"files.pythonhosted.org"}),
}
ALLOWED_BUILD_BACKENDS = frozenset({"setuptools.build_meta"})
BUILD_BOOTSTRAP_GROUP = "build-bootstrap"
BUILD_BOOTSTRAP_VERSIONS: Mapping[str, str] = {
    "setuptools": "80.9.0",
    "wheel": "0.45.1",
}
_ALLOWED_BUILD_REQUIREMENTS = frozenset(
    {
        ("setuptools", (), f"=={BUILD_BOOTSTRAP_VERSIONS['setuptools']}", ""),
        ("wheel", (), f"=={BUILD_BOOTSTRAP_VERSIONS['wheel']}", ""),
    }
)
_ALLOWED_SOURCE_BUILD_ARTIFACTS: Mapping[str, tuple[str, str, int, str]] = {
    # jsonpath is the one reviewed source-only transitive dependency.  Its
    # legacy setup.py path is built only after the exact bootstrap wheels have
    # been installed into a fresh, non-system-site virtual environment.
    "jsonpath": (
        "0.82.2",
        "sha256:d87ef2bcbcded68ee96bc34c1809b69457ecec9b0c4dd471658a12bd391002d1",
        10353,
        "https://files.pythonhosted.org/packages/cf/a1/693351acd0a9edca4de9153372a65e75398898ea7f8a5c722ab00f464929/jsonpath-0.82.2.tar.gz",
    ),
}

_PACKAGE_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
_EXTRA_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
_VERSION = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.!+_-]*[A-Za-z0-9])?")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_SPECIFIER = re.compile(r"(?:===|==|!=|<=|>=|~=|<|>)[A-Za-z0-9*+!._-]+")
_UPLOAD_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")
_UNSAFE_REQUIREMENT_SOURCE = re.compile(
    r"(?i)(?:@|git\+|https?://|file:|(?:^|[\s(])[A-Za-z]:[\\/]|(?:^|[\s(])[\\/]|\.\.?[\\/])"
)


class DependencyPolicyError(ValueError):
    """Raised when the local dependency manifest cannot be trusted."""


@dataclass(frozen=True, order=True)
class DependencyInventoryEntry:
    """One reviewed third-party lock entry, safe to include in quality-gate output."""

    name: str
    version: str
    source: str


@dataclass(frozen=True)
class DependencyPolicyReport:
    """Stable, secret-free evidence emitted by a successful policy check."""

    inventory: tuple[DependencyInventoryEntry, ...]
    source_build_artifacts: tuple[SourceBuildArtifact, ...]
    inventory_digest: str
    lock_digest: str

    def render(self) -> str:
        lines = [
            "DEPENDENCY_POLICY_OK",
            f"lock_digest=sha256:{self.lock_digest}",
            f"inventory_digest=sha256:{self.inventory_digest}",
        ]
        lines.extend(
            "package "
            f"name={entry.name} version={entry.version} source={entry.source}"
            for entry in self.inventory
        )
        return "\n".join(lines)


@dataclass(frozen=True, order=True)
class SourceBuildArtifact:
    """One closed, reviewed source-build input used by the bootstrap runner."""

    name: str
    version: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class _Requirement:
    name: str
    extras: tuple[str, ...]
    specifier: str
    marker: str = ""


@dataclass(frozen=True)
class _ProjectManifest:
    name: str
    version: str
    requires_python: str
    dependencies: tuple[_Requirement, ...]
    optional_dependencies: Mapping[str, tuple[_Requirement, ...]]
    build_bootstrap_dependencies: tuple[_Requirement, ...]


@dataclass(frozen=True)
class _LockedPackage:
    name: str
    version: str
    raw: Mapping[str, object]


def _normalize_package_name(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _PACKAGE_NAME.fullmatch(value) is None:
        raise DependencyPolicyError(f"{label} must be a valid package name")
    return re.sub(r"[-_.]+", "-", value).lower()


def _normalize_extra(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _EXTRA_NAME.fullmatch(value) is None:
        raise DependencyPolicyError(f"{label} must be a valid extra name")
    return re.sub(r"[-_.]+", "-", value).lower()


def _require_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\n" in value:
        raise DependencyPolicyError(f"{label} must be non-empty text")
    return value


def _normalize_version(value: object, *, label: str) -> str:
    version = _require_text(value, label=label)
    if _VERSION.fullmatch(version) is None:
        raise DependencyPolicyError(f"{label} must be a concrete package version")
    return version


def _require_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise DependencyPolicyError(f"{label} must be a TOML table")
    return value


def _require_list(value: object, *, label: str) -> list[object]:
    if not isinstance(value, list):
        raise DependencyPolicyError(f"{label} must be a TOML array")
    return value


def _normalize_specifier(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise DependencyPolicyError(f"{label} must be text")
    normalized = re.sub(r"\s+", "", value)
    if normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1]
    if not normalized:
        return ""
    if not all(_SPECIFIER.fullmatch(part) for part in normalized.split(",")):
        raise DependencyPolicyError(f"{label} has an unsupported or unsafe version specifier")
    return normalized


def _normalize_marker(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise DependencyPolicyError(f"{label} must be text")
    normalized = re.sub(r"\s+", " ", value.strip()).replace('"', "'")
    if not normalized:
        return ""
    if _UNSAFE_REQUIREMENT_SOURCE.search(normalized) is not None:
        raise DependencyPolicyError(f"{label} has an unsafe environment marker")
    return normalized


def _parse_requirement(value: object, *, label: str) -> _Requirement:
    requirement = _require_text(value, label=label)
    match = re.fullmatch(
        r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[(?P<extras>[^]]+)\])?(?P<tail>.*)",
        requirement,
    )
    if match is None:
        raise DependencyPolicyError(f"{label} must use a supported requirement form")

    name = _normalize_package_name(match.group("name"), label=label)
    extras_text = match.group("extras")
    extras: tuple[str, ...] = ()
    if extras_text is not None:
        extras = tuple(
            sorted(
                _normalize_extra(extra.strip(), label=f"{label} extra")
                for extra in extras_text.split(",")
            )
        )
        if not extras or len(set(extras)) != len(extras):
            raise DependencyPolicyError(f"{label} has duplicate or empty extras")

    tail = match.group("tail").strip()
    if _UNSAFE_REQUIREMENT_SOURCE.search(tail) is not None:
        raise DependencyPolicyError(f"{label} must not use a direct, VCS, or local source")
    constraint, separator, marker = tail.partition(";")
    if separator and not marker.strip():
        raise DependencyPolicyError(f"{label} has an empty environment marker")
    return _Requirement(
        name=name,
        extras=extras,
        specifier=_normalize_specifier(constraint.strip(), label=f"{label} specifier"),
        marker=_normalize_marker(marker, label=f"{label} marker") if separator else "",
    )


def _read_toml(path: Path, *, label: str) -> tuple[Mapping[str, object], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DependencyPolicyError(f"{label} is unavailable") from exc
    try:
        decoded = raw.decode("utf-8")
        parsed = tomllib.loads(decoded)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise DependencyPolicyError(f"{label} is not valid UTF-8 TOML") from exc
    return _require_mapping(parsed, label=label), raw


def _validate_tool_uv_policy(project: Mapping[str, object]) -> None:
    tool_value = project.get("tool")
    if tool_value is None:
        return
    tool = _require_mapping(tool_value, label="pyproject tool")
    uv_value = tool.get("uv")
    if uv_value is None:
        return
    uv = _require_mapping(uv_value, label="pyproject tool.uv")
    sources_value = uv.get("sources")
    if sources_value is not None:
        sources = _require_mapping(sources_value, label="pyproject tool.uv.sources")
        if sources:
            raise DependencyPolicyError("pyproject must not contain dependency source overrides")

    indexes_value = uv.get("index")
    if indexes_value is None:
        return
    for index in _require_list(indexes_value, label="pyproject tool.uv.index"):
        index_table = _require_mapping(index, label="pyproject tool.uv.index entry")
        url = _require_text(index_table.get("url"), label="pyproject tool.uv.index url")
        if url not in ALLOWED_REGISTRY_ARTIFACT_HOSTS:
            raise DependencyPolicyError("pyproject declares a non-allowlisted registry")


def _validate_build_system_policy(project: Mapping[str, object]) -> None:
    build_system = _require_mapping(project.get("build-system"), label="pyproject build-system")
    allowed_fields = {"requires", "build-backend"}
    if set(build_system) - allowed_fields:
        if "backend-path" in build_system:
            raise DependencyPolicyError("pyproject build-system.backend-path is not permitted")
        raise DependencyPolicyError("pyproject build-system has unreviewed fields")
    requirements = tuple(
        _parse_requirement(item, label="pyproject build-system requirement")
        for item in _require_list(build_system.get("requires"), label="pyproject build-system.requires")
    )
    if not requirements:
        raise DependencyPolicyError("pyproject build-system.requires must not be empty")
    if len(set(requirements)) != len(requirements):
        raise DependencyPolicyError("pyproject build-system.requires has duplicate declarations")
    requirement_identity = {
        (requirement.name, requirement.extras, requirement.specifier, requirement.marker)
        for requirement in requirements
    }
    if requirement_identity != _ALLOWED_BUILD_REQUIREMENTS:
        raise DependencyPolicyError("pyproject build-system.requires has unapproved requirements")
    backend = _require_text(
        build_system.get("build-backend"), label="pyproject build-system.build-backend"
    )
    if backend not in ALLOWED_BUILD_BACKENDS:
        raise DependencyPolicyError("pyproject build-system.build-backend is not allowlisted")


def _parse_build_bootstrap_group(project: Mapping[str, object]) -> tuple[_Requirement, ...]:
    dependency_groups = _require_mapping(
        project.get("dependency-groups"), label="pyproject dependency-groups"
    )
    if set(dependency_groups) != {BUILD_BOOTSTRAP_GROUP}:
        raise DependencyPolicyError(
            "pyproject must declare exactly the approved build-bootstrap dependency group"
        )
    requirements = tuple(
        _parse_requirement(item, label="pyproject build-bootstrap requirement")
        for item in _require_list(
            dependency_groups.get(BUILD_BOOTSTRAP_GROUP),
            label="pyproject dependency-groups.build-bootstrap",
        )
    )
    if not requirements:
        raise DependencyPolicyError("pyproject build-bootstrap dependency group must not be empty")
    if len(set(requirements)) != len(requirements):
        raise DependencyPolicyError(
            "pyproject build-bootstrap dependency group has duplicate declarations"
        )
    requirement_identity = {
        (requirement.name, requirement.extras, requirement.specifier, requirement.marker)
        for requirement in requirements
    }
    if requirement_identity != _ALLOWED_BUILD_REQUIREMENTS:
        raise DependencyPolicyError(
            "pyproject build-bootstrap dependency group has unapproved requirements"
        )
    return requirements


def _parse_project_manifest(project: Mapping[str, object]) -> _ProjectManifest:
    _validate_tool_uv_policy(project)
    _validate_build_system_policy(project)
    build_bootstrap_dependencies = _parse_build_bootstrap_group(project)
    project_table = _require_mapping(project.get("project"), label="pyproject project")
    name = _normalize_package_name(project_table.get("name"), label="pyproject project name")
    version = _normalize_version(project_table.get("version"), label="pyproject project version")
    requires_python = _require_text(
        project_table.get("requires-python"), label="pyproject requires-python"
    )

    dependencies = tuple(
        _parse_requirement(item, label="pyproject dependency")
        for item in _require_list(project_table.get("dependencies", []), label="pyproject dependencies")
    )
    if any(requirement.marker for requirement in dependencies):
        raise DependencyPolicyError(
            "pyproject runtime dependency markers require an explicit policy extension"
        )
    if len(set(dependencies)) != len(dependencies):
        raise DependencyPolicyError("pyproject has duplicate runtime dependency declarations")

    optional_value = project_table.get("optional-dependencies", {})
    optional_table = _require_mapping(optional_value, label="pyproject optional-dependencies")
    optional_dependencies: dict[str, tuple[_Requirement, ...]] = {}
    for raw_group, raw_requirements in optional_table.items():
        group = _normalize_extra(raw_group, label="pyproject optional-dependency group")
        if group in optional_dependencies:
            raise DependencyPolicyError("pyproject has duplicate optional-dependency groups")
        requirements = tuple(
            _parse_requirement(item, label=f"pyproject optional dependency {group}")
            for item in _require_list(
                raw_requirements, label=f"pyproject optional-dependencies.{group}"
            )
        )
        if any(requirement.marker for requirement in requirements):
            raise DependencyPolicyError(
                f"pyproject optional-dependencies.{group} markers require an explicit policy extension"
            )
        if len(set(requirements)) != len(requirements):
            raise DependencyPolicyError(
                f"pyproject optional-dependencies.{group} has duplicate declarations"
            )
        optional_dependencies[group] = requirements

    return _ProjectManifest(
        name=name,
        version=version,
        requires_python=requires_python,
        dependencies=dependencies,
        optional_dependencies=optional_dependencies,
        build_bootstrap_dependencies=build_bootstrap_dependencies,
    )


def _parse_dependency_link(value: object, *, label: str) -> _Requirement:
    table = _require_mapping(value, label=label)
    if set(table) - {"name", "marker", "extra", "extras"} or (
        "extra" in table and "extras" in table
    ):
        raise DependencyPolicyError(f"{label} has unsupported dependency-link fields")
    name = _normalize_package_name(table.get("name"), label=f"{label} name")
    extras_value = table.get("extra", table.get("extras", []))
    extras = tuple(
        sorted(
            _normalize_extra(item, label=f"{label} extra")
            for item in _require_list(extras_value, label=f"{label} extra")
        )
    )
    if len(set(extras)) != len(extras):
        raise DependencyPolicyError(f"{label} has duplicate extras")
    marker = _normalize_marker(table.get("marker", ""), label=f"{label} marker")
    return _Requirement(name=name, extras=extras, specifier="", marker=marker)


def _dependency_links(value: object, *, label: str) -> tuple[_Requirement, ...]:
    return tuple(
        _parse_dependency_link(item, label=label)
        for item in _require_list(value, label=label)
    )


def _optional_dependency_links(value: object, *, label: str) -> Mapping[str, tuple[_Requirement, ...]]:
    table = _require_mapping(value, label=label)
    result: dict[str, tuple[_Requirement, ...]] = {}
    for raw_group, raw_links in table.items():
        group = _normalize_extra(raw_group, label=f"{label} group")
        if group in result:
            raise DependencyPolicyError(f"{label} has duplicate groups")
        result[group] = _dependency_links(raw_links, label=f"{label}.{group}")
    return result


def _validate_source(
    package: _LockedPackage,
    *,
    is_project_root: bool,
) -> str | None:
    source = _require_mapping(package.raw.get("source"), label=f"lock package {package.name} source")
    if is_project_root:
        if source != {"editable": "."}:
            raise DependencyPolicyError("project root must be the sole editable lock package at '.'")
        return None

    if set(source) != {"registry"}:
        raise DependencyPolicyError(
            f"lock package {package.name} must use an allowlisted registry source"
        )
    registry = _require_text(source["registry"], label=f"lock package {package.name} registry")
    if registry not in ALLOWED_REGISTRY_ARTIFACT_HOSTS:
        raise DependencyPolicyError(f"lock package {package.name} uses a non-allowlisted registry")
    return registry


def _validate_artifact(
    value: object,
    *,
    package_name: str,
    artifact_label: str,
    allowed_hosts: frozenset[str],
) -> None:
    artifact = _require_mapping(value, label=f"lock package {package_name} {artifact_label}")
    if set(artifact) != {"url", "hash", "size", "upload-time"}:
        raise DependencyPolicyError(
            f"lock package {package_name} {artifact_label} has incomplete artifact metadata"
        )
    url = _require_text(artifact["url"], label=f"lock package {package_name} {artifact_label} url")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise DependencyPolicyError(
            f"lock package {package_name} {artifact_label} has invalid artifact metadata"
        ) from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or not parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise DependencyPolicyError(
            f"lock package {package_name} {artifact_label} has an unapproved artifact URL"
        )

    digest = artifact["hash"]
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise DependencyPolicyError(
            f"lock package {package_name} {artifact_label} must have an exact SHA-256 hash"
        )
    size = artifact["size"]
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise DependencyPolicyError(
            f"lock package {package_name} {artifact_label} has invalid artifact metadata"
        )
    upload_time = artifact["upload-time"]
    if not isinstance(upload_time, str) or _UPLOAD_TIME.fullmatch(upload_time) is None:
        raise DependencyPolicyError(
            f"lock package {package_name} {artifact_label} has invalid artifact metadata"
        )
    try:
        datetime.fromisoformat(upload_time.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise DependencyPolicyError(
            f"lock package {package_name} {artifact_label} has invalid artifact metadata"
        ) from exc


def _validate_artifacts(package: _LockedPackage, *, registry: str) -> None:
    raw = package.raw
    allowed_hosts = ALLOWED_REGISTRY_ARTIFACT_HOSTS[registry]
    artifact_count = 0
    if "sdist" in raw:
        _validate_artifact(
            raw["sdist"],
            package_name=package.name,
            artifact_label="sdist",
            allowed_hosts=allowed_hosts,
        )
        artifact_count += 1
    if "wheels" in raw:
        wheels = _require_list(raw["wheels"], label=f"lock package {package.name} wheels")
        for index, wheel in enumerate(wheels):
            _validate_artifact(
                wheel,
                package_name=package.name,
                artifact_label=f"wheel[{index}]",
                allowed_hosts=allowed_hosts,
            )
            artifact_count += 1
    if artifact_count == 0:
        raise DependencyPolicyError(f"lock package {package.name} has no reviewed artifacts")


def _source_build_artifacts(
    packages: Sequence[_LockedPackage], *, root: _LockedPackage
) -> tuple[SourceBuildArtifact, ...]:
    """Validate the deliberately closed list of source-only build inputs.

    Normal dependency materialization uses ``uv sync --no-build``.  A source
    distribution therefore cannot enter the venv unless it is listed here and
    later downloaded from this exact lock artifact by the bootstrap runner.
    """

    source_only: dict[str, _LockedPackage] = {}
    for package in packages:
        if package is root:
            continue
        if "sdist" not in package.raw:
            continue
        wheels = _require_list(
            package.raw.get("wheels", []), label=f"lock package {package.name} wheels"
        )
        if not wheels:
            source_only[package.name] = package

    if set(source_only) != set(_ALLOWED_SOURCE_BUILD_ARTIFACTS):
        raise DependencyPolicyError(
            "lock source-only packages do not match the approved source-build manifest"
        )

    artifacts: list[SourceBuildArtifact] = []
    for name, expected in _ALLOWED_SOURCE_BUILD_ARTIFACTS.items():
        package = source_only[name]
        expected_version, expected_hash, expected_size, expected_url = expected
        if package.version != expected_version:
            raise DependencyPolicyError(
                f"lock source-build package {name} has an unapproved version"
            )
        if "wheels" in package.raw:
            raise DependencyPolicyError(
                f"lock source-build package {name} must remain source-only"
            )
        if package.raw.get("dependencies", []) != []:
            raise DependencyPolicyError(
                f"lock source-build package {name} must not declare unreviewed dependencies"
            )
        sdist = _require_mapping(
            package.raw.get("sdist"), label=f"lock source-build package {name} sdist"
        )
        if (
            sdist.get("url") != expected_url
            or sdist.get("hash") != expected_hash
            or sdist.get("size") != expected_size
        ):
            raise DependencyPolicyError(
                f"lock source-build package {name} does not match approved artifact provenance"
            )
        artifacts.append(
            SourceBuildArtifact(
                name=name,
                version=package.version,
                url=expected_url,
                sha256=expected_hash,
                size=expected_size,
            )
        )
    return tuple(sorted(artifacts))


def _validate_build_bootstrap_lock_artifacts(
    *, project: _ProjectManifest, packages: Sequence[_LockedPackage]
) -> None:
    """Ensure each exact PEP 517 builder has a reviewed universal wheel."""

    for requirement in project.build_bootstrap_dependencies:
        expected_version = BUILD_BOOTSTRAP_VERSIONS.get(requirement.name)
        if expected_version is None or requirement.specifier != f"=={expected_version}":
            raise DependencyPolicyError("project build-bootstrap requirement is not exactly pinned")
        matching = [
            package
            for package in packages
            if package.name == requirement.name and package.version == expected_version
        ]
        if len(matching) != 1:
            raise DependencyPolicyError(
                f"lock must contain exactly one exact build-bootstrap package: {requirement.name}"
            )
        wheels = _require_list(
            matching[0].raw.get("wheels"),
            label=f"lock build-bootstrap package {requirement.name} wheels",
        )
        if not wheels:
            raise DependencyPolicyError(
                f"lock build-bootstrap package {requirement.name} requires a reviewed wheel"
            )
        if not any(
            isinstance(wheel, Mapping)
            and isinstance(wheel.get("url"), str)
            and wheel["url"].endswith("-py3-none-any.whl")
            for wheel in wheels
        ):
            raise DependencyPolicyError(
                f"lock build-bootstrap package {requirement.name} requires a universal wheel"
            )


def _validate_dependency_references(packages: Sequence[_LockedPackage]) -> None:
    known_names = {package.name for package in packages}
    for package in packages:
        dependency_values: list[tuple[str, object]] = []
        if "dependencies" in package.raw:
            dependency_values.append(("dependencies", package.raw["dependencies"]))
        if "optional-dependencies" in package.raw:
            optional = _optional_dependency_links(
                package.raw["optional-dependencies"],
                label=f"lock package {package.name} optional-dependencies",
            )
            dependency_values.extend(
                (f"optional-dependencies.{group}", links)
                for group, links in optional.items()
            )
        if "dev-dependencies" in package.raw:
            dev = _optional_dependency_links(
                package.raw["dev-dependencies"],
                label=f"lock package {package.name} dev-dependencies",
            )
            dependency_values.extend(
                (f"dev-dependencies.{group}", links) for group, links in dev.items()
            )

        for label, raw_links in dependency_values:
            links = (
                raw_links
                if isinstance(raw_links, tuple)
                else _dependency_links(raw_links, label=f"lock package {package.name} {label}")
            )
            for link in links:
                if link.name not in known_names:
                    raise DependencyPolicyError(
                        f"lock package {package.name} references a package missing from the lock"
                    )


def _metadata_requirement_counter(value: object, *, label: str) -> Counter[_Requirement]:
    requirements = _require_list(value, label=label)
    parsed: Counter[_Requirement] = Counter()
    for value in requirements:
        table = _require_mapping(value, label=f"{label} entry")
        if set(table) - {"name", "extras", "specifier", "marker"}:
            raise DependencyPolicyError(f"{label} has unsupported source metadata")
        name = _normalize_package_name(
            table.get("name"), label=f"{label} name"
        )
        extras = tuple(
            sorted(
                _normalize_extra(item, label=f"{label} extra")
                for item in _require_list(
                    table.get("extras", []), label=f"{label} extras"
                )
            )
        )
        if len(set(extras)) != len(extras):
            raise DependencyPolicyError(f"{label} has duplicate extras")
        specifier = _normalize_specifier(
            table.get("specifier", ""), label=f"{label} specifier"
        )
        marker = _normalize_marker(
            table.get("marker", ""), label=f"{label} marker"
        )
        parsed[_Requirement(name=name, extras=extras, specifier=specifier, marker=marker)] += 1
    return parsed


def _metadata_requirements(root: _LockedPackage) -> Counter[_Requirement]:
    metadata = _require_mapping(root.raw.get("metadata"), label="lock project metadata")
    return _metadata_requirement_counter(
        metadata.get("requires-dist"), label="lock project metadata.requires-dist"
    )


def _validate_project_lock_alignment(
    project: _ProjectManifest,
    lock: Mapping[str, object],
    root: _LockedPackage,
) -> None:
    if root.name != project.name or root.version != project.version:
        raise DependencyPolicyError("project metadata does not match the lock project package")
    lock_requires_python = _require_text(lock.get("requires-python"), label="lock requires-python")
    if lock_requires_python != project.requires_python:
        raise DependencyPolicyError("project requires-python does not match the lock")

    actual_runtime = Counter(
        _dependency_links(root.raw.get("dependencies", []), label="lock project dependencies")
    )
    expected_runtime = Counter(
        _Requirement(name=item.name, extras=item.extras, specifier="")
        for item in project.dependencies
    )
    if actual_runtime != expected_runtime:
        raise DependencyPolicyError("project runtime dependencies do not match the lock")

    actual_optional = _optional_dependency_links(
        root.raw.get("optional-dependencies", {}), label="lock project optional-dependencies"
    )
    expected_groups = set(project.optional_dependencies)
    if set(actual_optional) != expected_groups:
        raise DependencyPolicyError("project optional-dependency groups do not match the lock")
    for group, expected in project.optional_dependencies.items():
        actual = Counter(actual_optional[group])
        expected_links = Counter(
            _Requirement(name=item.name, extras=item.extras, specifier="") for item in expected
        )
        if actual != expected_links:
            raise DependencyPolicyError(
                f"project optional-dependencies.{group} does not match the lock"
            )

    actual_build_groups = _optional_dependency_links(
        root.raw.get("dev-dependencies", {}), label="lock project dev-dependencies"
    )
    if set(actual_build_groups) != {BUILD_BOOTSTRAP_GROUP}:
        raise DependencyPolicyError("project build-bootstrap group does not match the lock")
    expected_build_links = Counter(
        _Requirement(name=item.name, extras=item.extras, specifier="")
        for item in project.build_bootstrap_dependencies
    )
    if Counter(actual_build_groups[BUILD_BOOTSTRAP_GROUP]) != expected_build_links:
        raise DependencyPolicyError("project build-bootstrap group does not match the lock")

    expected_metadata = Counter(project.dependencies)
    for group, dependencies in project.optional_dependencies.items():
        expected_metadata.update(
            _Requirement(
                name=item.name,
                extras=item.extras,
                specifier=item.specifier,
                marker=f"extra == '{group}'",
            )
            for item in dependencies
        )
    if _metadata_requirements(root) != expected_metadata:
        raise DependencyPolicyError("project dependency specifiers do not match the lock metadata")

    metadata = _require_mapping(root.raw.get("metadata"), label="lock project metadata")
    metadata_build_groups = _require_mapping(
        metadata.get("requires-dev"), label="lock project metadata.requires-dev"
    )
    if set(metadata_build_groups) != {BUILD_BOOTSTRAP_GROUP}:
        raise DependencyPolicyError("project build-bootstrap metadata does not match the lock")
    actual_build_metadata = _metadata_requirement_counter(
        metadata_build_groups[BUILD_BOOTSTRAP_GROUP],
        label="lock project metadata.requires-dev.build-bootstrap",
    )
    if actual_build_metadata != Counter(project.build_bootstrap_dependencies):
        raise DependencyPolicyError("project build-bootstrap metadata does not match the lock")


def _parse_locked_packages(lock: Mapping[str, object]) -> tuple[_LockedPackage, ...]:
    packages = _require_list(lock.get("package"), label="lock package")
    if not packages:
        raise DependencyPolicyError("lock must contain at least one package")
    parsed: list[_LockedPackage] = []
    identities: set[tuple[str, str]] = set()
    for value in packages:
        table = _require_mapping(value, label="lock package entry")
        name = _normalize_package_name(table.get("name"), label="lock package name")
        version = _normalize_version(table.get("version"), label=f"lock package {name} version")
        identity = (name, version)
        if identity in identities:
            raise DependencyPolicyError("lock has duplicate package/version entries")
        identities.add(identity)
        parsed.append(_LockedPackage(name=name, version=version, raw=table))
    return tuple(parsed)


def _require_single_critical_lock_entries(packages: Sequence[_LockedPackage]) -> None:
    """Prevent duplicate build/source identities from shadowing reviewed entries."""

    counts = Counter(package.name for package in packages)
    for name in (*BUILD_BOOTSTRAP_VERSIONS, *_ALLOWED_SOURCE_BUILD_ARTIFACTS):
        if counts[name] != 1:
            raise DependencyPolicyError(
                f"lock must contain exactly one critical package entry: {name}"
            )


def _inventory_digest(inventory: Sequence[DependencyInventoryEntry]) -> str:
    canonical = "".join(f"{item.name}\t{item.version}\t{item.source}\n" for item in inventory)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluate_dependency_policy(
    pyproject_path: Path,
    lock_path: Path,
) -> DependencyPolicyReport:
    """Fail closed unless the local project and lock satisfy the policy.

    No package manager command is launched here: callers can safely use it in an
    offline quality-gate preflight before installing anything.
    """

    pyproject, _ = _read_toml(pyproject_path, label="pyproject.toml")
    lock, lock_bytes = _read_toml(lock_path, label="uv.lock")
    project = _parse_project_manifest(pyproject)
    packages = _parse_locked_packages(lock)
    _require_single_critical_lock_entries(packages)
    roots = [package for package in packages if package.name == project.name]
    if len(roots) != 1:
        raise DependencyPolicyError("lock must contain exactly one project root package")
    root = roots[0]

    inventory: list[DependencyInventoryEntry] = []
    for package in packages:
        registry = _validate_source(package, is_project_root=package is root)
        if registry is None:
            continue
        _validate_artifacts(package, registry=registry)
        inventory.append(
            DependencyInventoryEntry(name=package.name, version=package.version, source=registry)
        )

    _validate_dependency_references(packages)
    _validate_project_lock_alignment(project, lock, root)
    _validate_build_bootstrap_lock_artifacts(project=project, packages=packages)
    source_build_artifacts = _source_build_artifacts(packages, root=root)
    inventory.sort()
    inventory_tuple = tuple(inventory)
    return DependencyPolicyReport(
        inventory=inventory_tuple,
        source_build_artifacts=source_build_artifacts,
        inventory_digest=_inventory_digest(inventory_tuple),
        lock_digest=hashlib.sha256(lock_bytes).hexdigest(),
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="verify the offline uv dependency policy")
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--lock", type=Path, default=Path("uv.lock"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        report = evaluate_dependency_policy(args.pyproject, args.lock)
    except DependencyPolicyError as exc:
        print(f"DEPENDENCY_POLICY_FAILED: {exc}", file=sys.stderr)
        return 1
    print(report.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
