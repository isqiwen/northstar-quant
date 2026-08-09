import json
from pathlib import Path

import polars as pl
import pytest
from tests.support.paths import PROJECT_ROOT

from northstar_quant.backtest.event_engine import BacktestResult
from northstar_quant.config.settings import get_settings
from northstar_quant.reporting import pdf_renderer, report_builder


def test_periodic_backtest_views_use_distinct_period_windows():
    result = BacktestResult(
        total_return=0.10,
        annualized_return=0.20,
        max_drawdown=-0.05,
        turnover_estimate=0.30,
        equity_curve=[
            {"date": "2023-12-29", "equity": 0.98},
            {"date": "2024-01-31", "equity": 1.00},
            {"date": "2024-02-01", "equity": 1.01},
            {"date": "2024-02-02", "equity": 1.02},
            {"date": "2024-02-05", "equity": 1.04},
            {"date": "2024-02-06", "equity": 1.03},
            {"date": "2024-02-07", "equity": 1.05},
        ],
        monthly_returns=[
            {"month": "2023-12", "return": -0.02},
            {"month": "2024-01", "return": 0.01},
            {"month": "2024-02", "return": 0.05},
        ],
        turnover_curve=[
            {"date": "2023-12-29", "turnover": 0.05},
            {"date": "2024-01-31", "turnover": 0.1},
            {"date": "2024-02-01", "turnover": 0.2},
            {"date": "2024-02-02", "turnover": 0.3},
            {"date": "2024-02-05", "turnover": 0.4},
            {"date": "2024-02-06", "turnover": 0.5},
            {"date": "2024-02-07", "turnover": 0.6},
        ],
    )

    daily = report_builder.build_periodic_backtest_view(result, "daily")
    weekly = report_builder.build_periodic_backtest_view(result, "weekly")
    monthly = report_builder.build_periodic_backtest_view(result, "monthly")
    yearly = report_builder.build_periodic_backtest_view(result, "yearly")

    assert daily["metrics"]["期间收益率"] == pytest.approx(1.05 / 1.03 - 1.0)
    assert weekly["metrics"]["期间收益率"] == pytest.approx(1.05 / 1.02 - 1.0)
    assert monthly["metrics"]["期间收益率"] == pytest.approx(1.05 / 1.00 - 1.0)
    assert yearly["metrics"]["期间收益率"] == pytest.approx(1.05 / 0.98 - 1.0)
    assert daily["artifact_period"] == "20240207"
    assert weekly["artifact_period"] == "2024-W06"
    assert monthly["artifact_period"] == "2024-02"
    assert yearly["artifact_period"] == "2024"
    assert yearly["period_label"] == "2024年（截至 2024-02-07）"
    assert daily["metrics"]["期间观测数"] == 1
    assert weekly["metrics"]["期间观测数"] == 3
    assert monthly["metrics"]["期间观测数"] == 5
    assert yearly["metrics"]["期间观测数"] == 6
    assert monthly["analytics"]["monthly_returns"] == [
        {"month": "2024-02", "return": 0.05}
    ]
    assert yearly["analytics"]["monthly_returns"] == [
        {"month": "2024-01", "return": 0.01},
        {"month": "2024-02", "return": 0.05},
    ]


def test_yearly_report_uses_year_directory_and_template(tmp_path, monkeypatch):
    settings = get_settings().model_copy()
    object.__setattr__(settings, "project_root", PROJECT_ROOT)
    object.__setattr__(settings, "reports_dir", tmp_path / "reports")
    monkeypatch.setattr(report_builder, "get_settings", lambda: settings)

    report_path = Path(
        report_builder.build_markdown_report(
            report_type="yearly",
            strategy_id="portfolio",
            metrics={"期间收益率": 0.12},
            period_label="2024年（截至 2024-02-07）",
            artifact_period="2024",
            profile_id="cn_futures_daily_trend_offline",
        )
    )

    assert report_path.parts[-5:] == (
        "yearly",
        "cn_futures_daily_trend_offline",
        "portfolio",
        "2024",
        "report.md",
    )
    assert report_path.read_text(encoding="utf-8").startswith(
        "# Northstar Quant 年报"
    )
    assert pdf_renderer.parse_markdown_report(report_path).meta.report_type == "年报"


def test_backtest_report_filename_identifies_context(tmp_path, monkeypatch):
    settings = get_settings().model_copy()
    object.__setattr__(settings, "project_root", PROJECT_ROOT)
    object.__setattr__(settings, "reports_dir", tmp_path / "reports")
    monkeypatch.setattr(report_builder, "get_settings", lambda: settings)

    report_path = report_builder.build_markdown_report(
        report_type="backtest",
        strategy_id="portfolio",
        metrics={"total_return": -0.29},
        period_label="2015-01-05 至 2026-07-30",
        artifact_period="20150105-20260730",
        profile_id="cn_futures_daily_trend_offline",
        analytics={"equity_curve": [{"date": "2026-07-30", "equity": 0.71}]},
    )

    path = Path(report_path)
    content = path.read_text(encoding="utf-8")

    assert path.name == "report.md"
    assert path.parts[-5:] == (
        "backtest",
        "cn_futures_daily_trend_offline",
        "portfolio",
        "20150105-20260730",
        "report.md",
    )
    assert content.startswith("# Northstar Quant 期货回测报告")
    assert "- 画像：cn_futures_daily_trend_offline" in content
    assert "图表数据" not in content
    assert "```json" not in content
    data = json.loads(path.with_name("report.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == "northstar_report_v1"
    assert data["report_type"] == "backtest"
    assert data["metrics"]["total_return"] == -0.29
    parsed = pdf_renderer.parse_markdown_report(path)
    assert parsed.analytics == {
        "equity_curve": [{"date": "2026-07-30", "equity": 0.71}]
    }

    path.with_name("report.json").unlink()
    with pytest.raises(FileNotFoundError, match="缺少结构化数据文件"):
        pdf_renderer.parse_markdown_report(path)

    stale_pdf = path.with_name("report.pdf")
    stale_pdf.write_bytes(b"stale")
    report_builder.build_markdown_report(
        report_type="backtest",
        strategy_id="portfolio",
        metrics={"total_return": -0.28},
        period_label="2015-01-05 至 2026-07-30",
        artifact_period="20150105-20260730",
        profile_id="cn_futures_daily_trend_offline",
    )

    assert not stale_pdf.exists()


@pytest.mark.parametrize(
    ("case", "target_weights"),
    [
        ("flat", [0.0, 0.0]),
        ("all-short", [-0.10, -0.20]),
        ("long-short", [0.10, -0.20]),
    ],
)
def test_pdf_report_renders_flat_and_signed_target_weights(
    tmp_path,
    monkeypatch,
    case,
    target_weights,
):
    """空仓、全空和多空组合都必须能生成可归档 PDF。"""

    settings = get_settings().model_copy()
    object.__setattr__(settings, "project_root", PROJECT_ROOT)
    object.__setattr__(settings, "reports_dir", tmp_path / "reports")
    monkeypatch.setattr(report_builder, "get_settings", lambda: settings)
    holdings = pl.DataFrame(
        {
            "symbol": ["RB_CONT", "CU_CONT"],
            "target_weight": target_weights,
            "signal_value": [1.0, -1.0],
        }
    )
    report_path = report_builder.build_backtest_report(
        "futures_trend",
        {
            "total_return": -0.20,
            "annualized_return": -0.30,
            "max_drawdown": -0.25,
        },
        holdings,
        artifact_period=f"pdf-{case}",
        profile_id="cn_futures_daily_trend_offline",
        analytics={
            "equity_curve": [
                {"date": "2024-01-01", "equity": 1.0},
                {"date": "2024-01-02", "equity": 0.8},
            ],
            "drawdown_curve": [
                {"date": "2024-01-01", "drawdown": 0.0},
                {"date": "2024-01-02", "drawdown": -0.2},
            ],
            "monthly_returns": [{"month": "2024-01", "return": -0.2}],
        },
    )

    pdf_path = Path(pdf_renderer.markdown_to_pdf(report_path))

    assert pdf_path.is_file()
    assert pdf_path.stat().st_size > 0


def test_holding_charts_preserve_short_direction():
    holdings = [
        {"symbol": "RB_CONT", "target_weight": "-0.10"},
        {"symbol": "CU_CONT", "target_weight": "0.20"},
    ]

    pie_drawing = pdf_renderer._build_holdings_pie_chart(holdings, "STSong-Light")
    pie = next(item for item in pie_drawing.contents if isinstance(item, pdf_renderer.Pie))
    assert pie.data == [0.10, 0.20]
    assert pie.labels == ["RB_CONT 空头", "CU_CONT 多头"]

    bar_drawing = pdf_renderer._build_holdings_bar_chart(holdings, "STSong-Light")
    bar = next(
        item for item in bar_drawing.contents if isinstance(item, pdf_renderer.VerticalBarChart)
    )
    assert bar.valueAxis.valueMin < 0
    assert bar.valueAxis.valueMax > 0


def test_equity_chart_adds_aligned_benchmark_curve_when_available():
    drawing = pdf_renderer._build_equity_curve_chart(
        {
            "equity_curve": [
                {"date": "2024-01-02", "equity": 1.0},
                {"date": "2024-01-03", "equity": 1.1},
            ],
            "benchmark": {
                "status": "available",
                "equity_curve": [
                    {"date": "2024-01-02", "equity": 1.0},
                    {"date": "2024-01-03", "equity": 1.05},
                ],
            },
        },
        "STSong-Light",
    )

    chart = next(
        item for item in drawing.contents if isinstance(item, pdf_renderer.LinePlot)
    )
    assert len(chart.data) == 2
