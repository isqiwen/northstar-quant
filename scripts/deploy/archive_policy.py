"""Fail-closed policy for deployment archive creation and root extraction.

Deployment artifacts and the temporary Linux control archive are distinct
archives, but both cross a trust boundary. Keep their exclusion semantics in
one place so a newly added credential filename cannot be filtered in only one
of the two packaging paths.

The runtime artifact crosses an additional privilege boundary: it is unpacked
by root on the Linux target.  Before that can happen, this module validates
the complete tar member stream with fixed limits.  The validator intentionally
accepts only the regular files and directories emitted by ``package.py``;
links, device nodes, sparse members, special modes, ambiguous paths and
unbounded expansion are all rejected.
"""

from __future__ import annotations

import argparse
import stat
import tarfile
from pathlib import Path
from typing import Final, NoReturn


_CACHE_DIRECTORY_NAMES: Final = frozenset({"__pycache__", ".mypy_cache", ".pytest_cache"})
_GENERATED_SUFFIXES: Final = frozenset({".pyc", ".pyo"})
_CREDENTIAL_DIRECTORY_NAMES: Final = frozenset(
    {"secret", "secrets", ".secret", ".secrets", "credential", "credentials", ".credentials"}
)
_CREDENTIAL_FILE_STEMS: Final = frozenset({"secret", "secrets", "credential", "credentials"})
_CREDENTIAL_SUFFIXES: Final = frozenset(
    {
        ".der",
        ".jks",
        ".kdb",
        ".key",
        ".keystore",
        ".p12",
        ".p8",
        ".pem",
        ".pfx",
        ".pkcs12",
    }
)

# These limits apply to the *uncompressed* payload which root will extract.
# The artifact handoff separately caps the compressed stream at 4 GiB.  Keep
# this independent cap here so an otherwise small gzip stream cannot consume
# arbitrary disk space or CPU during extraction.
MAX_DEPLOYMENT_ARTIFACT_MEMBERS: Final = 10_000
MAX_DEPLOYMENT_ARTIFACT_MEMBER_BYTES: Final = 512 * 1024 * 1024
MAX_DEPLOYMENT_ARTIFACT_UNPACKED_BYTES: Final = 2 * 1024 * 1024 * 1024

_ARTIFACT_FIXED_FILES: Final = frozenset(
    {
        "pyproject.toml",
        "README.md",
        "uv.lock",
        "alembic.ini",
        "DEPLOY_ARTIFACT_META.txt",
        "scripts/ci/check_dependency_policy.py",
        "scripts/ci/bootstrap_pep517.py",
    }
)
_ARTIFACT_DIRECTORY_ROOTS: Final = frozenset(
    {
        "alembic",
        "configs",
        "src",
        "templates",
        "ontology",
        "datasets",
    }
)
_ARTIFACT_DIRECTORY_NAMES: Final = _ARTIFACT_DIRECTORY_ROOTS | frozenset(
    {"infra", "infra/systemd"}
)
_SPECIAL_MODE_BITS: Final = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX


class DeploymentArtifactPolicyError(ValueError):
    """A runtime deployment artifact is unsafe for root extraction."""


def archive_path_is_excluded(relative_path: Path, *, is_directory: bool) -> bool:
    """Return whether a relative path must stay out of a deployment archive.

    Public files ending in ".example" are intentionally retained as reviewed
    templates, including ".env.example" and "server.key.example". The
    exception never applies to directories or to files nested below a
    credential/environment directory: a template filename must not make an
    unreviewed secret tree archiveable.
    """

    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"archive path must be relative and normalized: {relative_path}")

    parts = tuple(part.casefold() for part in relative_path.parts)
    if any(part in _CACHE_DIRECTORY_NAMES for part in parts):
        return True
    if relative_path.suffix.casefold() in _GENERATED_SUFFIXES:
        return True

    parent_parts = parts[:-1]
    if any(
        _is_environment_name(part) or part in _CREDENTIAL_DIRECTORY_NAMES
        for part in parent_parts
    ):
        return True

    name = relative_path.name.casefold()
    if not is_directory and name.endswith(".example"):
        return False
    if _is_environment_name(name) or name in _CREDENTIAL_DIRECTORY_NAMES:
        return True
    if relative_path.suffix.casefold() in _CREDENTIAL_SUFFIXES:
        return True
    return _is_credential_file_name(name)


def _is_environment_name(name: str) -> bool:
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def _is_credential_file_name(name: str) -> bool:
    return name in _CREDENTIAL_FILE_STEMS or any(
        name.startswith(f"{stem}.") for stem in _CREDENTIAL_FILE_STEMS
    )


def _artifact_policy_fail(message: str) -> NoReturn:
    raise DeploymentArtifactPolicyError(message)


def _normalize_artifact_member_name(name: str) -> str:
    """Return one unambiguous portable artifact path or fail closed.

    GNU tar's extraction semantics are POSIX-oriented, while the controller
    may create an archive from Windows.  Restricting names to a small ASCII
    subset and rejecting redundant components keeps the root-side extraction
    target independent of platform-specific path normalization.
    """

    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        _artifact_policy_fail("deployment artifact contains an unsafe member path")

    normalized = name[:-1] if name.endswith("/") else name
    if not normalized or normalized.endswith("/"):
        _artifact_policy_fail("deployment artifact contains an ambiguous member path")

    parts = normalized.split("/")
    if any(
        not part
        or part in {".", ".."}
        or any(not (character.isascii() and (character.isalnum() or character in "._-")) for character in part)
        for part in parts
    ):
        _artifact_policy_fail("deployment artifact contains an unsafe member path")
    return normalized


def _artifact_path_is_allowed(member_name: str) -> bool:
    if member_name in _ARTIFACT_FIXED_FILES:
        return True
    if member_name in _ARTIFACT_DIRECTORY_NAMES:
        return True

    first_component = member_name.split("/", maxsplit=1)[0]
    if first_component in _ARTIFACT_DIRECTORY_ROOTS:
        return "/" in member_name
    return member_name.startswith("infra/systemd/")


def _validate_artifact_member(
    member: tarfile.TarInfo,
    *,
    seen_paths: set[str],
    regular_paths: set[str],
    descendant_prefixes: set[str],
    total_unpacked_bytes: int,
) -> tuple[str, int]:
    """Validate one logical member and return its path and aggregate size.

    ``tarfile`` resolves PAX/GNU extension records before exposing a
    ``TarInfo``.  Its sparse flag therefore covers both GNU sparse headers
    and the PAX sparse variants that the standard library understands.
    """

    member_name = _normalize_artifact_member_name(member.name)
    if not _artifact_path_is_allowed(member_name):
        _artifact_policy_fail("deployment artifact contains an unauthorized member path")
    if member_name in seen_paths:
        _artifact_policy_fail("deployment artifact contains duplicate member paths")

    if member.issparse():
        _artifact_policy_fail("deployment artifact contains a sparse member")
    if member.issym() or member.islnk():
        _artifact_policy_fail("deployment artifact contains a link member")
    if not member.isreg() and not member.isdir():
        _artifact_policy_fail("deployment artifact contains a device or special member")
    if member.name.endswith("/") and not member.isdir():
        _artifact_policy_fail("deployment artifact contains an ambiguous regular-file path")
    if member.mode & _SPECIAL_MODE_BITS:
        _artifact_policy_fail("deployment artifact contains a privileged mode")

    if member.isdir():
        if member.size != 0:
            _artifact_policy_fail("deployment artifact contains a nonempty directory member")
        if member_name in _ARTIFACT_FIXED_FILES:
            _artifact_policy_fail("deployment artifact contains an invalid fixed-file type")
        member_size = 0
    else:
        if member_name in _ARTIFACT_DIRECTORY_NAMES:
            _artifact_policy_fail("deployment artifact contains an invalid directory type")
        member_size = member.size
        if member_size < 0 or member_size > MAX_DEPLOYMENT_ARTIFACT_MEMBER_BYTES:
            _artifact_policy_fail("deployment artifact member exceeds the fixed size limit")
        if total_unpacked_bytes + member_size > MAX_DEPLOYMENT_ARTIFACT_UNPACKED_BYTES:
            _artifact_policy_fail("deployment artifact exceeds the aggregate unpacked size limit")

    components = member_name.split("/")
    for index in range(1, len(components)):
        if "/".join(components[:index]) in regular_paths:
            _artifact_policy_fail("deployment artifact places a child below a regular file")
    if member.isreg() and member_name in descendant_prefixes:
        _artifact_policy_fail("deployment artifact replaces a directory with a regular file")

    seen_paths.add(member_name)
    for index in range(1, len(components)):
        descendant_prefixes.add("/".join(components[:index]))
    if member.isreg():
        regular_paths.add(member_name)
    return member_name, total_unpacked_bytes + member_size


def validate_deployment_artifact(archive_path: Path) -> None:
    """Validate a gzip deployment artifact before any root tar extraction.

    This is deliberately validation-only.  ``install-release.sh`` extracts
    only after this function returns successfully and only from its fixed,
    root-owned candidate path.
    """

    seen_paths: set[str] = set()
    regular_paths: set[str] = set()
    descendant_prefixes: set[str] = set()
    total_unpacked_bytes = 0
    member_count = 0

    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            for member in archive:
                member_count += 1
                if member_count > MAX_DEPLOYMENT_ARTIFACT_MEMBERS:
                    _artifact_policy_fail("deployment artifact exceeds the member count limit")
                _, total_unpacked_bytes = _validate_artifact_member(
                    member,
                    seen_paths=seen_paths,
                    regular_paths=regular_paths,
                    descendant_prefixes=descendant_prefixes,
                    total_unpacked_bytes=total_unpacked_bytes,
                )
    except DeploymentArtifactPolicyError:
        raise
    except (EOFError, OSError, tarfile.TarError, UnicodeError) as exc:
        raise DeploymentArtifactPolicyError(
            "deployment artifact cannot be read as a valid gzip tar archive"
        ) from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a Northstar deployment artifact.")
    parser.add_argument("--validate-deployment-artifact", type=Path, required=True)
    return parser


def main() -> int:
    archive_path = _build_parser().parse_args().validate_deployment_artifact
    try:
        validate_deployment_artifact(archive_path)
    except DeploymentArtifactPolicyError as exc:
        print(f"deployment artifact policy failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
