"""mypy 类型债务基线的工程契约。"""

from __future__ import annotations

import json
import subprocess
import sys

from tests.support.paths import PROJECT_ROOT

BASELINE_PATH = PROJECT_ROOT / ".mypy-baseline.json"
CHECK_SCRIPT = PROJECT_ROOT / "scripts" / "check_mypy_baseline.py"


def test_mypy_baseline_is_versioned_and_sorted() -> None:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    assert baseline["schema_version"] == 1
    assert baseline["mypy_version"] == "1.20.0"
    diagnostics = baseline["diagnostics"]
    assert isinstance(diagnostics, list)
    assert len(diagnostics) == 89
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
    assert "mypy 类型基线通过：89 条已记录诊断。" in result.stdout
