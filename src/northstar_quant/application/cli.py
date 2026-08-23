"""项目命令行入口。"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Final, cast

import click
import typer
from typer.completion import install_callback, show_callback
from typer.core import TyperCommand, TyperGroup

from northstar_quant.application.backtest import (
    run_profile_backtest,
    run_profile_backtest_run,
)
from northstar_quant.platform.config.data_sources import list_data_source_summaries
from northstar_quant.platform.config.database_backup_readiness import (
    DatabaseBackupReadinessConfigError,
    load_database_backup_readiness_policy,
)
from northstar_quant.platform.config.output_retention import (
    OutputRetentionConfigError,
    load_output_retention_policy,
)
from northstar_quant.platform.config.settings import get_settings
from northstar_quant.platform.config.trading_profile import resolve_profile_id
from northstar_quant.data_platform.sources.downloader import (
    download_profile_data,
    import_profile_data,
    list_data_providers,
    list_profile_data_summaries,
    read_profile_manifest,
    validate_profile_data,
)
from northstar_quant.data_platform.artifacts.output_cleanup import (
    OutputCleanupSafetyError,
    cleanup_output_files,
)
from northstar_quant.platform.db.init_db import init_db
from northstar_quant.application.scheduler import run_scheduler
from northstar_quant.application.live_service import (
    analyze_live_position_drift,
    cancel_stale_orders_once,
    execute_latest_targets_once,
    poll_orders_and_fills_once,
    preview_rebalance,
    recent_anomaly_events,
    recent_account_attributions,
    recent_run_health,
    recent_trade_attributions,
    run_live_preflight,
    run_live_once,
    run_runtime_risk_monitor_once,
    run_shadow_once,
    soak_summary,
    sync_broker_once,
)
from northstar_quant.application.target_service import generate_daily_targets_once
from northstar_quant.platform.observability.logging.logger import get_logger, setup_logging
from northstar_quant.application.health import run_healthcheck
from northstar_quant.platform.security import redact, redact_text
from northstar_quant.platform.observability.monitoring.database_backup_readiness import (
    evaluate_database_backup_readiness,
)
from northstar_quant.platform.reporting.email_sender import send_report_via_email
from northstar_quant.platform.reporting.pdf_renderer import markdown_to_pdf
from northstar_quant.application.reporting import (
    build_report_email_subject,
    build_periodic_backtest_view,
    build_markdown_report,
    latest_live_account_attribution_summary,
    record_daily_anomaly_events,
    build_backtest_report,
)
from northstar_quant.portfolio_risk.portfolio.strategy_pipeline import (
    parse_strategy_selection,
)

_HELP_CONTEXT_SETTINGS: Final = {"help_option_names": ["-h", "--help"]}
_PROFILE_OPTION_HELP: Final = "交易画像 ID，默认使用配置中的默认画像。"


class ChineseTyperGroup(TyperGroup):
    """为命令组提供中文帮助选项。"""

    def get_help_option(self, ctx: click.Context) -> click.Option | None:
        option = super().get_help_option(ctx)
        if option is not None:
            option.help = "显示帮助并退出。"
        return option


class ChineseTyperCommand(TyperCommand):
    """为单个命令提供中文帮助选项。"""

    def get_help_option(self, ctx: click.Context) -> click.Option | None:
        option = super().get_help_option(ctx)
        if option is not None:
            option.help = "显示帮助并退出。"
        return option


# Typer 的装饰器关键字同时包含类、字典与布尔值；当前类型存根无法为这类可复用参数包
# 表达准确的 ``**kwargs`` 形状。把 Any 限制在 CLI 框架边界，业务命令保持完整类型检查。
_GROUP_KWARGS: Final[dict[str, Any]] = {
    "cls": ChineseTyperGroup,
    "context_settings": _HELP_CONTEXT_SETTINGS,
    "add_completion": False,
}
_CALLBACK_KWARGS: Final[dict[str, Any]] = {
    "cls": ChineseTyperGroup,
    "context_settings": _HELP_CONTEXT_SETTINGS,
}
_COMMAND_KWARGS: Final[dict[str, Any]] = {
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
ops_app = typer.Typer(help="运维就绪检查命令。", **_GROUP_KWARGS)
backup_app = typer.Typer(help="PostgreSQL 备份与恢复证据命令。", **_GROUP_KWARGS)

app.add_typer(backtest_app, name="backtest")
app.add_typer(research_app, name="research")
app.add_typer(live_app, name="live")
app.add_typer(report_app, name="report")
app.add_typer(dashboard_app, name="dashboard")
app.add_typer(data_app, name="data")
ops_app.add_typer(backup_app, name="backup")
app.add_typer(ops_app, name="ops")


def _log_message(message: str, level: int = logging.INFO, **context: object) -> None:
    logger.bind(**cast(dict[str, Any], redact(context))).log(level, redact_text(message))


def _log_json(payload: object, level: int = logging.INFO, **context: object) -> None:
    logger.bind(**cast(dict[str, Any], redact(context))).log(
        level,
        json.dumps(redact(payload), ensure_ascii=False, indent=2, default=str),
    )


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
    # Typer 的完成回调运行时接受 None，当前类型签名却把 param 标为必填；该参数不会被使用。
    completion_parameter = cast(click.Parameter, None)
    if install_completion:
        install_callback(ctx, completion_parameter, install_completion)
    if show_completion:
        show_callback(ctx, completion_parameter, show_completion)


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
    "health",
    short_help="检查项目当前运行状态。",
    help="检查项目当前运行状态，包括目录、环境和券商连接模式等基础健康信息。",
    **_COMMAND_KWARGS,
)
def health_command(
    fail_on_blocked: bool = typer.Option(
        False,
        "--fail-on-blocked",
        help="健康状态为 blocked 时以退出码 2 退出；默认仅输出检查结果。",
    ),
) -> None:
    """检查项目当前运行状态。"""

    payload = run_healthcheck()
    _log_json(payload, command="health")
    if fail_on_blocked and payload.get("status") == "blocked":
        raise typer.Exit(code=2)


@backup_app.command("status", **_COMMAND_KWARGS)
def backup_status_command(
    config: Path | None = typer.Option(
        None,
        "--config",
        help="备份就绪策略路径；默认 configs/maintenance/database_backup_readiness.yaml。",
    ),
) -> None:
    """只读检查 PostgreSQL 备份与隔离恢复演练证据，不执行备份或恢复。"""

    try:
        policy = load_database_backup_readiness_policy(config)
        result = evaluate_database_backup_readiness(
            policy,
            storage_dir=get_settings().storage_dir,
        )
    except (DatabaseBackupReadinessConfigError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _log_json(result.to_dict(), command="ops.backup.status")


@data_app.command("profiles", **_COMMAND_KWARGS)
def data_profiles_command() -> None:
    """列出当前可用的交易画像与路径规划。"""

    _log_json(list_profile_data_summaries(), command="data.profiles")


@data_app.command("providers", **_COMMAND_KWARGS)
def data_providers_command() -> None:
    """列出当前已注册的技术数据 adapter（不代表数据授权）。"""

    _log_json(
        {
            "providers": list_data_providers(),
            "note": "adapter 可用不等于供应商合同、用途或候选研究准入已获授权。",
        },
        command="data.providers",
    )


@data_app.command("sources", **_COMMAND_KWARGS)
def data_sources_command() -> None:
    """列出配置化数据源、授权状态和候选研究资格。"""

    _log_json({"sources": list_data_source_summaries()}, command="data.sources")


@data_app.command("download", **_COMMAND_KWARGS)
def data_download_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
    provider: str | None = typer.Option(
        None,
        "--provider",
        help="仅可重复指定画像已绑定的 adapter；不可借此切换数据来源。",
    ),
) -> None:
    """根据交易画像下载或生成数据，并规范落盘。"""

    resolved_profile = resolve_profile_id(profile)
    try:
        result = download_profile_data(resolved_profile, provider_override=provider)
    except (KeyError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _log_json(
        asdict(result),
        command="data.download",
        profile=resolved_profile,
        data_source=result.data_source,
    )


@data_app.command("import-file", **_COMMAND_KWARGS)
def data_import_file_command(
    source: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help="已核验的 Parquet 或 CSV 数据文件。",
    ),
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
) -> None:
    """校验并导入禁止自动下载的本地数据制品。"""

    resolved_profile = resolve_profile_id(profile)
    try:
        result = import_profile_data(source, resolved_profile)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _log_json(
        asdict(result),
        command="data.import-file",
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


@data_app.command("cleanup", **_COMMAND_KWARGS)
def data_cleanup_command(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="实际删除；缺省时只预览，且仍需在清理策略中显式 enabled=true。",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        help="运行输出清理策略 YAML；默认 configs/maintenance/output_retention.yaml。",
    ),
) -> None:
    """预览或清理过期下载缓存与安全临时文件，绝不处理标准行情或报告。"""

    try:
        policy = load_output_retention_policy(config)
        result = cleanup_output_files(policy, apply=apply)
    except (OutputRetentionConfigError, OutputCleanupSafetyError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _log_json(
        asdict(result),
        command="data.cleanup",
        mode=result.mode,
        planned_target_count=len(result.plan.targets),
        deleted_file_count=len(result.deleted_paths),
    )


@research_app.command("futures-trend", **_COMMAND_KWARGS)
def research_futures_trend_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
) -> None:
    """运行指定画像的离线回测，并输出研究摘要。"""

    resolved_profile = resolve_profile_id(profile)
    result = run_profile_backtest(profile_id=resolved_profile)
    _log_json(result, command="research.futures-trend", strategy="futures_trend", profile=resolved_profile)


@research_app.command("assess", **_COMMAND_KWARGS)
def research_assess_command(
    strategy: str = typer.Argument("portfolio"),
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
    require_pass: bool = typer.Option(
        False,
        "--require-pass",
        help="准入不是 PASS 时以非零状态退出，适用于人工审批或 CI 门禁。",
    ),
) -> None:
    """运行同一回测工作流，并只输出候选策略研究准入结论。"""

    resolved_profile = resolve_profile_id(profile)
    try:
        run = run_profile_backtest_run(
            resolved_profile,
            strategy_ids=parse_strategy_selection(strategy),
        )
    except FileNotFoundError as exc:
        raise typer.BadParameter(
            f"{exc}。请先下载或导入已核验的数据制品。"
        ) from exc
    except (LookupError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    admission = run.analytics["admission"]
    _log_json(
        {"run_id": run.run_id, "admission": admission},
        command="research.assess",
        strategy=strategy,
        profile=resolved_profile,
    )
    # 审批脚本和 CI 需要可捕获的结构化输出；日志并不等同于 CLI 标准输出。
    typer.echo(
        json.dumps(
            {"run_id": run.run_id, "admission": admission},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    if require_pass and isinstance(admission, dict) and admission.get("status") != "PASS":
        raise typer.Exit(code=2)


@backtest_app.command("run", **_COMMAND_KWARGS)
def backtest_run_command(
    strategy: str = typer.Argument("portfolio"),
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
) -> None:
    """运行唯一历史回测工作流，并生成带运行清单的报告。"""

    resolved_profile = resolve_profile_id(profile)
    try:
        run = run_profile_backtest_run(
            resolved_profile,
            strategy_ids=parse_strategy_selection(strategy),
        )
    except FileNotFoundError as exc:
        raise typer.BadParameter(
            f"{exc}。请先执行 `northstar data download --profile {resolved_profile}` "
            "或导入已审计的实际合约数据。"
        ) from exc
    except (LookupError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    try:
        report_path = build_backtest_report(run)
    except (LookupError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _log_json(
        {**run.metrics, "run_id": run.run_id},
        command="backtest.run",
        strategy=strategy,
        profile=resolved_profile,
    )
    _log_message(
        f"报告已生成：{report_path}",
        command="backtest.run",
        strategy=strategy,
        profile=resolved_profile,
    )


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


@report_app.command("yearly", **_COMMAND_KWARGS)
def yearly_report_command(
    strategy: str = typer.Option("portfolio", "--strategy", "-s"),
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
    send_email: bool = typer.Option(False, "--send-email", help="生成后立即发送邮件。"),
    send_pdf: bool = typer.Option(True, "--send-pdf/--no-send-pdf", help="发送邮件时是否自动附加 PDF 报告"),
) -> None:
    _report_command("yearly", strategy, profile=profile, send_email=send_email, send_pdf=send_pdf)


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
    """串行生成日频目标并执行一次，主要用于人工操作。"""

    resolved_profile = resolve_profile_id(profile)
    messages = run_live_once(profile_id=resolved_profile)
    _log_json(messages, command="live.run", profile=resolved_profile)


@live_app.command("signal", **_COMMAND_KWARGS)
def live_signal_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
) -> None:
    """收盘后生成并冻结一份日频目标快照。"""

    resolved_profile = resolve_profile_id(profile)
    snapshot = generate_daily_targets_once(profile_id=resolved_profile)
    _log_json(
        snapshot.to_dict(),
        command="live.signal",
        profile=resolved_profile,
    )


@live_app.command("execute", **_COMMAND_KWARGS)
def live_execute_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
) -> None:
    """读取最新冻结目标并执行一次盘中再平衡。"""

    resolved_profile = resolve_profile_id(profile)
    messages = execute_latest_targets_once(profile_id=resolved_profile)
    _log_json(messages, command="live.execute", profile=resolved_profile)


@live_app.command("risk-check", **_COMMAND_KWARGS)
def live_risk_check_command(
    profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_OPTION_HELP),
) -> None:
    """独立检查账户、保证金、持仓和实时行情风险。"""

    resolved_profile = resolve_profile_id(profile)
    result = run_runtime_risk_monitor_once(profile_id=resolved_profile)
    _log_json(result, command="live.risk-check", profile=resolved_profile)


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
    try:
        run = run_profile_backtest_run(
            resolved_profile,
            strategy_ids=parse_strategy_selection(strategy),
        )
    except (LookupError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    periodic_view = build_periodic_backtest_view(run.result, report_type)
    live_account_attribution = (
        latest_live_account_attribution_summary(profile_id=resolved_profile)
        if report_type == "daily"
        else None
    )
    path = build_markdown_report(
        report_type,
        strategy,
        periodic_view["metrics"],
        run.latest_holdings,
        period_label=str(periodic_view["period_label"]),
        artifact_period=str(periodic_view["artifact_period"]),
        profile_id=resolved_profile,
        analytics=periodic_view["analytics"],
        benchmark_symbol=run.profile.benchmark_symbol,
        live_account_attribution=live_account_attribution,
        backtest_run=run.manifest_mapping(),
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
        "src/northstar_quant/application/dashboard.py",
        "--server.address",
        # Settings 也会拒绝非 loopback 值；这里再次固定地址，避免未来调用方绕过配置层。
        "127.0.0.1",
        "--server.port",
        str(settings.dashboard_port),
        "--server.headless",
        "true",
        "--server.enableCORS",
        "true",
        "--server.enableXsrfProtection",
        "true",
        "--server.enableStaticServing",
        "false",
        "--server.fileWatcherType",
        "none",
        "--browser.gatherUsageStats",
        "false",
        "--client.toolbarMode",
        "viewer",
        "--client.showErrorDetails",
        "none",
        "--client.showErrorLinks",
        "false",
    ]
    raise typer.Exit(code=subprocess.call(cmd))
