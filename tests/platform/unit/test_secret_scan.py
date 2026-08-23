import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci import check_secrets
from scripts.ci.check_secrets import find_secret_lines, find_secret_paths
from tests.helpers.paths import PROJECT_ROOT


_ALLOW_MARKER = "secret-scan" + ": allow"


def _assignment(key: str, value: str) -> str:
    return f"{key}={value}"


def _allow_line(value: str, reason: str = "reason: disposable test fixture") -> str:
    return f"{_assignment('TOKEN', value)} # {_ALLOW_MARKER}; {reason}"


def _initialize_tracked_file(root: Path, relative_path: Path, contents: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "add", relative_path.as_posix()], cwd=root, check=True)


def _initialize_tracked_bytes(root: Path, relative_path: Path, contents: bytes) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "add", relative_path.as_posix()], cwd=root, check=True)


def test_secret_scan_detects_key_values_dsns_and_private_keys():
    assert find_secret_lines(_assignment("NORTHSTAR_NTFY_TOKEN", "real-token-value-123456")) == [1]
    assert find_secret_lines(_assignment("password", '"real-secret-value"')) == [1]
    dsn = "postgresql+psycopg://user" + ":" + "real-password@db.example.test/db"
    assert find_secret_lines(_assignment("DATABASE_URL", dsn)) == [1]
    assert find_secret_lines("-----BEGIN " + "PRIVATE KEY-----") == [1]


def test_secret_scan_allows_explicit_placeholders():
    source = "\n".join(
        (
            _assignment("NORTHSTAR_NTFY_TOKEN", ""),
            _assignment("NORTHSTAR_NTFY_TOKEN", "${NORTHSTAR_NTFY_TOKEN}"),
            _assignment("API_KEY", "CHANGE_ME"),
        )
    )

    assert find_secret_lines(source) == []


def test_secret_scan_does_not_treat_broad_substrings_as_placeholders():
    assert find_secret_lines(_assignment("TOKEN", "real-test-token-value")) == [1]
    assert find_secret_lines(_assignment("TOKEN", "your_token_is_not_a_placeholder")) == [1]
    assert find_secret_lines(_assignment("TOKEN", "real${token}value")) == [1]


def test_secret_scan_checks_every_key_value_match_on_a_line():
    source = " ".join(
        (
            _assignment("TOKEN", "${TOKEN}"),
            _assignment("API_KEY", "real-secret-value"),
        )
    )

    assert find_secret_lines(source) == [1]


def test_secret_scan_rejects_static_fstring_secret_segments_and_unsafe_shell_defaults():
    assert find_secret_lines(_assignment("API_KEY", 'f"real-{suffix}"')) == [1]
    assert find_secret_lines(_assignment("API_KEY", 'identity("real-secret")')) == [1]
    assert find_secret_lines(_assignment("API_KEY", "f\"{identity('real-secret')}\"")) == [1]
    assert find_secret_lines(_assignment("TOKEN", "${TOKEN?}")) == [1]  # secret-scan: allow; reason: disposable test fixture
    assert find_secret_lines(_assignment("TOKEN", "${TOKEN:?required}")) == [1]  # secret-scan: allow; reason: disposable test fixture
    assert find_secret_lines(_assignment("TOKEN", "${TOKEN:?real-secret}")) == [1]  # secret-scan: allow; reason: disposable test fixture
    assert find_secret_lines(_assignment("TOKEN", "${TOKEN:-real-secret}")) == [1]  # secret-scan: allow; reason: disposable test fixture
    assert find_secret_lines(_assignment("TOKEN", "${TOKEN:+real-secret}")) == [1]  # secret-scan: allow; reason: disposable test fixture
    assert find_secret_lines(_assignment("TOKEN", "$(printf real-secret)")) == [1]
    assert (
        find_secret_lines(
            _assignment("TOKEN", "$(deploy_read_env_value file; printf real-secret)")
        )
        == [1]
    )
    assert (
        find_secret_lines(
            _assignment(
                "TOKEN",
                '$(deploy_read_env_value "file; real-secret" "TOKEN")',
            )
        )
        == [1]
    )


def test_secret_scan_allows_exact_dynamic_references_without_static_secret_segments():
    assert find_secret_lines(_assignment("API_KEY", 'f"{suffix}"')) == []
    assert (
        find_secret_lines(
            _assignment(
                "POSTGRES_PASSWORD",
                "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}",  # secret-scan: allow; reason: disposable test fixture
            )
        )
        == []
    )


def test_secret_scan_allows_reasoned_directive_only_in_disposable_fixture_paths():
    source = _allow_line("fixture-secret-value")

    assert find_secret_lines(source, relative_path="tests/unit/test_fixture.py") == []
    assert find_secret_lines(source, relative_path=".github/workflows/ci.yml") == []
    assert find_secret_lines(source) == [1]


@pytest.mark.parametrize(
    "relative_path",
    (
        "src/northstar_quant/application/service.py",
        "configs/app.yaml",
        "scripts/deploy/release.py",
        "docs/security.md",
        ".github/workflows/deploy.yml",
    ),
)
def test_secret_scan_rejects_directives_outside_disposable_test_or_ci_fixtures(
    relative_path: str,
):
    assert find_secret_lines(_allow_line("fixture-secret-value"), relative_path=relative_path) == [1]


@pytest.mark.parametrize(
    "relative_path",
    (
        "Tests/unit/test_fixture.py",
        "tests\\unit\\test_fixture.py",
        "tests/../tests/unit/test_fixture.py",
        "./tests/unit/test_fixture.py",
        "/tests/unit/test_fixture.py",
    ),
)
def test_secret_scan_rejects_noncanonical_fixture_paths(relative_path: str):
    assert find_secret_lines(_allow_line("fixture-secret-value"), relative_path=relative_path) == [1]


@pytest.mark.parametrize(
    "source",
    (
        f"{_assignment('TOKEN', 'fixture-secret-value')} # {_ALLOW_MARKER}",
        f"{_assignment('TOKEN', 'fixture-secret-value')} # {_ALLOW_MARKER};",
        f"{_assignment('TOKEN', 'fixture-secret-value')} # {_ALLOW_MARKER}; arbitrary words",
        f"# {_ALLOW_MARKER}; ---",
    ),
)
def test_secret_scan_rejects_missing_or_non_explanatory_allow_reasons(source: str):
    assert find_secret_lines(source, relative_path="tests/unit/test_fixture.py") == [1]


def test_secret_scan_scans_tracked_test_text(tmp_path: Path):
    relative_path = Path("tests/unit/test_fixture.py")
    _initialize_tracked_file(
        tmp_path,
        relative_path,
        _assignment("NORTHSTAR_API_KEY", "fixture-secret-value"),
    )

    assert find_secret_paths(tmp_path) == [relative_path]


def test_secret_scan_reads_nul_delimited_tracked_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    relative_path = Path("tests/unit/fixture.py")
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True)
    path.write_text(_assignment("NORTHSTAR_API_KEY", "fixture-secret-value"), encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"tests/unit/fixture.py\0")

    monkeypatch.setattr(check_secrets, "_repository_root", lambda _: tmp_path)
    monkeypatch.setattr(check_secrets.subprocess, "run", fake_run)

    assert find_secret_paths(tmp_path) == [relative_path]
    assert calls == [["git", "-C", str(tmp_path), "ls-files", "-z"]]


def test_secret_scan_allows_reasoned_directive_in_tracked_test_fixture(tmp_path: Path):
    relative_path = Path("tests/unit/test_fixture.py")
    _initialize_tracked_file(tmp_path, relative_path, _allow_line("fixture-secret-value"))

    assert find_secret_paths(tmp_path) == []


def test_secret_scan_fails_closed_on_invalid_utf8_tracked_text(tmp_path: Path):
    relative_path = Path("tests/unit/invalid_fixture.py")
    _initialize_tracked_bytes(tmp_path, relative_path, b"\xff")

    assert find_secret_paths(tmp_path) == [relative_path]


def test_secret_scan_scans_tracked_templates_without_an_implicit_exclusion(tmp_path: Path):
    relative_path = Path(".env.example")
    _initialize_tracked_file(
        tmp_path,
        relative_path,
        _assignment("NORTHSTAR_API_KEY", "fixture-secret-value"),
    )

    assert find_secret_paths(tmp_path) == [relative_path]


def test_secret_scan_does_not_skip_textual_secrets_by_file_suffix(tmp_path: Path):
    relative_path = Path("tests/unit/fixture.png")
    _initialize_tracked_file(
        tmp_path,
        relative_path,
        _assignment("NORTHSTAR_API_KEY", "fixture-secret-value"),
    )

    assert find_secret_paths(tmp_path) == [relative_path]


def test_secret_scan_skips_only_recognized_binary_magic(tmp_path: Path):
    relative_path = Path("tests/unit/fixture.png")
    _initialize_tracked_bytes(tmp_path, relative_path, b"\x89PNG\r\n\x1a\n\xff")

    assert find_secret_paths(tmp_path) == []


def test_secret_scan_fails_closed_on_tracked_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    relative_path = Path("tests/unit/link.py")
    target = tmp_path / "outside-repository.py"
    target.write_text(_assignment("API_KEY", "fixture-secret-value"), encoding="utf-8")
    link = tmp_path / relative_path
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(target)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 0, stdout=b"tests/unit/link.py\0")

    monkeypatch.setattr(check_secrets, "_repository_root", lambda _: tmp_path)
    monkeypatch.setattr(check_secrets.subprocess, "run", fake_run)

    assert find_secret_paths(tmp_path) == [relative_path]


def test_secret_scan_accepts_current_tracked_repository():
    assert find_secret_paths(Path.cwd()) == []


def test_secret_scan_cli_discovers_the_repository_root_from_a_subdirectory():
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "ci" / "check_secrets.py")],
        cwd=PROJECT_ROOT / "scripts" / "ci",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "secret scan passed"


def test_just_check_runs_the_secret_scan():
    justfile = (PROJECT_ROOT / "justfile").read_text(encoding="utf-8")
    check_recipe = justfile.split("\ncheck:\n", maxsplit=1)[1].split("\n\n#", maxsplit=1)[0]

    assert "python scripts/ci/check_secrets.py" in check_recipe
