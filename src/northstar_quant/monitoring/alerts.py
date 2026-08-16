"""Alert delivery helpers."""

from __future__ import annotations

import logging
from typing import Literal

import httpx

from northstar_quant.config.settings import get_settings
from northstar_quant.logging_.logger import get_logger


AlertLevel = Literal["info", "warning", "error"]
logger = get_logger(__name__, command="alert.send")
_LOG_LEVEL_MAP = {
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}
_ALERT_PREFIX = {
    "info": "【信息】",
    "warning": "【警告】",
    "error": "【错误】",
}
_NTFY_PRIORITY = {
    "info": "3",
    "warning": "4",
    "error": "5",
}
_NTFY_TAGS = {
    "info": "information,northstar_quant",
    "warning": "warning,northstar_quant",
    "error": "rotating_light,northstar_quant",
}


def _build_alert_message(message: str, level: AlertLevel) -> str:
    return f"{_ALERT_PREFIX[level]}\n{message}"


def _send_ntfy_message(message: str, level: AlertLevel) -> str:
    """向私有 ntfy topic 提交一条简短告警。"""

    settings = get_settings()
    if not settings.ntfy_base_url or not settings.ntfy_topic or not settings.ntfy_token:
        logger.bind(alert_mode="ntfy").warning("私有 ntfy 告警配置不完整，跳过发送")
        return "私有 ntfy 地址、主题或令牌未配置，已跳过发送。"

    response = httpx.post(
        f"{settings.ntfy_base_url}/{settings.ntfy_topic}",
        content=message.encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.ntfy_token}",
            "Content-Type": "text/plain; charset=utf-8",
            "Title": "Northstar Quant",
            "Priority": _NTFY_PRIORITY[level],
            "Tags": _NTFY_TAGS[level],
        },
        timeout=settings.ntfy_timeout_seconds,
    )
    response.raise_for_status()
    logger.bind(alert_mode="ntfy", http_status=response.status_code).info(
        "私有 ntfy 告警提交成功"
    )
    return f"私有 ntfy 告警提交成功，HTTP {response.status_code}"


def send_alert(message: str, level: AlertLevel = "info") -> str:
    """写入本地日志，并以最佳努力投递即时告警。

    告警本身不是交易控制面。外部 ntfy 服务不可用时，调用方仍必须保留已完成的
    风控、撤单和交易结果；本函数只返回投递状态并记录异常。
    """

    settings = get_settings()
    full_message = _build_alert_message(message, level)
    logger.bind(alert_level=level, alert_mode=settings.alert_mode).log(
        _LOG_LEVEL_MAP[level],
        full_message,
    )

    if settings.alert_mode == "console":
        return f"[ALERT/console] {full_message}"

    try:
        return _send_ntfy_message(full_message, level)
    except Exception as exc:
        logger.bind(alert_mode="ntfy", alert_level=level).exception(
            "私有 ntfy 告警发送失败；已保留本地日志，不影响业务结果。"
        )
        return f"私有 ntfy 告警发送失败（{type(exc).__name__}），已记录本地日志。"
