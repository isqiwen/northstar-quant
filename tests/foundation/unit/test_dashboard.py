import json
import os
from pathlib import Path

import polars as pl
import pytest

from northstar_quant.application import dashboard
from northstar_quant.foundation.common.reporting import REPORT_SCHEMA_VERSION
from northstar_quant.application.dashboard import _build_price_options
from northstar_quant.foundation.platform_support import PlatformSupportError
from northstar_quant.foundation.observability.monitoring.report_catalog import list_recent_report_artifacts


def test_build_price_options_uses_explicit_price_labels_only():
    market_df = pl.DataFrame(
        {
            "date": ["2024-01-02"],
            "symbol": ["RB2405"],
            "close": [100.0],
            "adjusted_close": [98.5],
        }
    )

    options = _build_price_options(market_df, {"price_field": "adjusted_close"})

    assert list(options) == [
        "复权收盘价（adjusted_close）",
        "原始收盘价（close）",
    ]
    assert "研究视角（adjusted_close）" not in options


def test_dashboard_fails_closed_before_loading_settings_on_an_unsupported_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_unsupported_host() -> None:
        raise PlatformSupportError("Northstar Quant 仅支持 Linux x86_64")

    monkeypatch.setattr(dashboard, "require_linux_x86_64", reject_unsupported_host)
    monkeypatch.setattr(
        dashboard,
        "get_settings",
        lambda: (_ for _ in ()).throw(AssertionError("settings must not be loaded")),
    )

    with pytest.raises(PlatformSupportError, match="Linux x86_64"):
        dashboard.render_dashboard()


def test_recent_reports_discovers_only_nested_canonical_artifacts_in_stable_order(
    tmp_path: Path,
):
    reports_dir = tmp_path / "reports"
    older = _write_report_artifact(
        reports_dir,
        "backtest/profile/portfolio/20240101/run-a",
        modified_at_ns=1_700_000_000_000_000_000,
    )
    tied_later_name = _write_report_artifact(
        reports_dir,
        "backtest/profile/portfolio/20240102/run-b",
        modified_at_ns=1_800_000_000_000_000_000,
    )
    tied_earlier_name = _write_report_artifact(
        reports_dir,
        "backtest/profile/portfolio/20240102/run-a",
        modified_at_ns=1_800_000_000_000_000_000,
    )

    (reports_dir / "note.md").write_text("不是报告", encoding="utf-8")
    incomplete_dir = reports_dir / "daily/profile/portfolio/20240102"
    incomplete_dir.mkdir(parents=True)
    (incomplete_dir / "report.md").write_text("不完整", encoding="utf-8")
    invalid_dir = reports_dir / "weekly/profile/portfolio/2024-W01"
    invalid_dir.mkdir(parents=True)
    (invalid_dir / "report.md").write_text("错误制品", encoding="utf-8")
    (invalid_dir / "report.json").write_text(
        json.dumps({"schema_version": REPORT_SCHEMA_VERSION, "artifact_id": "other/path"}),
        encoding="utf-8",
    )

    found = list_recent_report_artifacts(reports_dir)

    assert found == [tied_earlier_name, tied_later_name, older]


def test_recent_reports_ignores_non_utf8_report_json(tmp_path: Path):
    reports_dir = tmp_path / "reports"
    artifact_dir = reports_dir / "daily/profile/portfolio/2024-01-02"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "report.md").write_text("# 报告", encoding="utf-8")
    (artifact_dir / "report.json").write_bytes(b"\xff\xfe\x00")

    assert list_recent_report_artifacts(reports_dir) == []


def _write_report_artifact(
    reports_dir: Path,
    artifact_id: str,
    *,
    modified_at_ns: int,
) -> Path:
    artifact_dir = reports_dir / artifact_id
    artifact_dir.mkdir(parents=True)
    markdown_path = artifact_dir / "report.md"
    markdown_path.write_text("# 报告", encoding="utf-8")
    (artifact_dir / "report.json").write_text(
        json.dumps(
            {
                "schema_version": REPORT_SCHEMA_VERSION,
                "artifact_id": artifact_id,
            }
        ),
        encoding="utf-8",
    )
    os.utime(markdown_path, ns=(modified_at_ns, modified_at_ns))
    return markdown_path
