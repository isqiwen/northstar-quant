"""Consent form/protocol browser check; responses are synthetic, never execution authority."""

from datetime import datetime, timedelta
from uuid import uuid4

from northstar_quant.web.protobuf import decode, methods, pack


def check_authority(page, base_url, visit, screenshot, runtime):
    from playwright.sync_api import expect

    stream_id, authorization_id = str(uuid4()), str(uuid4())
    stream_path = f"/api/streams/{stream_id}"
    list_path = stream_path + "/authorizations"
    detail_path = f"/api/authorizations/{authorization_id}"
    records, commands = [], []
    stream = dict(
        stream_id=stream_id,
        status="RECEIVING",
        connection="RECEIVING",
        paused=False,
        received=2,
        cursor=2,
        market_age_seconds=0,
        reason="SYNTHETIC_BROWSER_ONLY",
        steps=[],
        archives=[],
        state={},
        account_progress={"status": "UNBOUND"},
        binding=dict(
            account_id="synthetic-account",
            instrument="rb2610",
            environment="SANDBOX",
            request={"configuration_id": "synthetic-fixed-configuration"},
        ),
    )

    def reply(route, path, value):
        descriptor = methods("live")[(route.request.method, path)]
        if route.request.method == "POST":
            commands.append(
                (
                    path,
                    decode(descriptor.input_type, route.request.post_data_buffer),
                    route.request.headers.get("x-live-runtime-id"),
                )
            )
        route.fulfill(
            content_type="application/protobuf",
            body=pack(descriptor.output_type, value).SerializeToString(),
        )

    def consent(route):
        if route.request.method == "GET":
            reply(
                route,
                "/api/streams/{stream_id}/authorizations",
                {"authorizations": records, "next_before": None},
            )
        else:
            record = dict(
                authorization_id=authorization_id,
                runtime_id=runtime["runtime_id"],
                status="CONSENTED",
                requires_current_admission=True,
            )
            records.append(record)
            reply(route, "/api/streams/{stream_id}/authorizations", record)

    def revoke(route):
        records[0]["status"] = "REVOKED"
        reply(route, "/api/authorizations/{identifier}/revoke", records[0])

    patterns = [
        "**" + path for path in (stream_path, list_path, detail_path, detail_path + "/revoke")
    ]
    page.route(patterns[0], lambda route: reply(route, "/api/streams/{stream_id}", stream))
    page.route(patterns[1], consent)
    page.route(
        patterns[2], lambda route: reply(route, "/api/authorizations/{identifier}", records[0])
    )
    page.route(patterns[3], revoke)
    try:
        visit(base_url + f"/streams/{stream_id}/authorizations")
        expect(page.get_by_role("heading", name="执行限额与授权", exact=True)).to_be_visible()
        expect(page.get_by_text("synthetic-account", exact=True)).to_be_visible()
        deadline = page.get_by_label("授权截止时间（本地时间，不超过本次接收结束）", exact=True)
        deadline.fill((datetime.now() + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"))
        deadline.press("Enter")
        deadline.press("Tab")
        for label, value in (
            ("每张订单最多手数", "1"),
            ("本次授权累计委托手数上限（拒单不返还额度）", "2"),
            ("单笔费用预算上限（元）", "12.50"),
            ("单笔保证金预算上限（元）", "5000"),
            ("单笔名义金额上限（元）", "50000"),
            ("单笔损失预算上限（元）", "500"),
        ):
            page.get_by_label(label, exact=True).fill(value)
        page.get_by_role("button", name="确认执行限额", exact=True).click()
        expect(
            page.get_by_role("cell", name="已确认限额，仍需就绪检查", exact=True)
        ).to_be_visible()
        assert len(commands) == 1 and commands[0][2] == runtime["runtime_id"]
        assert commands[0][1]["fee"] == "12.50" and commands[0][1]["max_total_lots"] == 2
        screenshot("execution-consent")
        page.get_by_role("button", name="撤销该授权", exact=True).click()
        expect(page.get_by_role("cell", name="已撤销", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="撤销该授权", exact=True)).to_be_disabled()
        assert len(commands) == 2 and commands[1][2] == runtime["runtime_id"]
        screenshot("execution-revoked")
    finally:
        for pattern in patterns:
            page.unroute(pattern)
