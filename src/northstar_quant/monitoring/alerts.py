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


def _build_alert_message(message: str, level: AlertLevel) -> str:
    return f"{_ALERT_PREFIX[level]}\n{message}"


def _send_wecom_markdown(message: str) -> str:
    """Send a markdown alert to WeCom."""

    settings = get_settings()
    if not settings.wecom_webhook:
        logger.bind(alert_mode="wecom").warning("企业微信 webhook 未配置，跳过发送")
        return "企业微信 webhook 未配置，已跳过发送。"

    mentioned = []
    if settings.wecom_mentioned_mobile_list:
        mentioned = [
            item.strip()
            for item in settings.wecom_mentioned_mobile_list.split(",")
            if item.strip()
        ]

    payload = {
        "msgtype": "markdown",
        "markdown": {"content": message},
    }
    if mentioned:
        payload["mentioned_mobile_list"] = mentioned

    resp = httpx.post(settings.wecom_webhook, json=payload, timeout=10.0)
    resp.raise_for_status()
    logger.bind(alert_mode="wecom", http_status=resp.status_code).info(
        "企业微信告警发送成功"
    )
    return f"企业微信告警发送成功，HTTP {resp.status_code}"


def _send_telegram_message(message: str) -> str:
    """Send a plain-text alert to Telegram."""

    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.bind(alert_mode="telegram").warning(
            "Telegram bot_token 或 chat_id 未配置，跳过发送"
        )
        return "Telegram bot_token 或 chat_id 未配置，已跳过发送。"

    payload: dict[str, str | int | bool] = {
        "chat_id": settings.telegram_chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }
    if settings.telegram_message_thread_id is not None:
        payload["message_thread_id"] = settings.telegram_message_thread_id

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    resp = httpx.post(url, json=payload, timeout=10.0)
    resp.raise_for_status()
    logger.bind(alert_mode="telegram", http_status=resp.status_code).info(
        "Telegram 告警发送成功"
    )
    return f"Telegram 告警发送成功，HTTP {resp.status_code}"


def send_alert(message: str, level: AlertLevel = "info") -> str:
    """Send an alert via the configured channel."""

    settings = get_settings()
    full_message = _build_alert_message(message, level)
    logger.bind(alert_level=level, alert_mode=settings.alert_mode).log(
        _LOG_LEVEL_MAP[level],
        full_message,
    )

    if settings.alert_mode == "wecom":
        return _send_wecom_markdown(full_message)
    if settings.alert_mode == "telegram":
        return _send_telegram_message(full_message)
    return f"[ALERT/{settings.alert_mode}] {full_message}"
