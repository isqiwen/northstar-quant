from __future__ import annotations

from typing import Any

import httpx

from northstar_quant.platform.config.settings import get_settings
from northstar_quant.platform.observability.monitoring.alerts import send_alert


class _DummyResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None


def test_send_alert_to_private_ntfy(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_post(
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> _DummyResponse:
        captured["url"] = url
        captured["content"] = content
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _DummyResponse(200)

    monkeypatch.setenv("NORTHSTAR_ALERT_MODE", "ntfy")
    monkeypatch.setenv("NORTHSTAR_NTFY_BASE_URL", "https://ntfy.example.test/")
    monkeypatch.setenv("NORTHSTAR_NTFY_TOPIC", "northstar_alerts")
    monkeypatch.setenv("NORTHSTAR_NTFY_TOKEN", "tk_12345678901234567890123456789")
    monkeypatch.setenv("NORTHSTAR_NTFY_TIMEOUT_SECONDS", "6.5")
    monkeypatch.setattr("northstar_quant.platform.observability.monitoring.alerts.httpx.post", fake_post)
    get_settings.cache_clear()

    try:
        result = send_alert("回测完成", level="warning")
    finally:
        get_settings.cache_clear()

    assert result == "私有 ntfy 告警提交成功，HTTP 200"
    assert captured["url"] == "https://ntfy.example.test/northstar_alerts"
    assert captured["content"] == "【警告】\n回测完成".encode("utf-8")
    assert captured["timeout"] == 6.5
    assert captured["headers"] == {
        "Authorization": "Bearer tk_12345678901234567890123456789",
        "Content-Type": "text/plain; charset=utf-8",
        "Title": "Northstar Quant",
        "Priority": "4",
        "Tags": "warning,northstar_quant",
    }


def test_send_alert_to_private_ntfy_skips_when_config_is_incomplete(monkeypatch):
    monkeypatch.setenv("NORTHSTAR_ALERT_MODE", "ntfy")
    monkeypatch.delenv("NORTHSTAR_NTFY_BASE_URL", raising=False)
    monkeypatch.delenv("NORTHSTAR_NTFY_TOPIC", raising=False)
    monkeypatch.delenv("NORTHSTAR_NTFY_TOKEN", raising=False)
    get_settings.cache_clear()

    try:
        result = send_alert("服务已启动", level="info")
    finally:
        get_settings.cache_clear()

    assert result == "私有 ntfy 地址、主题或令牌未配置，已跳过发送。"


def test_ntfy_delivery_failure_does_not_raise_or_replace_business_result(monkeypatch):
    def failing_post(*args: Any, **kwargs: Any) -> _DummyResponse:
        raise httpx.ConnectError("ntfy unavailable")

    monkeypatch.setenv("NORTHSTAR_ALERT_MODE", "ntfy")
    monkeypatch.setenv("NORTHSTAR_NTFY_BASE_URL", "https://ntfy.example.test")
    monkeypatch.setenv("NORTHSTAR_NTFY_TOPIC", "northstar_alerts")
    monkeypatch.setenv("NORTHSTAR_NTFY_TOKEN", "tk_12345678901234567890123456789")
    monkeypatch.setattr("northstar_quant.platform.observability.monitoring.alerts.httpx.post", failing_post)
    get_settings.cache_clear()

    try:
        result = send_alert("撤单完成", level="error")
    finally:
        get_settings.cache_clear()

    assert result == "私有 ntfy 告警发送失败（ConnectError），已记录本地日志。"
