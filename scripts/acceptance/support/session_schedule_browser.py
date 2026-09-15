"""Check local calendar selection without starting a broker connection."""

import json


def check_session_schedule(page, base_url, visit, screenshot):
    from playwright.sync_api import expect

    visit(base_url + "/streams")
    upload = page.locator('input[type="file"]')
    start = page.get_by_role("button", name="启动有界持续接收", exact=True)
    upload.set_input_files(
        {"name": "invalid.json", "mimeType": "application/json", "buffer": b"{}"}
    )
    expect(page.get_by_role("alert").filter(has_text="缺少时段依据")).to_be_visible()
    expect(start).to_be_disabled()
    page.get_by_role("button", name="清除无效文件", exact=True).click()
    expect(start).to_be_enabled()
    schedule = {
        "source_reference": "浏览器合成时段证据，不连接柜台",
        "available_at": "2026-09-10T00:00:00Z",
        "windows": [
            {
                "trading_day": "2026-09-14",
                "opens_at": "2026-09-11T21:00:00+08:00",
                "closes_at": "2026-09-11T23:00:00+08:00",
            }
        ],
    }
    upload.set_input_files(
        {
            "name": "sessions.json",
            "mimeType": "application/json",
            "buffer": json.dumps(schedule).encode(),
        }
    )
    expect(page.get_by_text("2026-09-14", exact=True)).to_be_visible()
    expect(start).to_be_enabled()
    screenshot("live-declared-sessions")
    page.get_by_role("button", name="移除时段文件", exact=True).click()
    expect(page.get_by_text("2026-09-14", exact=True)).not_to_be_visible()
