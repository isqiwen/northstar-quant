"""健康检查模块。"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from northstar_quant.foundation.config.database_backup_readiness import (
    DatabaseBackupReadinessConfigError,
    load_database_backup_readiness_policy,
)
from northstar_quant.foundation.config.settings import get_settings
from northstar_quant.foundation.config.trading_profile import (
    ensure_broker_profile,
    load_trading_profile,
)
from northstar_quant.foundation.config.runtime_configuration import (
    resolve_runtime_configuration,
)
from northstar_quant.data.artifacts.storage import load_profile_market_data
from northstar_quant.foundation.observability.monitoring.database_backup_readiness import (
    evaluate_database_backup_readiness,
)
from northstar_quant.foundation.observability.monitoring.snapshot import (
    OperationalSnapshot,
    ObservationState,
    load_execution_observation_states,
    observation_state_from_health,
)
from northstar_quant.foundation.observability.monitoring.metrics import MetricsRegistry


def _check(code: str, status: str, message: str, **details) -> dict:
    return {
        "code": code,
        "status": status,
        "message": message,
        "details": details,
    }


def _expected_migration_head(project_root: Path) -> str:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("Alembic 未找到迁移 head")
    return head


def run_healthcheck() -> dict:
    """执行只读系统健康检查，并区分正常、降级与阻断状态。"""

    settings = get_settings()
    checks: list[dict] = []
    storage_exists = Path(settings.storage_dir).exists()
    reports_exists = Path(settings.reports_dir).exists()
    checks.append(
        _check(
            "storage_directory",
            "pass" if storage_exists else "warn",
            "storage 目录可用。" if storage_exists else "storage 目录尚未创建。",
        )
    )
    checks.append(
        _check(
            "reports_directory",
            "pass" if reports_exists else "warn",
            "reports 目录可用。" if reports_exists else "reports 目录尚未创建。",
        )
    )

    profile = None
    try:
        profile = load_trading_profile(
            settings.default_profile_id,
            config_dir=settings.profile_config_dir,
        )
        resolve_runtime_configuration(settings=settings, profile=profile)
    except Exception as exc:
        checks.append(
            _check(
                "default_profile",
                "fail",
                f"默认交易画像无法加载：{exc}",
            )
        )
    else:
        try:
            if settings.broker in {"ctp_sim", "ctp"}:
                ensure_broker_profile(
                    profile,
                    broker=settings.broker,
                    context="health",
                )
        except ValueError as exc:
            checks.append(
                _check(
                    "default_profile",
                    "fail",
                    f"默认交易画像与券商模式不匹配：{exc}",
                    role=profile.lifecycle.role,
                )
            )
        else:
            checks.append(
                _check(
                    "default_profile",
                    "pass",
                    f"默认交易画像可加载：{profile.profile_id}。",
                    role=profile.lifecycle.role,
                )
            )

    try:
        engine = create_engine(settings.database_url, future=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            table_names = set(inspect(connection).get_table_names())
            current_revision = (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                if "alembic_version" in table_names
                else None
            )
        expected_revision = _expected_migration_head(settings.project_root)
        revision_matches = current_revision == expected_revision
        checks.append(
            _check(
                "database",
                "pass" if revision_matches else "fail",
                (
                    "PostgreSQL 可连接且迁移版本为最新。"
                    if revision_matches
                    else "PostgreSQL 可连接，但迁移版本不是当前 head。"
                ),
                current_revision=current_revision,
                expected_revision=expected_revision,
            )
        )
    except Exception as exc:
        checks.append(
            _check("database", "fail", f"PostgreSQL 健康检查失败：{exc}")
        )

    if profile is not None:
        data_path = settings.storage_dir / "market" / profile.data.path
        if not data_path.exists():
            checks.append(
                _check(
                    "market_data_artifact",
                    "warn",
                    "默认画像的数据制品尚未下载。",
                    path=str(data_path),
                )
            )
        else:
            try:
                frame = load_profile_market_data(profile)
            except Exception as exc:
                checks.append(
                    _check(
                        "market_data_artifact",
                        "fail",
                        f"数据制品校验失败：{exc}",
                    )
                )
            else:
                checks.append(
                    _check(
                        "market_data_artifact",
                        "pass",
                        "数据制品 schema、画像身份与内容哈希校验通过。",
                        row_count=frame.height,
                    )
                )

    alert_ready = (
        settings.alert_mode == "console"
        or (
            settings.alert_mode == "ntfy"
            and bool(settings.ntfy_base_url)
            and bool(settings.ntfy_topic)
            and bool(settings.ntfy_token)
        )
    )
    checks.append(
        _check(
            "alert_delivery",
            "pass" if alert_ready else "warn",
            (
                f"告警通道 {settings.alert_mode} 配置可用。"
                if alert_ready
                else f"告警通道 {settings.alert_mode} 缺少必要凭据。"
            ),
        )
    )

    try:
        backup_policy = load_database_backup_readiness_policy()
        backup_readiness = evaluate_database_backup_readiness(
            backup_policy,
            storage_dir=settings.storage_dir,
        )
    except DatabaseBackupReadinessConfigError as exc:
        checks.append(
            _check(
                "database_backup_recovery_readiness",
                "fail",
                f"PostgreSQL 备份/恢复就绪策略无效：{exc}",
            )
        )
    else:
        checks.append(
            _check(
                "database_backup_recovery_readiness",
                backup_readiness.status,
                backup_readiness.message,
                **backup_readiness.details,
            )
        )

    if settings.broker == "ctp":
        checks.append(
            _check(
                "broker_capability",
                "fail",
                "CTP 报单适配器尚未实现，禁止真实交易。",
            )
        )
    elif settings.broker == "ctp_sim":
        checks.append(
            _check(
                "broker_capability",
                "pass",
                "当前为 ctp_sim 本地语义仿真，不连接真实交易柜台。",
            )
        )
    else:
        checks.append(
            _check(
                "broker_capability",
                "pass",
                "当前为 paper 模式，不会连接真实交易柜台。",
            )
        )

    statuses = {item["status"] for item in checks}
    overall_status = (
        "blocked"
        if "fail" in statuses
        else "degraded"
        if "warn" in statuses
        else "ok"
    )
    payload = {
        "app_name": settings.app_name,
        "env": settings.env,
        "status": overall_status,
        "storage_exists": storage_exists,
        "reports_exists": reports_exists,
        "broker_mode": settings.broker,
        "live_trading_enabled": settings.live_trading_enabled,
        "kill_switch_enabled": settings.kill_switch_enabled,
        "checks": checks,
    }
    checks_by_code = {item["code"]: item["status"] for item in checks}
    job_state, risk_state, reconciliation_state = (
        load_execution_observation_states(
            profile_id=profile.profile_id,
            broker=settings.broker,
            account=settings.ctp_sim_account if settings.broker == "ctp_sim" else settings.paper_account,
        )
        if profile is not None
        else (ObservationState.UNKNOWN,) * 3
    )
    operational_snapshot = OperationalSnapshot(
        system=observation_state_from_health(overall_status),
        job_state=job_state,
        broker_state=observation_state_from_health(checks_by_code.get("broker_capability", "unknown")),
        data_staleness=observation_state_from_health(checks_by_code.get("market_data_artifact", "unknown")),
        risk_state=risk_state,
        reconciliation_state=reconciliation_state,
    ).as_dict()
    payload["operational_snapshot"] = operational_snapshot
    metrics = MetricsRegistry()
    metrics.gauge("northstar_health_status", _observation_metric_value(operational_snapshot["system"]))
    for name, state in operational_snapshot.items():
        metrics.gauge("northstar_operational_state", _observation_metric_value(state), component=name)
    payload["metrics_prometheus"] = metrics.export_prometheus()
    if settings.broker == "ctp":
        payload["ctp_execution_available"] = False
        payload["ctp_execution_reason"] = "CTP 报单适配器尚未实现。"
    elif settings.broker == "ctp_sim":
        payload["ctp_simulation_available"] = True
        payload["ctp_execution_available"] = False
        payload["ctp_execution_reason"] = "仅本地语义仿真，未连接真实 CTP 前置。"
    return payload


def _observation_metric_value(state: str) -> float:
    return {"healthy": 1.0, "degraded": 0.5, "blocked": 0.0, "unknown": -1.0}.get(state, -1.0)
