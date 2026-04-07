"""中文报告构建模块。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import polars as pl
from jinja2 import Environment, FileSystemLoader

from northstar_quant.backtest.registry import run_target_backtest
from northstar_quant.common.enums import StrategyOutputType
from northstar_quant.config.settings import get_settings
from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.data.storage import load_profile_signal_data
from northstar_quant.strategies.registry import build_strategy

_TEMPLATE_MAP = {
    "daily": "daily_report.md.j2",
    "weekly": "weekly_report.md.j2",
    "monthly": "monthly_report.md.j2",
}


def build_markdown_report(
    report_type: str,
    strategy_id: str,
    metrics: dict,
    holdings: pl.DataFrame | None = None,
    period_label: str | None = None,
    analytics: dict | None = None,
    benchmark_symbol: str | None = None,
) -> str:
    """生成中文 Markdown 报告。"""

    settings = get_settings()
    settings.reports_dir.mkdir(parents=True, exist_ok=True)

    env = Environment(loader=FileSystemLoader(settings.project_root / "templates"))
    template = env.get_template(_TEMPLATE_MAP[report_type])

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "period_label": period_label or report_type,
        "strategy_id": strategy_id,
        "metrics": metrics,
        "benchmark_symbol": benchmark_symbol or settings.report_benchmark_symbol,
        "holdings": [] if holdings is None else holdings.to_dicts(),
        "analytics_json": json.dumps(analytics or {}, ensure_ascii=False, indent=2),
    }

    output = template.render(**payload)
    path = settings.reports_dir / f"{strategy_id}_{report_type}_report.md"
    Path(path).write_text(output, encoding="utf-8")
    return str(path)


def write_markdown_report(
    strategy_id: str,
    metrics: dict,
    holdings: pl.DataFrame | None = None,
    analytics: dict | None = None,
    benchmark_symbol: str | None = None,
) -> str:
    """兼容旧接口：默认生成日报。"""

    return build_markdown_report(
        "daily",
        strategy_id,
        metrics,
        holdings,
        analytics=analytics,
        benchmark_symbol=benchmark_symbol,
    )


def build_periodic_report_only(
    report_type: str,
    strategy: str = "etf_rotation",
    profile_id: str | None = None,
) -> str:
    """仅生成周期报告，供调度器调用。"""


    profile = load_trading_profile(profile_id)
    market_df = load_profile_signal_data(profile)
    strategy_obj = build_strategy(strategy)
    if strategy_obj.output_type != StrategyOutputType.TARGET_WEIGHT:
        raise ValueError(
            f"策略 {strategy} 的输出类型为 {strategy_obj.output_type.value}，当前周期报告仅支持 target_weight 型策略。"
        )
    targets = strategy_obj.generate_output(market_df)
    holdings = strategy_obj.latest_output(targets)
    result = run_target_backtest(profile, market_df, targets)

    if report_type == "daily":
        period_label = datetime.now().strftime("%Y-%m-%d")
    elif report_type == "weekly":
        period_label = datetime.now().strftime("%Y 第%W周")
    else:
        period_label = datetime.now().strftime("%Y-%m")

    return build_markdown_report(
        report_type=report_type,
        strategy_id=strategy,
        metrics={
            "total_return": result.total_return,
            "annualized_return": result.annualized_return,
            "max_drawdown": result.max_drawdown,
            "turnover_estimate": result.turnover_estimate,
        },
        holdings=holdings,
        period_label=period_label,
        analytics={
            "equity_curve": result.equity_curve,
            "drawdown_curve": result.drawdown_curve,
            "monthly_returns": result.monthly_returns,
        },
        benchmark_symbol=profile.benchmark_symbol,
    )
