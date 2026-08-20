"""mypy 类型债务基线的工程契约。"""

from __future__ import annotations

from collections import Counter
import json
import subprocess
import sys

from scripts.ci import check_mypy_baseline
from tests.helpers.paths import PROJECT_ROOT

BASELINE_PATH = PROJECT_ROOT / ".mypy-baseline.json"
CHECK_SCRIPT = PROJECT_ROOT / "scripts" / "ci" / "check_mypy_baseline.py"


def _run_git(repository, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _diagnostic(path: str, line: int) -> dict[str, int | str | None]:
    return {
        "file": path,
        "line": line,
        "column": 4,
        "code": "arg-type",
        "message": "历史类型错误",
    }


def test_mypy_baseline_is_versioned_and_sorted() -> None:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    assert baseline["schema_version"] == 1
    assert baseline["mypy_version"] == "1.20.0"
    diagnostics = baseline["diagnostics"]
    assert isinstance(diagnostics, list)
    assert len(diagnostics) == 43
    assert diagnostics == sorted(
        diagnostics,
        key=lambda item: (
            item["file"],
            item["line"],
            item["column"],
            item["code"] or "",
            item["message"],
        ),
    )


def test_mypy_baseline_matches_current_source() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), "check"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "mypy 类型基线通过：43 条已记录诊断。" in result.stdout


def test_mypy_ratchet_accepts_only_git_moved_unchanged_source_line(tmp_path, monkeypatch) -> None:
    """路径和行号迁移可保留债务，源码行变化则必须继续报为新增。"""

    package_dir = tmp_path / "src" / "northstar_quant"
    package_dir.mkdir(parents=True)
    legacy_path = package_dir / "legacy.py"
    legacy_lines = [f"value_{index} = {index}\n" for index in range(1, 10)]
    legacy_lines.append("bad_call()\n")
    legacy_path.write_text("".join(legacy_lines), encoding="utf-8")

    _run_git(tmp_path, "init")
    _run_git(tmp_path, "config", "user.email", "test@example.invalid")
    _run_git(tmp_path, "config", "user.name", "Northstar Test")
    _run_git(tmp_path, "add", ".")
    _run_git(tmp_path, "commit", "-m", "baseline")
    base_revision = _run_git(tmp_path, "rev-parse", "HEAD")

    moved_path = package_dir / "moved.py"
    legacy_path.rename(moved_path)
    moved_path.write_text(
        "# package move\n# line-number shift\n" + "".join(legacy_lines),
        encoding="utf-8",
    )
    _run_git(tmp_path, "add", "-A")
    monkeypatch.setattr(check_mypy_baseline, "PROJECT_ROOT", tmp_path)

    base_diagnostics = [_diagnostic("src/northstar_quant/legacy.py", 10)]
    current_diagnostics = [_diagnostic("src/northstar_quant/moved.py", 12)]

    assert not check_mypy_baseline._added_diagnostics_against_base(
        base_diagnostics,
        current_diagnostics,
        base_revision=base_revision,
    )

    extra_diagnostic = _diagnostic("src/northstar_quant/moved.py", 11)
    assert check_mypy_baseline._added_diagnostics_against_base(
        base_diagnostics,
        [*current_diagnostics, extra_diagnostic],
        base_revision=base_revision,
    ) == Counter(
        {
            (
                "src/northstar_quant/moved.py",
                11,
                4,
                "arg-type",
                "历史类型错误",
            ): 1,
        }
    )

    moved_lines = moved_path.read_text(encoding="utf-8").splitlines()
    moved_lines[11] = "different_bad_call()"
    moved_path.write_text("\n".join(moved_lines) + "\n", encoding="utf-8")
    _run_git(tmp_path, "add", "-A")

    added = check_mypy_baseline._added_diagnostics_against_base(
        base_diagnostics,
        current_diagnostics,
        base_revision=base_revision,
    )
    assert added == Counter(
        {
            (
                "src/northstar_quant/moved.py",
                12,
                4,
                "arg-type",
                "历史类型错误",
            ): 1,
        }
    )
