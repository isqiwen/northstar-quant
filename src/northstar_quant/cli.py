"""项目命令行入口。"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime

import click
import typer
from typer.completion import install_callback, show_callback
from typer.core import TyperCommand, TyperGroup

from northstar_quant.backtest.registry import (
    resolve_simulation_backtester,
    resolve_target_backtester,
    run_simulation_backtest,
    run_target_backtest,
)
from northstar_quant.common.enums import StrategyOutputType
from northstar_quant.config.settings import get_settings
from northstar_quant.config.trading_profile import load_trading_profile, resolve_profile_id
from northstar_quant.data.downloader import (
    download_profile_data,
    list_data_providers,
    list_profile_data_summaries,
    read_profile_manifest,
    validate_profile_data,
)
from northstar_quant.data.storage import load_profile_signal_data
from northstar_quant.db.init_db import init_db
from northstar_quant.live.scheduler import run_scheduler
from northstar_quant.live.service import (
    analyze_live_position_drift,
    cancel_stale_orders_once,
    poll_orders_and_fills_once,
    preview_rebalance,
    recent_anomaly_events,
    recent_account_attributions,
    recent_run_health,
    recent_trade_attributions,
    run_live_preflight,
    run_live_once,
    run_shadow_once,
    soak_summary,
    sync_broker_once,
)
from northstar_quant.logging_.logger import get_logger, setup_logging
from northstar_quant.monitoring.health import run_healthcheck
from northstar_quant.reporting.email_sender import send_report_via_email
from northstar_quant.reporting.pdf_renderer import markdown_to_pdf
from northstar_quant.reporting.report_builder import (
    build_report_email_subject,
    build_markdown_report,
    latest_live_account_attribution_summary,
    record_daily_anomaly_events,
    write_markdown_report,
)
from northstar_quant.research.momentum_scan import run_momentum_research
from northstar_quant.strategies.pipeline import (
    latest_pipeline_output,
    parse_strategy_selection,
    run_profile_strategy_pipeline,
)

_HELP_CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}
_PROFILE_OPTION_HELP = "交易画像 ID，默认使用配置中的默认画像。"


class _ChineseHelpOptionMixin:
    def get_help_option(self, ctx: click.Context) -> click.Option | None:
        option = super().get_help_option(ctx)
        if option is not None:
            option.help = "显示帮助并退出。"
        return option


class ChineseTyperGroup(_ChineseHelpOptionMixin, TyperGroup):
    pass


class ChineseTyperCommand(_ChineseHelpOptionMixin, TyperCommand):
    pass


_GROUP_KWARGS = {
    "cls": ChineseTyperGroup,
    "context_settings": _HELP_CONTEXT_SETTINGS,
    "add_completion": False,
}
_CALLBACK_KWARGS = {
    "cls": ChineseTyperGroup,
    "context_settings": _HELP_CONTEXT_SETTINGS,
}
_COMMAND_KWARGS = {
    "cls": ChineseTyperCommand,
    "context_settings": _HELP_CONTEXT_SETTINGS,
}

logger = get_logger(__name__)
app = typer.Typer(help="Northstar Quant 命令行工具。", **_GROUP_KWARGS)
backtest_app = typer.Typer(help="回测相关命令。", **_GROUP_KWARGS)
research_app = typer.Typer(help="研究相关命令。", **_GROUP_KWARGS)
live_app = typer.Typer(help="实盘相关命令。", **_GROUP_KWARGS)
report_app = typer.Typer(help="报告相关命令。", **_GROUP_KWARGS)
dashboard_app = typer.Typer(help="Dashboard 相关命令。", **_GROUP_KWARGS)
data_app = typer.Typer(help="数据下载与数据集管理命令。", **_GROUP_KWARGS)

app.add_typer(backtest_app, name="backtest")
app.add_typer(research_app, name="research")
app.add_typer(live_app, name="live")
app.add_typer(report_app, name="report")
app.add_typer(dashboard_app, name="dashboard")
app.add_typer(data_app, name="data")


def _log_message(message: str, level: int = logging.INFO, **context: object) -> None:
    logger.bind(**context).log(level, message)


def _log_json(payload: object, level: int = logging.INFO, **context: object) -> None:
    logger.bind(**context).log(level, json.dumps(payload, ensure_ascii=False, indent=2, default=str))


@app.callback(invoke_without_command=True, **_CALLBACK_KWARGS)
def main(
    ctx: typer.Context,
    install_completion: bool | None = typer.Option(
        None,
        "--install-completion",
        is_eager=True,
        help="为当前 shell 安装自动补全。",
    ),
    show_completion: bool | None = typer.Option(
        None,
        "--show-completion",
        is_eager=True,
        help="输出当前 shell 的自动补全脚本，可复制或按需定制。",
    ),
) -> None:
    """CLI 启动时初始化日志。"""

    setup_logging()
    if install_completion:
        install_callback(ctx, None, install_completion)
    if show_completion:
        show_callback(ctx, None, show_completion)


@app.command(
    "init-db",
    short_help="初始化本地数据库表结构。",
    help="初始化本地数据库表结构，并创建当前项目缺失的数据表。",
    **_COMMAND_KWARGS,
)
def init_db_command() -> None:
    """初始化本地数据库表结构。"""

    init_db()
    _log_message("数据库初始化完成", command="init-db")


@app.command(
    "sample-data",
    short_help="生成项目自带的样例行情数据。",
    help="生成项目自带的演示行情数据，并写入该画像的标准数据目录。",
    **_COMMAND_KWARGS,
)
def sample_data_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
) -> None:
    """生成项目自带的样例行情数据。"""

    resolved_profile = resolve_profile_id(profile)
    result = download_profile_data(resolved_profile, provider_override="demo")
    _log_json(
        {
            "dataset_path": result.dataset_path,
            "cache_path": result.cache_path,
            "row_count": result.row_count,
            "symbol_count": result.symbol_count,
        },
        command="sample-data",
        profile=resolved_profile,
    )


@app.command(
    "health",
    short_help="检查项目当前运行状态。",
    help="检查项目当前运行状态，包括目录、环境和券商连接模式等基础健康信息。",
    **_COMMAND_KWARGS,
)
def health_command() -> None:
    """检查项目当前运行状态。"""

    _log_json(run_healthcheck(), command="health")


@data_app.command("profiles", **_COMMAND_KWARGS)
def data_profiles_command() -> None:
    """列出当前可用的交易画像与路径规划。"""

    _log_json(list_profile_data_summaries(), command="data.profiles")


@data_app.command("providers", **_COMMAND_KWARGS)
def data_providers_command() -> None:
    """列出当前可用的数据提供器。"""

    _log_json({"providers": list_data_providers()}, command="data.providers")


@data_app.command("download", **_COMMAND_KWARGS)
def data_download_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
    provider: str | None = typer.Option(None, "--provider", help="覆盖画像中配置的数据提供器。"),
) -> None:
    """根据交易画像下载或生成数据，并规范落盘。"""

    resolved_profile = resolve_profile_id(profile)
    result = download_profile_data(resolved_profile, provider_override=provider)
    _log_json(
        asdict(result),
        command="data.download",
        profile=resolved_profile,
        data_source=result.data_source,
    )


@data_app.command("manifest", **_COMMAND_KWARGS)
def data_manifest_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
) -> None:
    """查看某个交易画像当前数据集的 manifest。"""

    resolved_profile = resolve_profile_id(profile)
    _log_json(read_profile_manifest(resolved_profile), command="data.manifest", profile=resolved_profile)


@data_app.command("validate", **_COMMAND_KWARGS)
def data_validate_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
) -> None:
    """校验某个交易画像当前数据集的 schema 与主键一致性。"""

    resolved_profile = resolve_profile_id(profile)
    _log_json(validate_profile_data(resolved_profile), command="data.validate", profile=resolved_profile)


@research_app.command("momentum", **_COMMAND_KWARGS)
def research_momentum_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
) -> None:
    """运行基于 canonical profile pipeline 的研究扫描。"""

    resolved_profile = resolve_profile_id(profile)
    result = run_momentum_research(profile_id=resolved_profile)
    _log_json(result, command="research.momentum", strategy="portfolio", profile=resolved_profile)


@backtest_app.command("event", **_COMMAND_KWARGS)
def event_backtest_command(
    strategy: str = typer.Argument("portfolio"),
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
) -> None:
    """运行目标持仓事件回测。"""

    resolved_profile = resolve_profile_id(profile)
    profile_obj = load_trading_profile(resolved_profile)
    market_df = load_profile_signal_data(resolved_profile)
    try:
        pipeline = run_profile_strategy_pipeline(
            market_df,
            profile_obj,
            strategy_ids=parse_strategy_selection(strategy),
            latest_only=False,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if pipeline.output_type != StrategyOutputType.TARGET_WEIGHT:
        raise typer.BadParameter(
            f"策略 {strategy} 的输出类型为 {pipeline.output_type.value}，不能使用 event 回测。"
            "请改用 `northstar backtest bt`。"
        )

    targets = pipeline.frame
    try:
        backtester = resolve_target_backtester(profile_obj)
        result = run_target_backtest(profile_obj, market_df, targets)
    except LookupError as exc:
        raise typer.BadParameter(str(exc)) from exc

    metrics = {
        "backtester": backtester.backtester_id,
        "total_return": result.total_return,
        "annualized_return": result.annualized_return,
        "max_drawdown": result.max_drawdown,
        "turnover_estimate": result.turnover_estimate,
    }
    holdings = latest_pipeline_output(pipeline)
    report_path = write_markdown_report(
        strategy,
        metrics,
        holdings,
        analytics={
            "equity_curve": result.equity_curve,
            "drawdown_curve": result.drawdown_curve,
            "monthly_returns": result.monthly_returns,
        },
        benchmark_symbol=profile_obj.benchmark_symbol,
    )

    _log_json(metrics, command="backtest.event", strategy=strategy, profile=resolved_profile)
    _log_message(
        f"报告已生成：{report_path}",
        command="backtest.event",
        strategy=strategy,
        profile=resolved_profile,
    )


@backtest_app.command("bt", **_COMMAND_KWARGS)
def bt_backtest_command(
    strategy: str = typer.Argument("portfolio"),
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
) -> None:
    """运行策略仿真回测。"""

    resolved_profile = resolve_profile_id(profile)
    profile_obj = load_trading_profile(resolved_profile)
    try:
        backtester = resolve_simulation_backtester(profile_obj)
        result = run_simulation_backtest(profile_obj, strategy_name=strategy)
    except (LookupError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    result["backtester"] = backtester.backtester_id
    _log_json(result, command="backtest.bt", strategy=strategy, profile=resolved_profile)


@report_app.command("daily", **_COMMAND_KWARGS)
def daily_report_command(
    strategy: str = typer.Option("portfolio", "--strategy", "-s"),
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
    send_email: bool = typer.Option(False, "--send-email", help="生成后立即发送邮件。"),
    send_pdf: bool = typer.Option(True, "--send-pdf/--no-send-pdf", help="发送邮件时是否自动附加 PDF 报告"),
) -> None:
    _report_command("daily", strategy, profile=profile, send_email=send_email, send_pdf=send_pdf)


@report_app.command("weekly", **_COMMAND_KWARGS)
def weekly_report_command(
    strategy: str = typer.Option("portfolio", "--strategy", "-s"),
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
    send_email: bool = typer.Option(False, "--send-email", help="生成后立即发送邮件。"),
    send_pdf: bool = typer.Option(True, "--send-pdf/--no-send-pdf", help="发送邮件时是否自动附加 PDF 报告"),
) -> None:
    _report_command("weekly", strategy, profile=profile, send_email=send_email, send_pdf=send_pdf)


@report_app.command("monthly", **_COMMAND_KWARGS)
def monthly_report_command(
    strategy: str = typer.Option("portfolio", "--strategy", "-s"),
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
    send_email: bool = typer.Option(False, "--send-email", help="生成后立即发送邮件。"),
    send_pdf: bool = typer.Option(True, "--send-pdf/--no-send-pdf", help="发送邮件时是否自动附加 PDF 报告"),
) -> None:
    _report_command("monthly", strategy, profile=profile, send_email=send_email, send_pdf=send_pdf)


@report_app.command("send", **_COMMAND_KWARGS)
def report_send_command(
    report_path: str = typer.Argument(..., help="要发送的 Markdown 报告路径"),
    subject: str | None = typer.Option(None, "--subject", help="可选邮件主题"),
    attach_pdf: bool = typer.Option(True, "--attach-pdf/--no-attach-pdf", help="是否自动附加 PDF 报告"),
) -> None:
    """发送已经生成好的报告邮件。"""

    result = send_report_via_email(report_path, subject=subject, attach_pdf=attach_pdf)
    _log_json(result, command="report.send", report_path=report_path)


@report_app.command("pdf", **_COMMAND_KWARGS)
def report_pdf_command(
    report_path: str = typer.Argument(..., help="要转换为 PDF 的 Markdown 报告路径"),
) -> None:
    """手动把 Markdown 报告转换为 PDF。"""

    pdf_path = markdown_to_pdf(report_path)
    _log_json({"pdf_path": pdf_path}, command="report.pdf", report_path=report_path)


@live_app.command("run", **_COMMAND_KWARGS)
def live_run_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
) -> None:
    """执行一次完整实盘主流程。"""

    resolved_profile = resolve_profile_id(profile)
    messages = run_live_once(profile_id=resolved_profile)
    _log_json(messages, command="live.run", profile=resolved_profile)


@live_app.command("preflight", **_COMMAND_KWARGS)
def live_preflight_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
) -> None:
    """执行一次实盘前硬门禁检查，但不真正下单。"""

    resolved_profile = resolve_profile_id(profile)
    result = run_live_preflight(profile_id=resolved_profile)
    _log_json(result, command="live.preflight", profile=resolved_profile)


@live_app.command("shadow-run", **_COMMAND_KWARGS)
def live_shadow_run_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
) -> None:
    """执行一次 shadow run，但不真正下单。"""

    resolved_profile = resolve_profile_id(profile)
    result = run_shadow_once(profile_id=resolved_profile)
    _log_json(result, command="live.shadow-run", profile=resolved_profile)


@live_app.command("sync", **_COMMAND_KWARGS)
def live_sync_command() -> None:
    """同步券商状态。"""

    result = sync_broker_once()
    _log_json(result, command="live.sync")


@live_app.command("poll", **_COMMAND_KWARGS)
def live_poll_command() -> None:
    """轮询订单状态并回写成交。"""

    result = poll_orders_and_fills_once()
    _log_json(result, command="live.poll")


@live_app.command("drift", **_COMMAND_KWARGS)
def live_drift_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
) -> None:
    """分析目标组合与真实持仓的偏离。"""

    resolved_profile = resolve_profile_id(profile)
    result = analyze_live_position_drift(profile_id=resolved_profile)
    _log_json(result, command="live.drift", profile=resolved_profile)


@live_app.command("scheduler", **_COMMAND_KWARGS)
def live_scheduler_command() -> None:
    """启动实盘调度器。"""

    run_scheduler()


@live_app.command("cancel-stale", **_COMMAND_KWARGS)
def live_cancel_stale_command() -> None:
    """撤销超时未成交订单。"""

    result = cancel_stale_orders_once()
    _log_json(result, command="live.cancel-stale")


@live_app.command("preview-rebalance", **_COMMAND_KWARGS)
def live_preview_rebalance_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
) -> None:
    """预览再平衡计划。"""

    resolved_profile = resolve_profile_id(profile)
    result = preview_rebalance(profile_id=resolved_profile)
    _log_json(result, command="live.preview-rebalance", profile=resolved_profile)


@live_app.command("trade-attribution", **_COMMAND_KWARGS)
def live_trade_attribution_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help="可选画像过滤。"),
    account: str | None = typer.Option(None, "--account", help="可选账户过滤。"),
    limit: int = typer.Option(20, "--limit", min=1, max=500, help="返回最近多少条。"),
) -> None:
    """查看最近成交归因。"""

    result = recent_trade_attributions(limit=limit, profile_id=profile, account=account)
    _log_json(
        result,
        command="live.trade-attribution",
        profile=profile,
        account=account,
        limit=limit,
    )


@live_app.command("account-attribution", **_COMMAND_KWARGS)
def live_account_attribution_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help="可选画像过滤。"),
    account: str | None = typer.Option(None, "--account", help="可选账户过滤。"),
    limit: int = typer.Option(20, "--limit", min=1, max=500, help="返回最近多少条。"),
) -> None:
    """查看最近账户区间归因。"""

    result = recent_account_attributions(limit=limit, profile_id=profile, account=account)
    _log_json(
        result,
        command="live.account-attribution",
        profile=profile,
        account=account,
        limit=limit,
    )


@live_app.command("anomaly-events", **_COMMAND_KWARGS)
def live_anomaly_events_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help="可选画像过滤。"),
    account: str | None = typer.Option(None, "--account", help="可选账户过滤。"),
    alert_tag: str | None = typer.Option(None, "--tag", help="可选异常标签过滤。"),
    limit: int = typer.Option(20, "--limit", min=1, max=500, help="返回最近多少条。"),
) -> None:
    """查看最近异常事件。"""

    result = recent_anomaly_events(
        limit=limit,
        profile_id=profile,
        account=account,
        alert_tag=alert_tag,
    )
    _log_json(
        result,
        command="live.anomaly-events",
        profile=profile,
        account=account,
        alert_tag=alert_tag,
        limit=limit,
    )


@live_app.command("run-health", **_COMMAND_KWARGS)
def live_run_health_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help="可选画像过滤。"),
    account: str | None = typer.Option(None, "--account", help="可选账户过滤。"),
    mode: str | None = typer.Option(None, "--mode", help="可选模式过滤，如 paper_soak / shadow_run。"),
    limit: int = typer.Option(20, "--limit", min=1, max=500, help="返回最近多少条。"),
) -> None:
    """查看最近的 soak / shadow 运行健康记录。"""

    result = recent_run_health(
        limit=limit,
        profile_id=profile,
        account=account,
        mode=mode,
    )
    _log_json(
        result,
        command="live.run-health",
        profile=profile,
        account=account,
        mode=mode,
        limit=limit,
    )


@live_app.command("soak-summary", **_COMMAND_KWARGS)
def live_soak_summary_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help="可选画像过滤。"),
    account: str | None = typer.Option(None, "--account", help="可选账户过滤。"),
    mode: str | None = typer.Option(None, "--mode", help="可选模式过滤，如 paper_soak / shadow_run。"),
    days: int = typer.Option(28, "--days", min=1, max=365, help="统计最近多少天。"),
    limit: int = typer.Option(20, "--limit", min=1, max=200, help="附带最近多少条运行记录。"),
) -> None:
    """汇总最近一段时间的 soak / shadow 稳定性。"""

    result = soak_summary(
        profile_id=profile,
        account=account,
        mode=mode,
        days=days,
        limit=limit,
    )
    _log_json(
        result,
        command="live.soak-summary",
        profile=profile,
        account=account,
        mode=mode,
        days=days,
        limit=limit,
    )


def _report_command(
    report_type: str,
    strategy: str,
    *,
    profile: str | None = None,
    send_email: bool = False,
    send_pdf: bool = True,
) -> None:
    resolved_profile = resolve_profile_id(profile)
    profile_obj = load_trading_profile(resolved_profile)
    market_df = load_profile_signal_data(profile_obj)
    try:
        pipeline = run_profile_strategy_pipeline(
            market_df,
            profile_obj,
            strategy_ids=parse_strategy_selection(strategy),
            latest_only=False,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if pipeline.output_type != StrategyOutputType.TARGET_WEIGHT:
        raise typer.BadParameter(
            f"策略 {strategy} 的输出类型为 {pipeline.output_type.value}，当前周期报告只支持 target_weight 型策略。"
        )

    holdings = latest_pipeline_output(pipeline)
    result = run_target_backtest(profile_obj, market_df, pipeline.frame)
    live_account_attribution = (
        latest_live_account_attribution_summary(profile_id=resolved_profile)
        if report_type == "daily"
        else None
    )
    path = build_markdown_report(
        report_type,
        strategy,
        {
            "total_return": result.total_return,
            "annualized_return": result.annualized_return,
            "max_drawdown": result.max_drawdown,
            "turnover_estimate": result.turnover_estimate,
        },
        holdings,
        period_label=_period_label(report_type),
        analytics={
            "equity_curve": result.equity_curve,
            "drawdown_curve": result.drawdown_curve,
            "monthly_returns": result.monthly_returns,
        },
        benchmark_symbol=profile_obj.benchmark_symbol,
        live_account_attribution=live_account_attribution,
    )
    if report_type == "daily":
        record_daily_anomaly_events(path, live_account_attribution)
    _log_message(
        f"{report_type} 报告已生成：{path}",
        command=f"report.{report_type}",
        report_type=report_type,
        strategy=strategy,
        profile=resolved_profile,
    )

    if send_email:
        subject = build_report_email_subject(
            report_type=report_type,
            report_path=path,
            live_account_attribution=live_account_attribution,
        )
        email_result = send_report_via_email(path, subject=subject, attach_pdf=send_pdf)
        _log_json(
            email_result,
            command=f"report.{report_type}",
            report_type=report_type,
            strategy=strategy,
            profile=resolved_profile,
        )


def _period_label(report_type: str) -> str:
    now = datetime.now()
    if report_type == "daily":
        return now.strftime("%Y-%m-%d")
    if report_type == "weekly":
        return now.strftime("%Y 第%W周")
    return now.strftime("%Y-%m")


@dashboard_app.command("run", **_COMMAND_KWARGS)
def dashboard_run_command() -> None:
    """启动 Streamlit Dashboard。"""

    import subprocess
    import sys

    settings = get_settings()
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "src/northstar_quant/monitoring/dashboard.py",
        "--server.address",
        settings.dashboard_host,
        "--server.port",
        str(settings.dashboard_port),
    ]
    raise typer.Exit(code=subprocess.call(cmd))
