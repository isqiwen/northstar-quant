from __future__ import annotations

from typing import Any

from northstar_quant.config.settings import get_settings
from northstar_quant.monitoring.alerts import send_alert


class _DummyResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None


def test_send_alert_to_telegram(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_post(url: str, *, json: dict[str, Any], timeout: float):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _DummyResponse(200)

    monkeypatch.setenv("NORTHSTAR_ALERT_MODE", "telegram")
    monkeypatch.setenv("NORTHSTAR_TELEGRAM_BOT_TOKEN", "123456:abc")
    monkeypatch.setenv("NORTHSTAR_TELEGRAM_CHAT_ID", "-100123456")
    monkeypatch.setenv("NORTHSTAR_TELEGRAM_MESSAGE_THREAD_ID", "7")
    monkeypatch.setattr("northstar_quant.monitoring.alerts.httpx.post", fake_post)
    get_settings.cache_clear()

    try:
        result = send_alert("回测完成", level="warning")
    finally:
        get_settings.cache_clear()

    assert result == "Telegram 告警发送成功，HTTP 200"
    assert captured["url"] == "https://api.telegram.org/bot123456:abc/sendMessage"
    assert captured["timeout"] == 10.0
    assert captured["json"]["chat_id"] == "-100123456"
    assert captured["json"]["message_thread_id"] == 7
    assert captured["json"]["disable_web_page_preview"] is True
    assert captured["json"]["text"] == "【警告】\n回测完成"


def test_send_alert_to_telegram_skips_when_missing_config(monkeypatch):
    monkeypatch.setenv("NORTHSTAR_ALERT_MODE", "telegram")
    monkeypatch.delenv("NORTHSTAR_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("NORTHSTAR_TELEGRAM_CHAT_ID", raising=False)
    get_settings.cache_clear()

    try:
        result = send_alert("服务已启动", level="info")
    finally:
        get_settings.cache_clear()

    assert result == "Telegram bot_token 或 chat_id 未配置，已跳过发送。"
