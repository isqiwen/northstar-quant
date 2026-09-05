"""Identify the actual current implementation shared by data processing and trading."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, distribution
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def _source_files(directory: Traversable, prefix: str = "") -> list[tuple[str, bytes]]:
    result: list[tuple[str, bytes]] = []
    for entry in sorted(directory.iterdir(), key=lambda candidate: candidate.name):
        relative = f"{prefix}{entry.name}"
        if entry.is_dir() and entry.name != "__pycache__":
            result.extend(_source_files(entry, f"{relative}/"))
        elif entry.is_file() and entry.name.endswith((".py", ".js", ".css", ".html")):
            result.append((relative, entry.read_bytes()))
    return result


@lru_cache(maxsize=1)
def implementation_hash() -> str:
    """Bind current sources, lock and actual runtime; never paths or build timestamps."""

    package = files("northstar_quant")
    packaged_lock = package.joinpath("uv.lock")
    if packaged_lock.is_file():
        lock = packaged_lock.read_bytes()
    else:
        checkout_lock = Path(__file__).resolve().parents[2] / "uv.lock"
        if not checkout_lock.is_file():
            raise ValueError("the installed application is missing its dependency lock")
        lock = checkout_lock.read_bytes()
    runtime = json.dumps(
        _runtime_identity(),
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256()
    for name, contents in [*_source_files(package), ("uv.lock", lock), ("runtime", runtime)]:
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(contents).to_bytes(8, "big"))
        digest.update(contents)
    return digest.hexdigest()


def _runtime_identity() -> dict[str, object]:
    pending: list[tuple[str, frozenset[str]]] = [("northstar-quant", frozenset())]
    visited: dict[str, set[str]] = {}
    versions: dict[str, str] = {}
    while pending:
        name, extras = pending.pop()
        name = canonicalize_name(name)
        contexts = {"", *extras}
        prior = visited.setdefault(name, set())
        if contexts <= prior:
            continue
        prior.update(contexts)
        try:
            installed = distribution(name)
        except PackageNotFoundError as error:
            raise ValueError(f"active runtime dependency {name} is not installed") from error
        versions[name] = installed.version
        for declaration in installed.requires or []:
            requirement = Requirement(declaration)
            if requirement.marker is None or any(
                requirement.marker.evaluate({"extra": extra}) for extra in contexts
            ):
                pending.append((requirement.name, frozenset(requirement.extras)))
    return {
        "python_implementation": sys.implementation.name,
        "python_version": list(sys.version_info),
        "implementation_version": list(sys.implementation.version),
        "platform": sys.platform,
        "architecture": platform.machine(),
        "distributions": dict(sorted(versions.items())),
    }
