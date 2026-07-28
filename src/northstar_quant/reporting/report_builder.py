"""中文报告构建模块。"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TypedDict

import polars as pl
from jinja2 import Environment, FileSystemLoader

from northstar_quant.backtest.event_engine import BacktestResult
from northstar_quant.backtest.registry import run_target_backtest
from northstar_quant.common.time import utc_now
from northstar_quant.config.settings import get_settings
from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.data.storage import load_profile_signal_data
from northstar_quant.db.repositories import (
    list_recent_account_attributions,
    replace_anomaly_events_for_account_attribution,
)
from northstar_quant.db.session import SessionLocal
from northstar_quant.logging_.logger import get_logger
from northstar_quant.monitoring.run_health import soak_summary
from northstar_quant.strategies.pipeline import (
    latest_pipeline_output,
    parse_strategy_selection,
    run_profile_strategy_pipeline,
)

_TEMPLATE_MAP = {
    "daily": "daily_report.md.j2",
    "weekly": "weekly_report.md.j2",
    "monthly": "monthly_report.md.j2",
}


class PeriodicBacktestView(TypedDict):
    """周期报告需要的强类型回测视图。"""

    period_label: str
    metrics: dict[str, float]
    analytics: dict[str, object]
logger = get_logger(__name__, command="report.build")


def _format_report_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S %Z")


def _non_zero_component_rows(
    components: list[tuple[str, float | None]],
) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    for label, value in components:
        if value is None or abs(float(value)) <= 1e-8:
            continue
        rows.append({"label": label, "value": float(value)})
    return rows


def _format_amount(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):,.2f}"


def _format_bps(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.1f}"


def _largest_named_component(
    components: list[tuple[str, float | None]],
) -> tuple[str, float] | None:
    ranked = [
        (label, abs(float(value)))
        for label, value in components
        if value is not None and abs(float(value)) > 1e-8
    ]
    if not ranked:
        return None
    label, magnitude = max(ranked, key=lambda item: item[1])
    return label, magnitude


def _residual_alert_threshold(equity_change: float, *, absolute_floor: float, ratio: float) -> float:
    return max(float(absolute_floor), abs(float(equity_change)) * float(ratio))


def _equity_scaled_alert_threshold(
    equity_base: float,
    *,
    absolute_floor: float,
    ratio: float,
) -> float:
    return max(float(absolute_floor), abs(float(equity_base)) * float(ratio))


def _build_daily_alert_items(
    live_account_attribution: dict[str, object] | None,
    *,
    shortfall_bps_threshold: float,
    residual_abs_threshold: float,
    residual_ratio_threshold: float,
    funding_abs_threshold: float,
    funding_ratio_threshold: float,
) -> list[dict[str, str]]:
    if not live_account_attribution:
        return []

    starting_equity = float(live_account_attribution.get("starting_equity") or 0.0)
    ending_equity = float(live_account_attribution.get("ending_equity") or 0.0)
    execution_shortfall = float(live_account_attribution.get("execution_shortfall") or 0.0)
    traded_notional = float(live_account_attribution.get("traded_notional") or 0.0)
    equity_change = float(live_account_attribution.get("equity_change") or 0.0)
    residual_pnl = float(live_account_attribution.get("residual_pnl") or 0.0)
    funding_cash_flow = float(live_account_attribution.get("funding_cash_flow") or 0.0)
    alert_items: list[dict[str, str]] = []

    if traded_notional > 1e-8:
        shortfall_bps = execution_shortfall / traded_notional * 10000.0
        if shortfall_bps >= float(shortfall_bps_threshold):
            alert_items.append(
                {
                    "code": "execution_shortfall",
                    "tag": "执行异常",
                    "severity": "warning",
                    "message": (
                        f"执行损耗达到 {_format_amount(execution_shortfall)}，约 {shortfall_bps:.1f} bps，"
                        f"已高于 {shortfall_bps_threshold:.1f} bps 阈值。"
                    ),
                }
            )

    residual_threshold = _residual_alert_threshold(
        equity_change,
        absolute_floor=residual_abs_threshold,
        ratio=residual_ratio_threshold,
    )
    if abs(residual_pnl) >= residual_threshold:
        alert_items.append(
            {
                "code": "residual_pnl",
                "tag": "账本异常",
                "severity": "warning",
                "message": (
                    f"未解释剩余达到 {_format_amount(residual_pnl)}，已高于 {_format_amount(residual_threshold)} 阈值，"
                    "建议优先排查现金流水、费用和公司行为。"
                ),
            }
        )

    funding_threshold = _equity_scaled_alert_threshold(
        max(abs(starting_equity), abs(ending_equity)),
        absolute_floor=funding_abs_threshold,
        ratio=funding_ratio_threshold,
    )
    if abs(funding_cash_flow) >= funding_threshold:
        alert_items.append(
            {
                "code": "funding_cash_flow",
                "tag": "资金异常",
                "severity": "warning",
                "message": (
                    f"资金划转达到 {_format_amount(funding_cash_flow)}，已高于 {_format_amount(funding_threshold)} 阈值。"
                ),
            }
        )
    return alert_items


def _render_alert_lines(alert_items: list[dict[str, str]]) -> list[str]:
    return [f"[{item['tag']}] {item['message']}" for item in alert_items]


def _alert_tags(alert_items: list[dict[str, str]]) -> list[str]:
    tags: list[str] = []
    for item in alert_items:
        tag = str(item.get("tag") or "").strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _alert_tag_summary(tags: list[str]) -> str:
    return "".join(f"[{tag}]" for tag in tags)


def _resolve_alert_tag_summary(live_account_attribution: dict[str, object] | None) -> str:
    if not live_account_attribution:
        return ""
    tag_summary = str(live_account_attribution.get("alert_tag_summary") or "").strip()
    if tag_summary:
        return tag_summary
    alert_items = [
        item
        for item in (live_account_attribution.get("alert_items") or [])
        if isinstance(item, dict)
    ]
    return _alert_tag_summary(_alert_tags(alert_items))


def _run_health_mode_label(mode: str) -> str:
    mapping = {
        "paper_soak": "Paper Soak（本地仿真账户）",
        "shadow_run": "Shadow Run（只建计划不下单）",
        "live_run": "Live Run（真实执行）",
    }
    return mapping.get(mode, mode)


def _run_health_trend_label(trend: str | None) -> str:
    mapping = {"down": "下降", "up": "上升", "flat": "持平"}
    return mapping.get(str(trend or "").strip().lower(), "未知")


def _build_run_health_summary_lines(summary: dict[str, object]) -> list[str]:
    days = int(summary.get("days") or 0)
    run_count = int(summary.get("run_count") or 0)
    preflight_pass_count = int(summary.get("preflight_pass_count") or 0)
    blocked_run_count = int(summary.get("blocked_run_count") or 0)
    plan_consistency_issue_run_count = int(summary.get("plan_consistency_issue_run_count") or 0)
    open_order_run_count = int(summary.get("open_order_run_count") or 0)
    partial_fill_run_count = int(summary.get("partial_fill_run_count") or 0)
    anomaly_recent_7d = int(summary.get("anomaly_events_recent_7d") or 0)
    anomaly_prev_7d = int(summary.get("anomaly_events_prev_7d") or 0)
    avg_abs_execution_shortfall_bps = summary.get("avg_abs_execution_shortfall_bps")
    avg_abs_residual_pnl = summary.get("avg_abs_residual_pnl")
    latest_runs = [
        row for row in (summary.get("latest_runs") or []) if isinstance(row, dict)
    ]

    if run_count <= 0:
        return [f"近 {days} 天暂无可用样本。"]

    pass_rate = preflight_pass_count / run_count * 100.0
    lines = [
        (
            f"近 {days} 天共运行 {run_count} 次，preflight 通过 {preflight_pass_count} 次，"
            f"阻止 {blocked_run_count} 次，通过率 {pass_rate:.0f}%。"
        ),
        (
            f"target 与 execution plan 不一致的运行有 {plan_consistency_issue_run_count} 次，"
            f"open order 干扰 {open_order_run_count} 次，partial fill 干扰 {partial_fill_run_count} 次。"
        ),
        (
            f"平均绝对执行损耗 {_format_bps(avg_abs_execution_shortfall_bps)} bps，"
            f"平均绝对 residual {_format_amount(avg_abs_residual_pnl)}。"
        ),
        (
            f"最近 7 天异常事件 {anomaly_recent_7d} 次，前 7 天 {anomaly_prev_7d} 次，"
            f"趋势 {_run_health_trend_label(str(summary.get('anomaly_trend') or ''))}。"
        ),
    ]
    if latest_runs:
        latest = latest_runs[0]
        latest_created_at = str(latest.get("created_at") or "N/A")
        latest_can_trade = bool(latest.get("preflight_can_trade"))
        lines.append(
            (
                f"最近一次运行发生在 {latest_created_at}，"
                f"preflight {'通过' if latest_can_trade else '阻止'}，"
                f"计划 {int(latest.get('execution_plan_count') or 0)} 笔。"
            )
        )
    return lines


def rolling_run_health_summaries(
    *,
    profile_id: str,
    account: str | None = None,
    days: int = 28,
    modes: tuple[str, ...] = ("paper_soak", "shadow_run"),
) -> list[dict[str, object]]:
    """构建滚动运行健康摘要，供周期报告直接引用。"""

    summaries: list[dict[str, object]] = []
    for mode in modes:
        summary = soak_summary(
            days=days,
            limit=5,
            profile_id=profile_id,
            account=account,
            mode=mode,
        )
        summaries.append(
            {
                **summary,
                "mode_label": _run_health_mode_label(mode),
                "summary_lines": _build_run_health_summary_lines(summary),
            }
        )
    return summaries


def _build_daily_recap_lines(
    live_account_attribution: dict[str, object] | None,
    *,
    shortfall_bps_threshold: float,
    residual_abs_threshold: float,
    residual_ratio_threshold: float,
) -> list[str]:
    if not live_account_attribution:
        return ["当日尚无可用的账户归因样本，邮件正文暂不输出自动复盘结论。"]

    equity_change = float(live_account_attribution.get("equity_change") or 0.0)
    price_pnl = float(live_account_attribution.get("price_pnl") or 0.0)
    rebalance_pnl = float(live_account_attribution.get("rebalance_pnl") or 0.0)
    execution_shortfall = float(live_account_attribution.get("execution_shortfall") or 0.0)
    total_non_trade = float(live_account_attribution.get("total_non_trade_cash_flow") or 0.0)
    traded_notional = float(live_account_attribution.get("traded_notional") or 0.0)
    fill_count = int(live_account_attribution.get("fill_count") or 0)
    residual_pnl = float(live_account_attribution.get("residual_pnl") or 0.0)

    lines = [
        (
            f"本期账户权益变动 {_format_amount(equity_change)}，其中价格变动贡献 {_format_amount(price_pnl)}，"
            f"调仓贡献 {_format_amount(rebalance_pnl)}，非交易现金流 {_format_amount(total_non_trade)}。"
        )
    ]

    non_trade_components = [
        ("利息", live_account_attribution.get("interest_cash_flow")),
        ("费用", live_account_attribution.get("fee_cash_flow")),
        ("税费", live_account_attribution.get("tax_cash_flow")),
        ("资金划转", live_account_attribution.get("funding_cash_flow")),
        ("其他非交易项", live_account_attribution.get("other_non_trade_cash_flow")),
    ]
    top_non_trade = _largest_named_component(non_trade_components)
    if top_non_trade is not None:
        lines.append(
            f"非交易现金流中影响最大的是 {top_non_trade[0]}，绝对金额约 {_format_amount(top_non_trade[1])}。"
        )

    if traded_notional > 1e-8:
        shortfall_bps = execution_shortfall / traded_notional * 10000.0
        execution_tone = (
            "执行损耗偏高，需要复盘限价与追价节奏。"
            if shortfall_bps >= float(shortfall_bps_threshold)
            else "执行偏差可控。"
        )
        lines.append(
            f"本期共成交 {fill_count} 笔，名义成交额 {_format_amount(traded_notional)}，"
            f"执行损耗 {_format_amount(execution_shortfall)}，约 {shortfall_bps:.1f} bps，{execution_tone}"
        )
    else:
        lines.append("本期没有新增成交，账户变化主要来自价格波动与非交易现金流。")

    residual_tolerance = _residual_alert_threshold(
        equity_change,
        absolute_floor=residual_abs_threshold,
        ratio=residual_ratio_threshold,
    )
    if abs(residual_pnl) <= residual_tolerance:
        lines.append(f"剩余未解释项 {_format_amount(residual_pnl)}，当前账本闭环基本正常。")
    else:
        lines.append(
            f"剩余未解释项 {_format_amount(residual_pnl)} 偏大，建议继续排查费用、公司行为或账户现金流水。"
        )

    return lines


def latest_live_account_attribution_summary(
    *,
    profile_id: str | None = None,
    account: str | None = None,
) -> dict[str, object] | None:
    """读取最新一段账户归因，用于日报文本。"""

    try:
        with SessionLocal() as session:
            rows = list_recent_account_attributions(
                session,
                limit=1,
                profile_id=profile_id,
                account=account,
            )
    except Exception as exc:
        logger.bind(profile=profile_id, account=account).warning(
            "读取最新账户归因失败，日报将跳过该部分: %s",
            exc,
        )
        return None

    if not rows:
        return None

    row = rows[0]
    settings = get_settings()
    summary_payload = {
        "starting_equity": row.starting_equity,
        "ending_equity": row.ending_equity,
        "equity_change": row.equity_change,
        "price_pnl": row.price_pnl,
        "rebalance_pnl": row.rebalance_pnl,
        "execution_shortfall": row.execution_shortfall,
        "interest_cash_flow": row.interest_cash_flow,
        "fee_cash_flow": row.fee_cash_flow,
        "tax_cash_flow": row.tax_cash_flow,
        "funding_cash_flow": row.funding_cash_flow,
        "other_non_trade_cash_flow": row.other_non_trade_cash_flow,
        "total_non_trade_cash_flow": row.total_non_trade_cash_flow,
        "traded_notional": row.traded_notional,
        "fill_count": row.fill_count,
        "residual_pnl": row.residual_pnl,
    }
    cash_flow_rows = _non_zero_component_rows(
        [
            ("利息现金流", row.interest_cash_flow),
            ("费用现金流", row.fee_cash_flow),
            ("税费现金流", row.tax_cash_flow),
            ("资金划转", row.funding_cash_flow),
            ("其他非交易现金流", row.other_non_trade_cash_flow),
        ]
    )
    alert_items = _build_daily_alert_items(
        summary_payload,
        shortfall_bps_threshold=settings.report_recap_execution_shortfall_alert_bps,
        residual_abs_threshold=settings.report_recap_residual_abs_alert,
        residual_ratio_threshold=settings.report_recap_residual_ratio_alert,
        funding_abs_threshold=settings.report_recap_funding_abs_alert,
        funding_ratio_threshold=settings.report_recap_funding_ratio_alert,
    )
    alert_tags = _alert_tags(alert_items)
    alert_tag_summary = _alert_tag_summary(alert_tags)

    return {
        "account_attribution_id": row.id,
        "profile_id": row.profile_id,
        "account": row.account,
        "run_id": row.run_id,
        "start_asof": _format_report_datetime(row.start_asof),
        "end_asof": _format_report_datetime(row.end_asof),
        "starting_equity": row.starting_equity,
        "ending_equity": row.ending_equity,
        "equity_change": row.equity_change,
        "starting_cash": row.starting_cash,
        "ending_cash": row.ending_cash,
        "cash_change": row.cash_change,
        "price_pnl": row.price_pnl,
        "rebalance_pnl": row.rebalance_pnl,
        "execution_shortfall": row.execution_shortfall,
        "interest_cash_flow": row.interest_cash_flow,
        "fee_cash_flow": row.fee_cash_flow,
        "tax_cash_flow": row.tax_cash_flow,
        "funding_cash_flow": row.funding_cash_flow,
        "other_non_trade_cash_flow": row.other_non_trade_cash_flow,
        "total_non_trade_cash_flow": row.total_non_trade_cash_flow,
        "traded_notional": row.traded_notional,
        "fill_count": row.fill_count,
        "residual_pnl": row.residual_pnl,
        "cash_flow_rows": cash_flow_rows,
        "alert_items": alert_items,
        "alert_tags": alert_tags,
        "alert_tag_summary": alert_tag_summary,
        "alert_lines": _render_alert_lines(alert_items),
        "recap_lines": _build_daily_recap_lines(
            summary_payload,
            shortfall_bps_threshold=settings.report_recap_execution_shortfall_alert_bps,
            residual_abs_threshold=settings.report_recap_residual_abs_alert,
            residual_ratio_threshold=settings.report_recap_residual_ratio_alert,
        ),
    }


def build_daily_alert_notification(
    report_path: str | Path,
    live_account_attribution: dict[str, object] | None,
) -> str | None:
    """把日报异常归因整理成一条可直接推送的摘要。"""

    if not live_account_attribution:
        return None

    alert_lines = [
        str(line).strip()
        for line in (live_account_attribution.get("alert_lines") or [])
        if str(line).strip()
    ]
    alert_items = [
        item
        for item in (live_account_attribution.get("alert_items") or [])
        if isinstance(item, dict) and str(item.get("message") or "").strip()
    ]
    if not alert_lines and not alert_items:
        return None

    lines = ["日报检测到异常归因。"]
    tag_summary = _resolve_alert_tag_summary(live_account_attribution)
    if tag_summary:
        lines[0] = f"日报检测到异常归因 {tag_summary}。"
    profile_id = live_account_attribution.get("profile_id")
    if profile_id:
        lines.append(f"画像：{profile_id}")
    account = live_account_attribution.get("account")
    if account:
        lines.append(f"账户：{account}")
    start_asof = live_account_attribution.get("start_asof")
    end_asof = live_account_attribution.get("end_asof")
    if start_asof and end_asof:
        lines.append(f"区间：{start_asof} -> {end_asof}")
    lines.append(f"报告：{Path(report_path)}")
    if alert_items:
        lines.extend(f"- [{item['tag']}] {item['message']}" for item in alert_items)
    else:
        lines.extend(f"- {line}" for line in alert_lines)
    return "\n".join(lines)


def build_report_email_subject(
    *,
    report_type: str,
    report_path: str | Path,
    live_account_attribution: dict[str, object] | None = None,
    subject_prefix: str | None = None,
) -> str:
    """为报告邮件生成更容易扫描的主题。"""

    settings = get_settings()
    prefix = subject_prefix or settings.report_email_subject_prefix
    report_type_label = {"daily": "日报", "weekly": "周报", "monthly": "月报"}.get(
        report_type,
        report_type,
    )
    tag_summary = _resolve_alert_tag_summary(live_account_attribution)
    middle = f"{report_type_label} {tag_summary}".strip()
    return f"{prefix} - {middle} - {Path(report_path).stem}"


def record_daily_anomaly_events(
    report_path: str | Path,
    live_account_attribution: dict[str, object] | None,
) -> dict[str, int]:
    """把日报里的异常项落为结构化事件。"""

    if not live_account_attribution:
        return {"deleted": 0, "created": 0}

    account_attribution_id = live_account_attribution.get("account_attribution_id")
    if account_attribution_id is None:
        return {"deleted": 0, "created": 0}

    with SessionLocal() as session:
        return replace_anomaly_events_for_account_attribution(
            session,
            account_attribution_id=int(account_attribution_id),
            profile_id=str(live_account_attribution.get("profile_id") or "") or None,
            account=str(live_account_attribution.get("account") or "") or None,
            run_id=str(live_account_attribution.get("run_id") or "") or None,
            report_type="daily",
            report_path=str(report_path),
            detected_at=utc_now(),
            alert_items=list(live_account_attribution.get("alert_items") or []),
        )


def build_markdown_report(
    report_type: str,
    strategy_id: str,
    metrics: dict,
    holdings: pl.DataFrame | None = None,
    period_label: str | None = None,
    analytics: dict | None = None,
    benchmark_symbol: str | None = None,
    live_account_attribution: dict[str, object] | None = None,
    run_health_summaries: list[dict[str, object]] | None = None,
    run_health_days: int | None = None,
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
        "live_account_attribution": live_account_attribution,
        "run_health_summaries": run_health_summaries or [],
        "run_health_days": run_health_days,
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
    live_account_attribution: dict[str, object] | None = None,
) -> str:
    """兼容旧接口：默认生成日报。"""

    return build_markdown_report(
        "daily",
        strategy_id,
        metrics,
        holdings,
        analytics=analytics,
        benchmark_symbol=benchmark_symbol,
        live_account_attribution=live_account_attribution,
    )


def build_periodic_backtest_view(
    result: BacktestResult,
    report_type: str,
    *,
    periods_per_year: int = 252,
) -> PeriodicBacktestView:
    """从完整回测曲线提取日报、周报或月报对应的真实期间指标。"""

    if report_type not in _TEMPLATE_MAP:
        raise ValueError(f"不支持的报告类型：{report_type}")
    if not result.equity_curve:
        raise ValueError("回测结果缺少净值曲线，无法生成周期报告")

    curve = sorted(
        (
            date.fromisoformat(str(item["date"])[:10]),
            float(item["equity"]),
        )
        for item in result.equity_curve
    )
    end_date = curve[-1][0]
    if report_type == "daily":
        boundary = end_date
        period_label = end_date.isoformat()
    elif report_type == "weekly":
        boundary = end_date - timedelta(days=end_date.weekday())
        iso_year, iso_week, _ = end_date.isocalendar()
        period_label = (
            f"{iso_year}年第{iso_week:02d}周"
            f"（{boundary.isoformat()} 至 {end_date.isoformat()}）"
        )
    else:
        boundary = end_date.replace(day=1)
        period_label = end_date.strftime("%Y-%m")

    first_index = next(
        index
        for index, (current_date, _) in enumerate(curve)
        if current_date >= boundary
    )
    scoped_curve = curve[first_index:]
    baseline_equity = curve[first_index - 1][1] if first_index > 0 else 1.0
    if baseline_equity <= 0:
        raise ValueError("周期报告基准权益必须大于 0")

    normalized_curve = [
        (current_date, equity / baseline_equity)
        for current_date, equity in scoped_curve
    ]
    running_max = 1.0
    drawdown_rows: list[dict[str, float | str]] = []
    for current_date, normalized_equity in normalized_curve:
        running_max = max(running_max, normalized_equity)
        drawdown_rows.append(
            {
                "date": current_date.isoformat(),
                "drawdown": normalized_equity / running_max - 1.0,
            }
        )

    period_return = normalized_curve[-1][1] - 1.0
    observation_count = len(normalized_curve)
    annualized_return = (
        (1.0 + period_return) ** (periods_per_year / observation_count) - 1.0
        if period_return > -1.0
        else -1.0
    )
    turnover_by_date = {
        date.fromisoformat(str(item["date"])[:10]): float(item["turnover"])
        for item in result.turnover_curve
    }
    scoped_turnovers = [
        turnover_by_date[current_date]
        for current_date, _ in scoped_curve
        if current_date in turnover_by_date
    ]
    period_turnover = (
        sum(scoped_turnovers) / len(scoped_turnovers)
        if scoped_turnovers
        else 0.0
    )
    month_keys = {
        current_date.strftime("%Y-%m")
        for current_date, _ in scoped_curve
    }

    return {
        "period_label": period_label,
        "metrics": {
            "期间收益率": period_return,
            "期间年化收益率": annualized_return,
            "期间最大回撤": min(
                (float(item["drawdown"]) for item in drawdown_rows),
                default=0.0,
            ),
            "期间平均换手": period_turnover,
            "期间观测数": observation_count,
        },
        "analytics": {
            "period_start": scoped_curve[0][0].isoformat(),
            "period_end": end_date.isoformat(),
            "equity_curve": [
                {
                    "date": current_date.isoformat(),
                    "equity": normalized_equity,
                }
                for current_date, normalized_equity in normalized_curve
            ],
            "drawdown_curve": drawdown_rows,
            "monthly_returns": [
                item
                for item in result.monthly_returns
                if str(item.get("month")) in month_keys
            ],
            "turnover_curve": [
                {
                    "date": current_date.isoformat(),
                    "turnover": turnover_by_date[current_date],
                }
                for current_date, _ in scoped_curve
                if current_date in turnover_by_date
            ],
        },
    }


def build_periodic_report_only(
    report_type: str,
    strategy: str = "portfolio",
    profile_id: str | None = None,
) -> str:
    """仅生成周期报告，供调度器调用。"""

    profile = load_trading_profile(profile_id)
    market_df = load_profile_signal_data(profile)
    pipeline = run_profile_strategy_pipeline(
        market_df,
        profile,
        strategy_ids=parse_strategy_selection(strategy),
        latest_only=False,
    )
    if pipeline.output_type.value != "target_weight":
        raise ValueError(
            f"策略 {strategy} 的输出类型为 {pipeline.output_type.value}，当前周期报告仅支持 target_weight 型策略。"
        )
    holdings = latest_pipeline_output(pipeline)
    result = run_target_backtest(profile, market_df, pipeline.frame)

    periodic_view = build_periodic_backtest_view(result, report_type)
    if report_type == "daily":
        run_health_days = 28
    elif report_type == "weekly":
        run_health_days = 28
    else:
        run_health_days = 56

    live_account_attribution = (
        latest_live_account_attribution_summary(profile_id=profile.profile_id)
        if report_type == "daily"
        else None
    )
    run_health_summaries = rolling_run_health_summaries(
        profile_id=profile.profile_id,
        days=run_health_days,
    )

    report_path = build_markdown_report(
        report_type=report_type,
        strategy_id=strategy,
        metrics=periodic_view["metrics"],
        holdings=holdings,
        period_label=str(periodic_view["period_label"]),
        analytics=periodic_view["analytics"],
        benchmark_symbol=profile.benchmark_symbol,
        live_account_attribution=live_account_attribution,
        run_health_summaries=run_health_summaries,
        run_health_days=run_health_days,
    )
    if report_type == "daily":
        record_daily_anomaly_events(report_path, live_account_attribution)
    return report_path
