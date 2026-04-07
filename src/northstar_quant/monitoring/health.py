"""健康检查模块。"""

from __future__ import annotations

from pathlib import Path

from northstar_quant.config.settings import get_settings
from northstar_quant.live.ibkr_service import IBKRService


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
    if settings.broker == 'ibkr':
        try:
            service = IBKRService()
            payload['ibkr_connected'] = service.is_connected()
        except Exception as exc:  # pragma: no cover
            payload['ibkr_connected'] = False
            payload['ibkr_error'] = str(exc)
    return payload
