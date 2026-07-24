"""健康检查模块。"""

from __future__ import annotations

from pathlib import Path

from northstar_quant.config.settings import get_settings


def run_healthcheck() -> dict:
    """执行系统健康检查。"""

    settings = get_settings()
    payload = {
        "app_name": settings.app_name,
        "env": settings.env,
        "storage_exists": Path(settings.storage_dir).exists(),
        "reports_exists": Path(settings.reports_dir).exists(),
        "broker_mode": settings.broker,
    }
    if settings.broker == "ctp":
        payload["ctp_execution_available"] = False
        payload["ctp_execution_reason"] = "CTP 报单适配器尚未实现。"
    return payload
