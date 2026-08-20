"""完整离线回测运行制品的端到端回归。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import northstar_quant.application.cli as cli
from northstar_quant.application.backtest import run_profile_backtest_run
from northstar_quant.research.backtest.models import BacktestContractError
from northstar_quant.platform.config.settings import get_settings
from northstar_quant.data_platform.artifacts import storage
from northstar_quant.data_platform.sources import downloader
from northstar_quant.data_platform.sources.downloader import download_profile_data
from northstar_quant.application import reporting as report_builder
from tests.helpers.futures_actual import actual_futures_frame
from tests.helpers.paths import PROJECT_ROOT


runner = CliRunner()


def test_actual_daily_backtest_run_writes_one_auditable_report_artifact(
    monkeypatch,
    tmp_path: Path,
):
    """数据发布、策略、实际合约回测、清单和报告必须使用同一运行 ID。"""

    isolated_storage = tmp_path / "storage"
    data_settings = get_settings().model_copy(
        update={
            "storage_dir": isolated_storage,
            "downloads_dir": isolated_storage / "downloads",
        }
    )
    report_settings = get_settings().model_copy()
    object.__setattr__(report_settings, "project_root", PROJECT_ROOT)
    object.__setattr__(report_settings, "reports_dir", tmp_path / "reports")
    monkeypatch.setattr(storage, "get_settings", lambda: data_settings)
    monkeypatch.setattr(report_builder, "get_settings", lambda: report_settings)
    monkeypatch.setitem(
        downloader._PROVIDERS,
        "akshare_actual_daily",
        lambda _profile: actual_futures_frame(day_count=70, roll_offset=35),
    )

    download_profile_data("cn_futures_daily_actual_offline")
    first_run = run_profile_backtest_run("cn_futures_daily_actual_offline")
    second_run = run_profile_backtest_run("cn_futures_daily_actual_offline")
    first_manifest = first_run.manifest_mapping()

    assert first_run.run_id == second_run.run_id
    assert first_manifest["data"]["content_sha256"]
    assert (
        first_manifest["data"]["governance"]["source_id"]
        == "akshare_actual_daily_public_v1"
    )
    assert first_manifest["strategy"]["row_count"] > 0
    assert first_run.analytics["benchmark"]["status"] == "available"
    assert first_run.analytics["execution"]["detail_level"] == "fills_and_target_events"
    assert first_run.metrics["成交事件数"] == len(first_run.result.trades)
    assert first_run.analytics["admission"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert first_run.metrics["研究准入结论"] == "INSUFFICIENT_EVIDENCE"

    report_path = Path(
        report_builder.build_backtest_report(first_run)
    )
    report_data = json.loads(report_path.with_name("report.json").read_text(encoding="utf-8"))
    artifact_manifest = json.loads(
        report_path.with_name("manifest.json").read_text(encoding="utf-8")
    )

    assert report_path.parts[-6:] == (
        "backtest",
        "cn_futures_daily_actual_offline",
        "__".join(first_run.selected_strategy_ids),
        first_run.artifact_period,
        first_run.run_id,
        "report.md",
    )
    assert report_data["backtest_run"]["run_id"] == first_run.run_id
    assert artifact_manifest == first_manifest
    assert artifact_manifest["output_checksums"]["result_sha256"]
    assert artifact_manifest["research_admission"]["status"] == "NOT_ELIGIBLE"
    assert artifact_manifest["research_admission"]["observed_policy_status"] == (
        "INSUFFICIENT_EVIDENCE"
    )
    assert artifact_manifest["research_admission"]["policy_id"] == (
        "cn_commodity_futures_research_conservative_v1"
    )
    content = report_path.read_text(encoding="utf-8")
    assert "基准比较与执行审计" in content
    assert "基准状态：可用" in content
    assert "可复现性与审计" in content
    assert "研究准入结论" in content
    assert "INSUFFICIENT_EVIDENCE" in content
    first_holding = first_run.latest_holdings.to_dicts()[0]
    assert (
        f"| {first_holding['symbol']} | {float(first_holding['target_weight']):.2%} |"
        in content
    )

    original_report_json = report_path.with_name("report.json").read_text(
        encoding="utf-8"
    )
    original_markdown = report_path.read_text(encoding="utf-8")
    sentinel_pdf = report_path.with_name("report.pdf")
    sentinel_pdf.write_bytes(b"preserve immutable backtest artifact")
    assert (
        report_builder.build_backtest_report(
            first_run,
        )
        == str(report_path)
    )
    assert report_path.with_name("report.json").read_text(encoding="utf-8") == original_report_json
    assert sentinel_pdf.read_bytes() == b"preserve immutable backtest artifact"

    tampered_report_data = json.loads(original_report_json)
    tampered_report_data["analytics"]["benchmark"]["status"] = "tampered"
    report_path.with_name("report.json").write_text(
        json.dumps(tampered_report_data, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="report.json 已偏离"):
        report_builder.build_backtest_report(first_run)
    report_path.with_name("report.json").write_text(original_report_json, encoding="utf-8")

    report_path.write_text("tampered markdown", encoding="utf-8")
    with pytest.raises(ValueError, match="report.md 已偏离"):
        report_builder.build_backtest_report(first_run)
    report_path.write_text(original_markdown, encoding="utf-8")

    linked_tamper = json.loads(original_report_json)
    linked_tamper["profile_id"] = "forged-profile"
    linked_markdown = original_markdown.replace(
        "cn_futures_daily_actual_offline", "forged-profile", 1
    )
    linked_tamper["markdown_sha256"] = hashlib.sha256(
        linked_markdown.encode("utf-8")
    ).hexdigest()
    report_path.with_name("report.json").write_text(
        json.dumps(linked_tamper, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report_path.write_text(linked_markdown, encoding="utf-8")
    with pytest.raises(ValueError, match="report.json 已偏离"):
        report_builder.build_backtest_report(first_run)
    report_path.with_name("report.json").write_text(original_report_json, encoding="utf-8")
    report_path.write_text(original_markdown, encoding="utf-8")

    conflicting_manifest = json.loads(json.dumps(first_manifest))
    conflicting_manifest["code"]["package_version"] = "conflicting-version"
    with pytest.raises(TypeError, match="BacktestRun"):
        report_builder.build_backtest_report(conflicting_manifest)

    cli_result = runner.invoke(
        cli.app,
        [
            "backtest",
            "run",
            "portfolio",
            "--profile",
            "cn_futures_daily_actual_offline",
        ],
    )

    assert cli_result.exit_code == 0, cli_result.output
    cli_report_data = json.loads(
        report_path.with_name("report.json").read_text(encoding="utf-8")
    )
    assert cli_report_data["backtest_run"]["run_id"] == first_run.run_id

    assess_result = runner.invoke(
        cli.app,
        [
            "research",
            "assess",
            "portfolio",
            "--profile",
            "cn_futures_daily_actual_offline",
        ],
    )
    assert assess_result.exit_code == 0, assess_result.output
    assert "INSUFFICIENT_EVIDENCE" in assess_result.output

    require_pass_result = runner.invoke(
        cli.app,
        [
            "research",
            "assess",
            "portfolio",
            "--profile",
            "cn_futures_daily_actual_offline",
            "--require-pass",
        ],
    )
    assert require_pass_result.exit_code == 2

    first_run.analytics["tampered_after_manifest"] = True
    with pytest.raises(BacktestContractError, match="analytics"):
        report_builder.build_backtest_report(
            first_run,
        )
