"""中文报告构建模块。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict, cast

import polars as pl
from jinja2 import Environment, FileSystemLoader

from northstar_quant.research.backtest.models import BacktestResult
from northstar_quant.application.backtest import BacktestRun, run_profile_backtest_run
from northstar_quant.foundation.common.time import utc_now
from northstar_quant.foundation.config.settings import get_settings
from northstar_quant.foundation.db.repositories import (
    list_recent_account_attributions,
    replace_anomaly_events_for_account_attribution,
)
from northstar_quant.foundation.db.session import SessionLocal
from northstar_quant.foundation.observability.logging.logger import get_logger
from northstar_quant.foundation.observability.monitoring.run_health import soak_summary
from northstar_quant.foundation.reporting.artifacts import (
    REPORT_SCHEMA_VERSION,
    report_artifact_label,
)
from northstar_quant.foundation.security import redact
from northstar_quant.portfolio_risk.portfolio.strategy_pipeline import (
    parse_strategy_selection,
)

_TEMPLATE_MAP = {
    "backtest": "backtest_report.md.j2",
    "daily": "daily_report.md.j2",
    "weekly": "weekly_report.md.j2",
    "monthly": "monthly_report.md.j2",
    "yearly": "yearly_report.md.j2",
}
_PERIODIC_REPORT_TYPES = {"daily", "weekly", "monthly", "yearly"}
_MIN_ANNUALIZED_PERIOD_OBSERVATIONS = 20
_PERCENT_METRIC_MARKERS = (
    "收益率",
    "波动率",
    "回撤",
    "占比",
    "换手",
    "跟踪误差",
    "超额收益",
    "保证金/权益",
    "可用资金/权益",
)
_COUNT_METRIC_MARKERS = ("事件数", "观测数", "周期数", "订单数", "成交数量")
_IMMUTABLE_BACKTEST_GENERATED_AT = "由冻结运行清单确定（不记录墙钟时间）"


class PeriodicBacktestView(TypedDict):
    """周期报告需要的强类型回测视图。"""

    period_label: str
    artifact_period: str
    metrics: dict[str, object]
    analytics: dict[str, object]


logger = get_logger(__name__, command="report.build")


def _safe_report_filename_part(value: str) -> str:
    """把报告标识规范为不含路径语义的稳定文件名片段。"""

    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    normalized = normalized.strip(".-_")
    if not normalized:
        raise ValueError("报告文件名片段不能为空")
    return normalized


def _build_report_artifact_path(
    *,
    report_type: str,
    strategy_id: str,
    profile_id: str,
    artifact_period: str,
    run_id: str | None = None,
) -> Path:
    report_group = "backtest" if report_type == "backtest" else report_type
    parts = [
        _safe_report_filename_part(report_group),
        _safe_report_filename_part(profile_id),
        _safe_report_filename_part(strategy_id),
        _safe_report_filename_part(artifact_period),
    ]
    if run_id:
        parts.append(_safe_report_filename_part(run_id))
    return Path(*parts)


def _report_json_default(value: object) -> str:
    """仅转换报告允许的日期和路径类型，其他未知对象直接失败关闭。"""

    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"报告结构化数据包含不支持的类型：{type(value).__name__}")


def _serialize_report_json(payload: Mapping[str, object]) -> str:
    """序列化不允许 NaN/Infinity 的审计 JSON。"""

    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        default=_report_json_default,
        allow_nan=False,
    )


def _reuse_immutable_backtest_artifact(
    report_dir: Path,
    *,
    manifest_json: str,
    report_json: str,
    markdown: str,
) -> str | None:
    """复用同一运行 ID 的完整制品，拒绝静默覆盖审计记录。"""

    if not report_dir.exists():
        return None

    manifest_path = report_dir / "manifest.json"
    markdown_path = report_dir / "report.md"
    report_json_path = report_dir / "report.json"
    if not manifest_path.is_file():
        if any(report_dir.iterdir()):
            raise ValueError(
                f"回测制品目录 {report_dir} 已存在但缺少运行清单；"
                "为避免覆盖不可审计文件，已拒绝写入。"
            )
        return None
    existing_manifest = manifest_path.read_text(encoding="utf-8")
    if existing_manifest != manifest_json:
        raise ValueError(
            f"回测运行 ID 对应的清单与现有制品不一致：{report_dir}；"
            "已拒绝覆盖。"
        )
    if markdown_path.is_file() and report_json_path.is_file():
        _verify_existing_backtest_artifact(
            manifest_json=manifest_json,
            expected_report_json=report_json,
            expected_markdown=markdown,
            markdown_path=markdown_path,
            report_json_path=report_json_path,
        )
        return str(markdown_path)
    raise ValueError(
        f"回测制品目录 {report_dir} 不完整；"
        "为避免混合不同运行结果，已拒绝覆盖。"
    )


def _verify_existing_backtest_artifact(
    *,
    manifest_json: str,
    expected_report_json: str,
    expected_markdown: str,
    markdown_path: Path,
    report_json_path: Path,
) -> None:
    """复用前逐字节核验正式报告能否由当前冻结运行重建。

    ``report.json`` 中自带的 hash 不是信任根：攻击者可以同时改动 JSON、Markdown
    和其中的 hash。正式回测报告不写墙钟时间，因此同一 ``BacktestRun`` 能在当前
    代码与模板下重建完全相同的两个制品；任何一个字节偏离都拒绝复用。
    """

    # 调用方在进入此函数前已逐字节比对 manifest.json。参数保留在签名中，令这项
    # 信任边界与 report JSON/Markdown 的重建比较保持显式且不易被误用为自报告 hash。
    del manifest_json
    if report_json_path.read_text(encoding="utf-8") != expected_report_json:
        raise ValueError("既有 report.json 已偏离冻结 BacktestRun，拒绝复用")
    if markdown_path.read_text(encoding="utf-8") != expected_markdown:
        raise ValueError("既有 report.md 已偏离冻结 BacktestRun，拒绝复用")


def _display_report_metric(key: object, value: object) -> object:
    """把结构化数值渲染为适合人读的单位，同时保留 JSON 原始值。"""

    if value is None:
        return "N/A（样本不足或不适用）"
    if isinstance(value, bool):
        return value
    if not isinstance(value, (int, float)):
        return value
    key_text = str(key)
    number = float(value)
    if any(marker in key_text for marker in _COUNT_METRIC_MARKERS):
        return f"{number:,.0f}" if number.is_integer() else f"{number:,.4f}"
    if key_text in {"累计手续费", "累计成交名义金额"}:
        return f"{number:,.2f}"
    if any(marker in key_text for marker in _PERCENT_METRIC_MARKERS):
        return f"{number:.2%}"
    if "比率" in key_text:
        return f"{number:.4f}"
    return f"{number:.6f}"


def _format_report_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S %Z")


def _report_float(
    value: object,
    *,
    field_name: str,
    default: float = 0.0,
) -> float:
    """把数据库/JSON 边界的数值显式收窄为有限 float。

    SQLAlchemy Numeric、JSON 字符串和普通数值都可能到达报告层；转换只在这个
    边界集中进行，避免把 ``object`` 不加验证地传播到展示和告警计算。
    """

    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError(f"报告字段 {field_name} 不能是布尔值")
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"报告字段 {field_name} 必须是数值") from exc
    if not math.isfinite(number):
        raise ValueError(f"报告字段 {field_name} 必须是有限数")
    return number


def _report_int(value: object, *, field_name: str, default: int = 0) -> int:
    """把报告计数字段收窄为整数；缺失值沿用既有的零值展示语义。"""

    if value is None or value == "":
        return default
    return int(_report_float(value, field_name=field_name, default=float(default)))


def _optional_report_float(value: object, *, field_name: str) -> float | None:
    if value is None or value == "":
        return None
    return _report_float(value, field_name=field_name)


def _report_mapping_rows(value: object) -> list[dict[str, object]]:
    """从非受信任的报告 payload 中提取映射行，拒绝字符串等伪序列。"""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    rows: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, Mapping):
            rows.append({str(key): item_value for key, item_value in item.items()})
    return rows


def _non_zero_component_rows(
    components: Sequence[tuple[str, float | None]],
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
    components: Sequence[tuple[str, float | None]],
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
    live_account_attribution: Mapping[str, object] | None,
    *,
    shortfall_bps_threshold: float,
    residual_abs_threshold: float,
    residual_ratio_threshold: float,
    funding_abs_threshold: float,
    funding_ratio_threshold: float,
) -> list[dict[str, str]]:
    if not live_account_attribution:
        return []

    starting_equity = _report_float(
        live_account_attribution.get("starting_equity"), field_name="starting_equity"
    )
    ending_equity = _report_float(
        live_account_attribution.get("ending_equity"), field_name="ending_equity"
    )
    execution_shortfall = _report_float(
        live_account_attribution.get("execution_shortfall"), field_name="execution_shortfall"
    )
    traded_notional = _report_float(
        live_account_attribution.get("traded_notional"), field_name="traded_notional"
    )
    equity_change = _report_float(
        live_account_attribution.get("equity_change"), field_name="equity_change"
    )
    residual_pnl = _report_float(
        live_account_attribution.get("residual_pnl"), field_name="residual_pnl"
    )
    funding_cash_flow = _report_float(
        live_account_attribution.get("funding_cash_flow"), field_name="funding_cash_flow"
    )
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


def _render_alert_lines(alert_items: Sequence[Mapping[str, object]]) -> list[str]:
    return [
        f"[{str(item.get('tag') or '')}] {str(item.get('message') or '')}"
        for item in alert_items
    ]


def _alert_tags(alert_items: Sequence[Mapping[str, object]]) -> list[str]:
    tags: list[str] = []
    for item in alert_items:
        tag = str(item.get("tag") or "").strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _alert_tag_summary(tags: list[str]) -> str:
    return "".join(f"[{tag}]" for tag in tags)


def _resolve_alert_tag_summary(live_account_attribution: Mapping[str, object] | None) -> str:
    if not live_account_attribution:
        return ""
    tag_summary = str(live_account_attribution.get("alert_tag_summary") or "").strip()
    if tag_summary:
        return tag_summary
    alert_items = _report_mapping_rows(live_account_attribution.get("alert_items"))
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


def _build_run_health_summary_lines(summary: Mapping[str, object]) -> list[str]:
    days = _report_int(summary.get("days"), field_name="days")
    run_count = _report_int(summary.get("run_count"), field_name="run_count")
    preflight_pass_count = _report_int(
        summary.get("preflight_pass_count"), field_name="preflight_pass_count"
    )
    blocked_run_count = _report_int(
        summary.get("blocked_run_count"), field_name="blocked_run_count"
    )
    plan_consistency_issue_run_count = _report_int(
        summary.get("plan_consistency_issue_run_count"),
        field_name="plan_consistency_issue_run_count",
    )
    open_order_run_count = _report_int(
        summary.get("open_order_run_count"), field_name="open_order_run_count"
    )
    partial_fill_run_count = _report_int(
        summary.get("partial_fill_run_count"), field_name="partial_fill_run_count"
    )
    anomaly_recent_7d = _report_int(
        summary.get("anomaly_events_recent_7d"), field_name="anomaly_events_recent_7d"
    )
    anomaly_prev_7d = _report_int(
        summary.get("anomaly_events_prev_7d"), field_name="anomaly_events_prev_7d"
    )
    avg_abs_execution_shortfall_bps = _optional_report_float(
        summary.get("avg_abs_execution_shortfall_bps"),
        field_name="avg_abs_execution_shortfall_bps",
    )
    avg_abs_residual_pnl = _optional_report_float(
        summary.get("avg_abs_residual_pnl"), field_name="avg_abs_residual_pnl"
    )
    latest_runs = _report_mapping_rows(summary.get("latest_runs"))

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
                f"计划 {_report_int(latest.get('execution_plan_count'), field_name='execution_plan_count')} 笔。"
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
    live_account_attribution: Mapping[str, object] | None,
    *,
    shortfall_bps_threshold: float,
    residual_abs_threshold: float,
    residual_ratio_threshold: float,
) -> list[str]:
    if not live_account_attribution:
        return ["当日尚无可用的账户归因样本，邮件正文暂不输出自动复盘结论。"]

    equity_change = _report_float(
        live_account_attribution.get("equity_change"), field_name="equity_change"
    )
    price_pnl = _report_float(
        live_account_attribution.get("price_pnl"), field_name="price_pnl"
    )
    rebalance_pnl = _report_float(
        live_account_attribution.get("rebalance_pnl"), field_name="rebalance_pnl"
    )
    execution_shortfall = _report_float(
        live_account_attribution.get("execution_shortfall"), field_name="execution_shortfall"
    )
    total_non_trade = _report_float(
        live_account_attribution.get("total_non_trade_cash_flow"),
        field_name="total_non_trade_cash_flow",
    )
    traded_notional = _report_float(
        live_account_attribution.get("traded_notional"), field_name="traded_notional"
    )
    fill_count = _report_int(live_account_attribution.get("fill_count"), field_name="fill_count")
    residual_pnl = _report_float(
        live_account_attribution.get("residual_pnl"), field_name="residual_pnl"
    )

    lines = [
        (
            f"本期账户权益变动 {_format_amount(equity_change)}，其中价格变动贡献 {_format_amount(price_pnl)}，"
            f"调仓贡献 {_format_amount(rebalance_pnl)}，非交易现金流 {_format_amount(total_non_trade)}。"
        )
    ]

    non_trade_components = [
        (
            "利息",
            _optional_report_float(
                live_account_attribution.get("interest_cash_flow"),
                field_name="interest_cash_flow",
            ),
        ),
        (
            "费用",
            _optional_report_float(
                live_account_attribution.get("fee_cash_flow"), field_name="fee_cash_flow"
            ),
        ),
        (
            "税费",
            _optional_report_float(
                live_account_attribution.get("tax_cash_flow"), field_name="tax_cash_flow"
            ),
        ),
        (
            "资金划转",
            _optional_report_float(
                live_account_attribution.get("funding_cash_flow"),
                field_name="funding_cash_flow",
            ),
        ),
        (
            "其他非交易项",
            _optional_report_float(
                live_account_attribution.get("other_non_trade_cash_flow"),
                field_name="other_non_trade_cash_flow",
            ),
        ),
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
    live_account_attribution: Mapping[str, object] | None,
) -> str | None:
    """把日报异常归因整理成一条可直接推送的摘要。"""

    if not live_account_attribution:
        return None

    raw_alert_lines = live_account_attribution.get("alert_lines")
    alert_lines = (
        [str(line).strip() for line in raw_alert_lines if str(line).strip()]
        if isinstance(raw_alert_lines, Sequence)
        and not isinstance(raw_alert_lines, (str, bytes, bytearray))
        else []
    )
    alert_items = [
        item
        for item in _report_mapping_rows(live_account_attribution.get("alert_items"))
        if str(item.get("message") or "").strip()
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
        lines.extend(
            f"- [{str(item.get('tag') or '')}] {str(item.get('message') or '')}"
            for item in alert_items
        )
    else:
        lines.extend(f"- {line}" for line in alert_lines)
    return "\n".join(lines)


def build_report_email_subject(
    *,
    report_type: str,
    report_path: str | Path,
    live_account_attribution: Mapping[str, object] | None = None,
    subject_prefix: str | None = None,
) -> str:
    """为报告邮件生成更容易扫描的主题。"""

    settings = get_settings()
    prefix = subject_prefix or settings.report_email_subject_prefix
    report_type_label = {
        "daily": "日报",
        "weekly": "周报",
        "monthly": "月报",
        "yearly": "年报",
    }.get(report_type, report_type)
    tag_summary = _resolve_alert_tag_summary(live_account_attribution)
    middle = f"{report_type_label} {tag_summary}".strip()
    return f"{prefix} - {middle} - {report_artifact_label(report_path)}"


def record_daily_anomaly_events(
    report_path: str | Path,
    live_account_attribution: Mapping[str, object] | None,
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
            account_attribution_id=_report_int(
                account_attribution_id, field_name="account_attribution_id"
            ),
            profile_id=str(live_account_attribution.get("profile_id") or "") or None,
            account=str(live_account_attribution.get("account") or "") or None,
            run_id=str(live_account_attribution.get("run_id") or "") or None,
            report_type="daily",
            report_path=str(report_path),
            detected_at=utc_now(),
            alert_items=_report_mapping_rows(live_account_attribution.get("alert_items")),
        )


def build_markdown_report(
    report_type: str,
    strategy_id: str,
    metrics: dict,
    holdings: pl.DataFrame | None = None,
    period_label: str | None = None,
    artifact_period: str | None = None,
    profile_id: str | None = None,
    analytics: dict | None = None,
    benchmark_symbol: str | None = None,
    live_account_attribution: dict[str, object] | None = None,
    run_health_summaries: list[dict[str, object]] | None = None,
    run_health_days: int | None = None,
    backtest_run: dict[str, object] | None = None,
) -> str:
    """生成非正式回测或周期报告的 Markdown 与结构化 JSON 制品。

    带运行清单的正式回测报告必须调用 :func:`build_backtest_report`。该入口只接受
    ``BacktestRun``，会在写入前复验结果、analytics 与指标 checksum；不能把手工
    拼装的 ``dict`` 当作可审计回测清单传入这里。
    """

    if report_type == "backtest":
        raise ValueError(
            "回测报告必须调用 build_backtest_report，并传入已验证的 BacktestRun"
        )
    return _build_markdown_report(
        report_type=report_type,
        strategy_id=strategy_id,
        metrics=metrics,
        holdings=holdings,
        period_label=period_label,
        artifact_period=artifact_period,
        profile_id=profile_id,
        analytics=analytics,
        benchmark_symbol=benchmark_symbol,
        live_account_attribution=live_account_attribution,
        run_health_summaries=run_health_summaries,
        run_health_days=run_health_days,
        backtest_run=backtest_run,
    )


def _build_markdown_report(
    report_type: str,
    strategy_id: str,
    metrics: dict,
    holdings: pl.DataFrame | None = None,
    *,
    period_label: str | None = None,
    artifact_period: str | None = None,
    profile_id: str | None = None,
    analytics: dict | None = None,
    benchmark_symbol: str | None = None,
    live_account_attribution: dict[str, object] | None = None,
    run_health_summaries: list[dict[str, object]] | None = None,
    run_health_days: int | None = None,
    backtest_run: dict[str, object] | None = None,
) -> str:
    """已验证调用方使用的报告写入实现。"""

    settings = get_settings()
    settings.reports_dir.mkdir(parents=True, exist_ok=True)

    env = Environment(loader=FileSystemLoader(settings.project_root / "templates"))
    template = env.get_template(_TEMPLATE_MAP[report_type])

    if not profile_id:
        raise ValueError("生成报告必须提供 profile_id")
    if report_type == "backtest" and backtest_run is None:
        raise ValueError("正式回测报告必须附带冻结运行清单")
    safe_backtest_run = (
        cast(dict[str, object], redact(backtest_run)) if backtest_run is not None else None
    )
    resolved_period_label = period_label or report_type
    resolved_artifact_period = artifact_period or _safe_report_filename_part(
        resolved_period_label
    )
    run_id: str | None = None
    if safe_backtest_run is not None:
        candidate = str(safe_backtest_run.get("run_id") or "").strip()
        if not candidate:
            raise ValueError("回测运行清单缺少 run_id")
        run_id = candidate
    artifact_path = _build_report_artifact_path(
        report_type=report_type,
        strategy_id=strategy_id,
        profile_id=profile_id,
        artifact_period=resolved_artifact_period,
        run_id=run_id if report_type == "backtest" else None,
    )
    artifact_id = artifact_path.as_posix()
    report_dir = settings.reports_dir / artifact_path
    manifest_json = _serialize_report_json(safe_backtest_run) if safe_backtest_run is not None else None
    is_immutable_backtest = report_type == "backtest" and manifest_json is not None
    generated_at = (
        _IMMUTABLE_BACKTEST_GENERATED_AT
        if is_immutable_backtest
        else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    resolved_benchmark_symbol = benchmark_symbol or settings.report_benchmark_symbol
    safe_metrics = cast(dict[str, object], redact(metrics))
    holdings_payload = cast(list[dict[str, object]], redact([] if holdings is None else holdings.to_dicts()))
    analytics_payload = cast(dict[str, object], redact(analytics or {}))
    safe_live_account_attribution = redact(live_account_attribution)
    safe_run_health_summaries = redact(run_health_summaries or [])
    display_metrics = {
        key: _display_report_metric(key, value) for key, value in safe_metrics.items()
    }
    payload = {
        "generated_at": generated_at,
        "period_label": resolved_period_label,
        "profile_id": profile_id,
        "strategy_id": strategy_id,
        "metrics": display_metrics,
        "benchmark_symbol": resolved_benchmark_symbol,
        "holdings": holdings_payload,
        "analytics": analytics_payload,
        "live_account_attribution": safe_live_account_attribution,
        "run_health_summaries": safe_run_health_summaries,
        "run_health_days": run_health_days,
        "backtest_run": safe_backtest_run,
    }

    report_data = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "report_type": report_type,
        "generated_at": generated_at,
        "period_label": resolved_period_label,
        "artifact_period": resolved_artifact_period,
        "profile_id": profile_id,
        "strategy_id": strategy_id,
        "benchmark_symbol": resolved_benchmark_symbol,
        "metrics": safe_metrics,
        "holdings": holdings_payload,
        "analytics": analytics_payload,
        "live_account_attribution": safe_live_account_attribution,
        "run_health_summaries": safe_run_health_summaries,
        "run_health_days": run_health_days,
        "backtest_run": safe_backtest_run,
    }
    output = template.render(**payload)
    report_data["markdown_sha256"] = hashlib.sha256(output.encode("utf-8")).hexdigest()
    report_json = _serialize_report_json(report_data)
    if is_immutable_backtest:
        assert manifest_json is not None
        existing_artifact = _reuse_immutable_backtest_artifact(
            report_dir,
            manifest_json=manifest_json,
            report_json=report_json,
            markdown=output,
        )
        if existing_artifact is not None:
            return existing_artifact

    report_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = report_dir / "report.md"
    markdown_path.write_text(output, encoding="utf-8")
    (report_dir / "report.json").write_text(report_json, encoding="utf-8")
    if safe_backtest_run is not None:
        assert manifest_json is not None
        (report_dir / "manifest.json").write_text(manifest_json, encoding="utf-8")
    stale_pdf_path = report_dir / "report.pdf"
    if stale_pdf_path.exists():
        stale_pdf_path.unlink()
    return str(markdown_path)


def build_backtest_report(backtest_run: BacktestRun) -> str:
    """生成画像驱动的回测报告。

    该公开入口只接受 ``BacktestRun``，并从已验证运行对象派生画像、策略、指标、持仓、
    周期和清单，避免调用方把有效 manifest 与另一份报告内容拼接。底层
    ``build_markdown_report`` 仅供周期报告等非回测调用方使用。
    """

    if not isinstance(backtest_run, BacktestRun):
        raise TypeError("backtest_run 必须是 BacktestRun，不接受手工拼装 manifest")
    manifest = backtest_run.manifest_mapping()
    strategy_id = "__".join(backtest_run.selected_strategy_ids)
    if not strategy_id:
        raise ValueError("BacktestRun 缺少已选策略，无法生成正式回测报告")

    return _build_markdown_report(
        "backtest",
        strategy_id,
        backtest_run.metrics,
        backtest_run.verified_latest_holdings(),
        period_label=backtest_run.period_label,
        artifact_period=backtest_run.artifact_period,
        profile_id=backtest_run.profile.profile_id,
        analytics=backtest_run.analytics,
        benchmark_symbol=backtest_run.profile.benchmark_symbol,
        backtest_run=manifest,
    )


def build_periodic_backtest_view(
    result: BacktestResult,
    report_type: str,
    *,
    periods_per_year: int = 252,
) -> PeriodicBacktestView:
    """从完整回测曲线提取日报、周报、月报或年报的真实期间指标。"""

    if report_type not in _PERIODIC_REPORT_TYPES:
        raise ValueError(f"不支持的报告类型：{report_type}")
    if not result.equity_curve:
        raise ValueError("回测结果缺少净值曲线，无法生成周期报告")

    curve = sorted(
        (
            date.fromisoformat(str(item["date"])[:10]),
            _report_float(item["equity"], field_name="equity_curve.equity"),
        )
        for item in result.equity_curve
    )
    end_date = curve[-1][0]
    if report_type == "daily":
        boundary = end_date
        period_label = end_date.isoformat()
        artifact_period = end_date.strftime("%Y%m%d")
    elif report_type == "weekly":
        boundary = end_date - timedelta(days=end_date.weekday())
        iso_year, iso_week, _ = end_date.isocalendar()
        artifact_period = f"{iso_year}-W{iso_week:02d}"
        period_label = (
            f"{iso_year}年第{iso_week:02d}周"
            f"（{boundary.isoformat()} 至 {end_date.isoformat()}）"
        )
    elif report_type == "monthly":
        boundary = end_date.replace(day=1)
        period_label = end_date.strftime("%Y-%m")
        artifact_period = period_label
    else:
        boundary = end_date.replace(month=1, day=1)
        period_label = f"{end_date.year}年（截至 {end_date.isoformat()}）"
        artifact_period = str(end_date.year)

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
    annualized_return: float | None = None
    if observation_count >= _MIN_ANNUALIZED_PERIOD_OBSERVATIONS:
        annualized_return = (
            (1.0 + period_return) ** (periods_per_year / observation_count) - 1.0
            if period_return > -1.0
            else -1.0
        )
    turnover_by_date = {
        date.fromisoformat(str(item["date"])[:10]): _report_float(
            item["turnover"], field_name="turnover_curve.turnover"
        )
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
        "artifact_period": artifact_period,
        "metrics": {
            "期间收益率": period_return,
            "期间年化收益率（至少 20 个权益观测）": annualized_return,
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
                dict(item)
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

    run = run_profile_backtest_run(
        profile_id,
        strategy_ids=parse_strategy_selection(strategy),
    )
    periodic_view = build_periodic_backtest_view(run.result, report_type)
    if report_type == "daily":
        run_health_days = 28
    elif report_type == "weekly":
        run_health_days = 28
    elif report_type == "monthly":
        run_health_days = 56
    else:
        run_health_days = 365

    live_account_attribution = (
        latest_live_account_attribution_summary(profile_id=run.profile.profile_id)
        if report_type == "daily"
        else None
    )
    run_health_summaries = rolling_run_health_summaries(
        profile_id=run.profile.profile_id,
        days=run_health_days,
    )

    report_path = build_markdown_report(
        report_type=report_type,
        strategy_id=strategy,
        metrics=periodic_view["metrics"],
        holdings=run.latest_holdings,
        period_label=str(periodic_view["period_label"]),
        artifact_period=str(periodic_view["artifact_period"]),
        profile_id=run.profile.profile_id,
        analytics=periodic_view["analytics"],
        benchmark_symbol=run.profile.benchmark_symbol,
        live_account_attribution=live_account_attribution,
        run_health_summaries=run_health_summaries,
        run_health_days=run_health_days,
        backtest_run=run.manifest_mapping(),
    )
    if report_type == "daily":
        record_daily_anomaly_events(report_path, live_account_attribution)
    return report_path
