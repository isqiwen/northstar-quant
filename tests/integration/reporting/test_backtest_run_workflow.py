"""完整离线回测运行制品的端到端回归。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from northstar_quant import cli
from northstar_quant.backtest.runner import run_profile_backtest_run
from northstar_quant.config.settings import get_settings
from northstar_quant.data import downloader, storage
from northstar_quant.data.downloader import download_profile_data
from northstar_quant.reporting import report_builder
from tests.support.futures_actual import actual_futures_frame
from tests.support.paths import PROJECT_ROOT


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

    assert first_run.run_id == second_run.run_id
    assert first_run.manifest["data"]["content_sha256"]
    assert (
        first_run.manifest["data"]["governance"]["source_id"]
        == "akshare_actual_daily_public_v1"
    )
    assert first_run.manifest["strategy"]["target_row_count"] > 0
    assert first_run.analytics["benchmark"]["status"] == "available"
    assert first_run.analytics["execution"]["detail_level"] == "fills_and_target_events"
    assert first_run.metrics["成交事件数"] == len(first_run.result.trades)
    assert first_run.analytics["admission"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert first_run.metrics["研究准入结论"] == "INSUFFICIENT_EVIDENCE"

    report_path = Path(
        report_builder.build_backtest_report(
            "portfolio",
            first_run.metrics,
            first_run.latest_holdings,
            period_label=first_run.period_label,
            artifact_period=first_run.artifact_period,
            profile_id=first_run.profile.profile_id,
            analytics=first_run.analytics,
            benchmark_symbol=first_run.profile.benchmark_symbol,
            backtest_run=first_run.manifest,
        )
    )
    report_data = json.loads(report_path.with_name("report.json").read_text(encoding="utf-8"))
    artifact_manifest = json.loads(
        report_path.with_name("manifest.json").read_text(encoding="utf-8")
    )

    assert report_path.parts[-6:] == (
        "backtest",
        "cn_futures_daily_actual_offline",
        "portfolio",
        first_run.artifact_period,
        first_run.run_id,
        "report.md",
    )
    assert report_data["backtest_run"]["run_id"] == first_run.run_id
    assert artifact_manifest == first_run.manifest
    assert artifact_manifest["output_checksums"]["result_sha256"]
    assert artifact_manifest["research_admission"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert artifact_manifest["effective_configuration"]["research_admission"][
        "policy_id"
    ] == "cn_commodity_futures_research_conservative_v1"
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
    sentinel_pdf = report_path.with_name("report.pdf")
    sentinel_pdf.write_bytes(b"preserve immutable backtest artifact")
    assert (
        report_builder.build_backtest_report(
            "portfolio",
            first_run.metrics,
            first_run.latest_holdings,
            period_label=first_run.period_label,
            artifact_period=first_run.artifact_period,
            profile_id=first_run.profile.profile_id,
            analytics=first_run.analytics,
            benchmark_symbol=first_run.profile.benchmark_symbol,
            backtest_run=first_run.manifest,
        )
        == str(report_path)
    )
    assert report_path.with_name("report.json").read_text(encoding="utf-8") == original_report_json
    assert sentinel_pdf.read_bytes() == b"preserve immutable backtest artifact"

    conflicting_manifest = json.loads(json.dumps(first_run.manifest))
    conflicting_manifest["code"]["package_version"] = "conflicting-version"
    try:
        report_builder.build_backtest_report(
            "portfolio",
            first_run.metrics,
            first_run.latest_holdings,
            period_label=first_run.period_label,
            artifact_period=first_run.artifact_period,
            profile_id=first_run.profile.profile_id,
            analytics=first_run.analytics,
            benchmark_symbol=first_run.profile.benchmark_symbol,
            backtest_run=conflicting_manifest,
        )
    except ValueError as exc:
        assert "清单与现有制品不一致" in str(exc)
    else:
        raise AssertionError("同一 run_id 的不一致清单必须被拒绝")

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
