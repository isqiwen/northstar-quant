"""Focused tests for the offline dependency-integrity policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci import check_dependency_policy as policy
from tests.helpers.paths import PROJECT_ROOT


_REGISTRY = "https://pypi.org/simple"
_ARTIFACT_HOST = "https://files.pythonhosted.org/packages"
_HASH_A = "a" * 64
_HASH_B = "b" * 64
_JSONPATH_HASH = "d87ef2bcbcded68ee96bc34c1809b69457ecec9b0c4dd471658a12bd391002d1"
_JSONPATH_URL = (
    "https://files.pythonhosted.org/packages/cf/a1/693351acd0a9edca4de9153372a65e75398898ea7f8a5c722ab00f464929/"
    "jsonpath-0.82.2.tar.gz"
)


def _artifact(*, name: str, digest: str, size: int = 1, url: str | None = None) -> str:
    artifact_url = url or f"{_ARTIFACT_HOST}/{name}-1.0.0.tar.gz"
    return (
        "{ "
        f'url = "{artifact_url}", '
        f'hash = "sha256:{digest}", '
        f"size = {size}, "
        'upload-time = "2026-01-02T03:04:05Z" '
        "}"
    )


def _registry_package(
    *,
    name: str,
    version: str,
    digest: str,
    source: str = f'source = {{ registry = "{_REGISTRY}" }}',
    artifact: str | None = None,
) -> str:
    artifact_value = artifact or _artifact(name=name, digest=digest)
    return "\n".join(
        (
            "[[package]]",
            f'name = "{name}"',
            f'version = "{version}"',
            source,
            f"sdist = {artifact_value}",
            "wheels = ["
            + _artifact(
                name=name,
                digest=digest,
                url=f"{_ARTIFACT_HOST}/{name}-1.0.0-py3-none-any.whl",
            )
            + "]",
        )
    )


def _valid_pyproject(*, alpha_requirement: str = "alpha>=1.0") -> str:
    return "\n".join(
        (
            "[build-system]",
            'requires = ["setuptools==80.9.0", "wheel==0.45.1"]',
            'build-backend = "setuptools.build_meta"',
            "",
            "[project]",
            'name = "sample-project"',
            'version = "0.1.0"',
            'requires-python = ">=3.11"',
            f'dependencies = ["{alpha_requirement}"]',
            "",
            "[project.optional-dependencies]",
            'dev = ["beta>=2.0"]',
            "",
            "[dependency-groups]",
            'build-bootstrap = ["setuptools==80.9.0", "wheel==0.45.1"]',
            "",
        )
    )


def _valid_lock(*, alpha_source: str | None = None, alpha_artifact: str | None = None) -> str:
    root = "\n".join(
        (
            "[[package]]",
            'name = "sample-project"',
            'version = "0.1.0"',
            'source = { editable = "." }',
            'dependencies = [{ name = "alpha" }]',
            "",
            "[package.optional-dependencies]",
            'dev = [{ name = "beta" }]',
            "",
            "[package.dev-dependencies]",
            'build-bootstrap = [{ name = "setuptools" }, { name = "wheel" }]',
            "",
            "[package.metadata]",
            "requires-dist = [",
            '    { name = "alpha", specifier = ">=1.0" },',
            '    { name = "beta", marker = "extra == \'dev\'", specifier = ">=2.0" },',
            "]",
            "",
            "[package.metadata.requires-dev]",
            "build-bootstrap = [",
            '    { name = "setuptools", specifier = "==80.9.0" },',
            '    { name = "wheel", specifier = "==0.45.1" },',
            "]",
        )
    )
    alpha = _registry_package(
        name="alpha",
        version="1.0.0",
        digest=_HASH_A,
        source=alpha_source or f'source = {{ registry = "{_REGISTRY}" }}',
        artifact=alpha_artifact,
    )
    beta = _registry_package(name="beta", version="2.0.0", digest=_HASH_B)
    setuptools = _registry_package(name="setuptools", version="80.9.0", digest="c" * 64)
    wheel = _registry_package(name="wheel", version="0.45.1", digest="d" * 64)
    jsonpath = "\n".join(
        (
            "[[package]]",
            'name = "jsonpath"',
            'version = "0.82.2"',
            f'source = {{ registry = "{_REGISTRY}" }}',
            "sdist = { "
            f'url = "{_JSONPATH_URL}", '
            f'hash = "sha256:{_JSONPATH_HASH}", '
            "size = 10353, "
            'upload-time = "2023-08-24T18:57:55.459Z" '
            "}",
        )
    )
    return "\n".join(
        (
            "version = 1",
            "revision = 1",
            'requires-python = ">=3.11"',
            "",
            root,
            "",
            alpha,
            "",
            beta,
            "",
            setuptools,
            "",
            wheel,
            "",
            jsonpath,
            "",
        )
    )


def _write_manifest(
    tmp_path: Path,
    *,
    pyproject: str | None = None,
    lock: str | None = None,
) -> tuple[Path, Path]:
    pyproject_path = tmp_path / "pyproject.toml"
    lock_path = tmp_path / "uv.lock"
    pyproject_path.write_text(pyproject or _valid_pyproject(), encoding="utf-8")
    lock_path.write_text(lock or _valid_lock(), encoding="utf-8")
    return pyproject_path, lock_path


def _evaluate_fixture(tmp_path: Path, *, pyproject: str | None = None, lock: str | None = None):
    pyproject_path, lock_path = _write_manifest(tmp_path, pyproject=pyproject, lock=lock)
    return policy.evaluate_dependency_policy(pyproject_path, lock_path)


def test_policy_accepts_current_repository_and_emits_sorted_stable_inventory():
    first = policy.evaluate_dependency_policy(PROJECT_ROOT / "pyproject.toml", PROJECT_ROOT / "uv.lock")
    second = policy.evaluate_dependency_policy(PROJECT_ROOT / "pyproject.toml", PROJECT_ROOT / "uv.lock")

    assert first == second
    assert first.inventory == tuple(sorted(first.inventory))
    assert first.inventory
    assert all(entry.source == _REGISTRY for entry in first.inventory)
    assert len(first.inventory_digest) == 64
    assert len(first.lock_digest) == 64
    assert "hash =" not in first.render()
    assert "files.pythonhosted.org" not in first.render()


def test_policy_cli_output_is_deterministic_and_limited_to_inventory_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    pyproject_path, lock_path = _write_manifest(tmp_path)

    assert policy.main(("--pyproject", str(pyproject_path), "--lock", str(lock_path))) == 0
    first = capsys.readouterr()
    assert policy.main(("--pyproject", str(pyproject_path), "--lock", str(lock_path))) == 0
    second = capsys.readouterr()

    assert first.err == second.err == ""
    assert first.out == second.out
    assert first.out.splitlines() == [
        "DEPENDENCY_POLICY_OK",
        *[line for line in first.out.splitlines() if line.startswith("lock_digest=")],
        *[line for line in first.out.splitlines() if line.startswith("inventory_digest=")],
        "package name=alpha version=1.0.0 source=https://pypi.org/simple",
        "package name=beta version=2.0.0 source=https://pypi.org/simple",
        "package name=jsonpath version=0.82.2 source=https://pypi.org/simple",
        "package name=setuptools version=80.9.0 source=https://pypi.org/simple",
        "package name=wheel version=0.45.1 source=https://pypi.org/simple",
    ]


def test_policy_rejects_project_lock_dependency_and_metadata_mismatches(tmp_path: Path):
    lock = _valid_lock().replace('dependencies = [{ name = "alpha" }]', 'dependencies = [{ name = "beta" }]')
    with pytest.raises(policy.DependencyPolicyError, match="runtime dependencies"):
        _evaluate_fixture(tmp_path, lock=lock)

    lock = _valid_lock().replace('specifier = ">=1.0"', 'specifier = ">=9.0"', 1)
    with pytest.raises(policy.DependencyPolicyError, match="specifiers"):
        _evaluate_fixture(tmp_path, lock=lock)

    lock = _valid_lock().replace("marker = \"extra == 'dev'\", ", "")
    with pytest.raises(policy.DependencyPolicyError, match="specifiers"):
        _evaluate_fixture(tmp_path, lock=lock)


def test_policy_rejects_runtime_marker_declarations_and_lock_marker_drift(tmp_path: Path):
    pyproject = _valid_pyproject().replace(
        'dependencies = ["alpha>=1.0"]',
        'dependencies = ["alpha>=1.0; sys_platform == \'win32\'"]',
    )
    with pytest.raises(policy.DependencyPolicyError, match="runtime dependency markers"):
        _evaluate_fixture(tmp_path, pyproject=pyproject)

    lock = _valid_lock().replace(
        'dependencies = [{ name = "alpha" }]',
        'dependencies = [{ name = "alpha", marker = "sys_platform == \'win32\'" }]',
    )
    with pytest.raises(policy.DependencyPolicyError, match="runtime dependencies"):
        _evaluate_fixture(tmp_path, lock=lock)


def test_policy_rejects_direct_source_metadata_hidden_in_dependency_links(tmp_path: Path):
    lock = _valid_lock().replace(
        'dependencies = [{ name = "alpha" }]',
        'dependencies = [{ name = "alpha", url = "https://user:secret@example.invalid/a.whl" }]',  # secret-scan: allow; reason: disposable test fixture
    )

    with pytest.raises(policy.DependencyPolicyError, match="unsupported dependency-link fields"):
        _evaluate_fixture(tmp_path, lock=lock)


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ('source = { registry = "https://mirror.example.invalid/simple" }', "non-allowlisted"),
        ('source = { url = "https://example.invalid/alpha.whl" }', "allowlisted registry"),
        ('source = { git = "https://example.invalid/alpha.git" }', "allowlisted registry"),
        ('source = { path = "../alpha" }', "allowlisted registry"),
        ('source = { editable = "." }', "allowlisted registry"),
    ),
)
def test_policy_rejects_non_registry_and_non_allowlisted_package_sources(
    tmp_path: Path, source: str, expected: str
):
    with pytest.raises(policy.DependencyPolicyError, match=expected):
        _evaluate_fixture(tmp_path, lock=_valid_lock(alpha_source=source))


def test_policy_allows_only_the_exact_project_root_editable_exemption(tmp_path: Path):
    lock = _valid_lock().replace('source = { editable = "." }', 'source = { editable = ".." }', 1)

    with pytest.raises(policy.DependencyPolicyError, match="sole editable"):
        _evaluate_fixture(tmp_path, lock=lock)


def test_policy_rejects_direct_project_requirements_and_source_overrides(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    pyproject = _valid_pyproject(alpha_requirement="alpha @ https://user:secret@example.invalid/a.whl")  # secret-scan: allow; reason: disposable test fixture
    pyproject_path, lock_path = _write_manifest(tmp_path, pyproject=pyproject)

    assert policy.main(("--pyproject", str(pyproject_path), "--lock", str(lock_path))) == 1
    assert "secret" not in capsys.readouterr().err

    pyproject = _valid_pyproject() + "\n[tool.uv.sources]\nalpha = { path = \"../alpha\" }\n"
    with pytest.raises(policy.DependencyPolicyError, match="source overrides"):
        _evaluate_fixture(tmp_path, pyproject=pyproject)


@pytest.mark.parametrize(
    ("build_requires", "suffix", "backend", "expected"),
    (
        (
            '["setuptools @ https://user:secret@example.invalid/a.whl"]',  # secret-scan: allow; reason: disposable test fixture
            "",
            None,
            "direct, VCS, or local",
        ),
        (
            '["setuptools==80.9.0", "wheel==0.45.1"]',
            'backend-path = ["build_backend"]\n',
            None,
            "backend-path",
        ),
        (
            '["setuptools==80.9.0", "wheel==0.45.1", "ordinary-registry-package>=1"]',
            "",
            None,
            "unapproved requirements",
        ),
        (
            '["setuptools==80.9.0; sys_platform == \'linux\'", "wheel==0.45.1"]',
            "",
            None,
            "unapproved requirements",
        ),
        ('["setuptools==80.9.0", "wheel==0.45.1"]', "", "ambient.backend", "not allowlisted"),
    ),
)
def test_policy_rejects_build_system_supply_chain_bypasses(
    tmp_path: Path, build_requires: str, suffix: str, backend: str | None, expected: str
):
    pyproject = _valid_pyproject().replace(
        'requires = ["setuptools==80.9.0", "wheel==0.45.1"]\n',
        f"requires = {build_requires}\n{suffix}",
        1,
    )
    if backend is not None:
        pyproject = pyproject.replace('build-backend = "setuptools.build_meta"', f'build-backend = "{backend}"')

    with pytest.raises(policy.DependencyPolicyError, match=expected):
        _evaluate_fixture(tmp_path, pyproject=pyproject)


@pytest.mark.parametrize(
    ("artifact", "expected"),
    (
        (_artifact(name="alpha", digest="A" * 64), "SHA-256"),
        (_artifact(name="alpha", digest=_HASH_A, size=0), "invalid artifact metadata"),
        (
            _artifact(
                name="alpha",
                digest=_HASH_A,
                url="https://artifacts.example.invalid/alpha-1.0.0.tar.gz",
            ),
            "unapproved artifact URL",
        ),
        ('{ url = "https://files.pythonhosted.org/packages/a.tar.gz", hash = "sha256:' + _HASH_A + '", size = 1 }', "incomplete artifact metadata"),
    ),
)
def test_policy_rejects_missing_or_invalid_artifact_hashes_and_metadata(
    tmp_path: Path, artifact: str, expected: str
):
    with pytest.raises(policy.DependencyPolicyError, match=expected):
        _evaluate_fixture(tmp_path, lock=_valid_lock(alpha_artifact=artifact))


def test_policy_rejects_untracked_dependency_references_and_unapproved_dependency_groups(
    tmp_path: Path,
):
    lock = _valid_lock().replace('dependencies = [{ name = "alpha" }]', 'dependencies = [{ name = "missing" }]')
    with pytest.raises(policy.DependencyPolicyError, match="missing from the lock"):
        _evaluate_fixture(tmp_path, lock=lock)

    pyproject = _valid_pyproject().replace(
        "[dependency-groups]\nbuild-bootstrap = [\"setuptools==80.9.0\", \"wheel==0.45.1\"]",
        "[dependency-groups]\ntest = [\"pytest>=8\"]",
    )
    with pytest.raises(policy.DependencyPolicyError, match="build-bootstrap dependency group"):
        _evaluate_fixture(tmp_path, pyproject=pyproject)


def test_policy_rejects_build_bootstrap_lock_and_source_build_manifest_drift(tmp_path: Path):
    lock = _valid_lock().replace('version = "0.45.1"', 'version = "9.9.9"', 1)
    with pytest.raises(policy.DependencyPolicyError, match="exact build-bootstrap package: wheel"):
        _evaluate_fixture(tmp_path, lock=lock)

    lock = _valid_lock().replace(_JSONPATH_HASH, "e" * 64)
    with pytest.raises(policy.DependencyPolicyError, match="source-build package jsonpath"):
        _evaluate_fixture(tmp_path, lock=lock)

    unexpected_source_only = "\n".join(
        (
            "[[package]]",
            'name = "unexpected-source"',
            'version = "1.0.0"',
            f'source = {{ registry = "{_REGISTRY}" }}',
            f'sdist = {_artifact(name="unexpected-source", digest="f" * 64)}',
            "",
        )
    )
    with pytest.raises(policy.DependencyPolicyError, match="source-only packages"):
        _evaluate_fixture(tmp_path, lock=_valid_lock() + unexpected_source_only)


@pytest.mark.parametrize(
    ("name", "version"),
    (
        ("setuptools", "99.0.0"),
        ("wheel", "99.0.0"),
        ("jsonpath", "99.0.0"),
    ),
)
def test_policy_rejects_duplicate_critical_lock_entries(
    tmp_path: Path, name: str, version: str
) -> None:
    duplicate = _registry_package(name=name, version=version, digest="f" * 64)

    with pytest.raises(policy.DependencyPolicyError, match=f"critical package entry: {name}"):
        _evaluate_fixture(tmp_path, lock=_valid_lock() + "\n" + duplicate)
