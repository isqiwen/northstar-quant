"""Actual installed closing form; every simulated-money command is intercepted."""

from uuid import uuid4

from playwright.sync_api import expect

from northstar_quant.web.protobuf import decode, methods, pack


def check_closing_order(page, base_url, runtime_id, template, screenshot):
    stream_id, budget_id, consent_id, order_id, query_id = (str(uuid4()) for _ in range(5))
    stream = {
        **template,
        "stream_id": stream_id,
        "status": "RECEIVING",
        "paused": True,
        "latest_query": {
            "status": "COMPLETE",
            "reason": "FIXED_RECEIVER_QUERY",
            "through_sequence": 10,
            "source_hash": "synthetic",
            "completeness": {},
            "query_id": query_id,
        },
    }
    budget = {"budget_id": budget_id, "stream_id": stream_id}
    record = {
        "order_id": order_id,
        "contract_id": str(uuid4()),
        "authorization_id": consent_id,
        "runtime_id": runtime_id,
        "attempt_id": str(uuid4()),
        "status": "FILLED",
        "quantity_lots": 1,
        "filled_lots": 1,
        "fee_pending_lots": 1,
        "requires_reconciliation": False,
        "reservation": {
            "reserved_fee": "4",
            "reserved_margin": "0",
            "reserved_gross": "0",
            "reserved_loss": "0",
            "reserved_close_lots": 0,
        },
        "order": {"offset": "OPEN", "observation_id": budget_id},
    }
    requests = []

    def fulfill(route, path, value):
        route.fulfill(
            content_type="application/protobuf",
            body=pack(methods("live")[("GET", path)].output_type, value).SerializeToString(),
        )

    def submit(route):
        descriptor = methods("live")[("POST", "/api/streams/{stream_id}/closing-orders")]
        body = decode(descriptor.input_type, route.request.post_data_buffer)
        assert route.request.headers["x-live-runtime-id"] == runtime_id
        assert body["opening_order_id"] == order_id and body["query_id"] == query_id
        assert body["authorization_id"] == consent_id and body["limit_price"] == "3110"
        requests.append(body)
        value = {
            "request_id": body["request_id"],
            "status": "UNKNOWN",
            "order_id": body["request_id"],
        }
        route.fulfill(
            content_type="application/protobuf",
            body=pack(descriptor.output_type, value).SerializeToString(),
        )

    patterns = [
        f"**/api/orders/{order_id}?*",
        f"**/api/broker/opening-budgets/{budget_id}",
        f"**/api/streams/{stream_id}",
        f"**/api/streams/{stream_id}/authorizations",
        f"**/api/streams/{stream_id}/closing-orders",
    ]
    page.route(
        patterns[0],
        lambda route: fulfill(
            route,
            "/api/orders/{order_id}",
            {"record": record, "events": [], "next_after": None},
        ),
    )
    page.route(
        patterns[1],
        lambda route: fulfill(route, "/api/broker/opening-budgets/{budget_id}", budget),
    )
    page.route(patterns[2], lambda route: fulfill(route, "/api/streams/{stream_id}", stream))
    page.route(
        patterns[3],
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
    page.route(patterns[4], submit)
    try:
        page.goto(base_url + f"/orders/{order_id}")
        page.get_by_label("使用的执行授权", exact=True).click()
        page.get_by_title(consent_id, exact=True).click()
        page.get_by_label("平仓限价", exact=True).fill("3110")
        button = page.get_by_role("button", name="提交一手 SimNow 平今仓", exact=True)
        expect(button).to_be_enabled()
        button.click()
        expect(
            page.get_by_text("操作已完成，结果仍有未知项，请查看记录", exact=True)
        ).to_be_visible()
        expect(page.locator(f'a[title="{requests[-1]["request_id"]}"]')).to_be_visible()
        expect(page.get_by_text("操作结果未知", exact=True)).not_to_be_visible()
        screenshot("closing-order")
        stream["status"] = "STOPPED"
        page.reload()
        expect(button).to_be_disabled()
        assert len(requests) == 1
    finally:
        for pattern in patterns:
            page.unroute(pattern)
