"""Installed opening form against intercepted Protobuf, never a broker command."""

from uuid import uuid4

from playwright.sync_api import expect

from northstar_quant.web.protobuf import decode, methods, pack


def check_opening_order(page, base_url, runtime_id, template, screenshot):
    stream_id, budget_id, consent_id = (str(uuid4()) for _ in range(3))
    stream = {**template, "stream_id": stream_id, "status": "RECEIVING", "paused": False}
    budget = {
        "budget_id": budget_id,
        "stream_id": stream_id,
        "status": "WITHIN_BUDGET",
        "account_check": {"status": "UNCHANGED"},
        "budget": {"side": "BUY"},
        "limit_price": "3110",
    }
    requests = []

    def fulfill(route, path, value):
        descriptor = methods("live")[("GET", path)]
        route.fulfill(
            content_type="application/protobuf",
            body=pack(descriptor.output_type, value).SerializeToString(),
        )

    def submit(route):
        descriptor = methods("live")[("POST", "/api/streams/{stream_id}/opening-orders")]
        body = decode(descriptor.input_type, route.request.post_data_buffer)
        assert route.request.headers["x-live-runtime-id"] == runtime_id
        assert body["budget_id"] == budget_id and body["authorization_id"] == consent_id
        requests.append(body)
        # A known refusal unlocks the form. A later, explicitly clicked submission
        # records an uncertain order, not an uncertain HTTP command.
        value = (
            {
                "request_id": body["request_id"],
                "status": "REJECTED",
                "reason": "ACCOUNT_OBSERVATION_NOT_CURRENT",
            }
            if len(requests) == 1
            else {
                "request_id": body["request_id"],
                "order_id": body["request_id"],
                "status": "UNKNOWN",
            }
        )
        route.fulfill(
            content_type="application/protobuf",
            body=pack(descriptor.output_type, value).SerializeToString(),
        )

    patterns = [
        f"**/api/broker/opening-budgets/{budget_id}",
        f"**/api/streams/{stream_id}",
        f"**/api/streams/{stream_id}/authorizations",
        f"**/api/streams/{stream_id}/opening-orders",
    ]
    page.route(
        patterns[0], lambda route: fulfill(route, "/api/broker/opening-budgets/{budget_id}", budget)
    )
    page.route(patterns[1], lambda route: fulfill(route, "/api/streams/{stream_id}", stream))
    page.route(
        patterns[2],
        lambda route: fulfill(
            route,
            "/api/streams/{stream_id}/authorizations",
            {
                "authorizations": [
                    {
                        "authorization_id": consent_id,
                        "status": "CONSENTED",
                        "requires_current_admission": True,
                    }
                ],
                "next_before": None,
            },
        ),
    )
    page.route(patterns[3], submit)
    try:
        page.goto(base_url + f"/broker/opening-budgets/{budget_id}")
        expect(page.get_by_role("heading", name="固定开仓预算", exact=True)).to_be_visible()
        page.get_by_label("使用的执行授权", exact=True).click()
        page.get_by_title(consent_id, exact=True).click()
        button = page.get_by_role("button", name="提交一手 SimNow 开仓", exact=True)
        expect(button).to_be_enabled()
        button.click()
        expect(
            page.get_by_text("资金查询已过期，请刷新账户并重新计算预算。", exact=True)
        ).to_be_visible()
        expect(button).to_be_enabled()
        button.click()
        expect(
            page.get_by_text("操作已完成，结果仍有未知项，请查看记录", exact=True)
        ).to_be_visible()
        expect(page.locator(f'a[title="{requests[-1]["request_id"]}"]')).to_be_visible()
        expect(page.get_by_text("操作结果未知", exact=True)).not_to_be_visible()
        assert len(requests) == 2 and requests[0]["request_id"] != requests[1]["request_id"]
        screenshot("opening-order")
        stream["paused"] = True
        page.reload()
        expect(button).to_be_disabled()
        assert len(requests) == 2
    finally:
        for pattern in patterns:
            page.unroute(pattern)
