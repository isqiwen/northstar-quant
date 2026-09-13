"""Exercise the cancellation UI with synthetic intercepted replies, never an SDK."""

from urllib.parse import urlsplit
from uuid import uuid4

from northstar_quant.web.protobuf import decode, methods, pack


def check_order_cancel(page, base_url, visit, screenshot, runtime):
    from playwright.sync_api import expect

    order_id, stream_id = str(uuid4()), str(uuid4())
    detail_path = f"/api/orders/{order_id}"
    commands = []
    record = dict(
        order_id=order_id,
        contract_id=str(uuid4()),
        authorization_id=str(uuid4()),
        runtime_id=runtime["runtime_id"],
        attempt_id=str(uuid4()),
        status="UNKNOWN",
        quantity_lots=1,
        filled_lots=0,
        fee_pending_lots=0,
        requires_reconciliation=True,
        reservation=dict(
            reserved_fee="12.340000000000000001",
            reserved_margin="3000",
            reserved_gross="30000",
            reserved_loss="50",
            reserved_close_lots=0,
        ),
        order={},
    )

    def reply(route):
        path = urlsplit(route.request.url).path
        if path == "/api/streams":
            schema = path
            value = [{"stream_id": stream_id, "status": "RECEIVING", "paused": True}]
        elif path == detail_path:
            schema = "/api/orders/{order_id}"
            value = dict(record=record, events=[], next_after=None)
        else:
            assert path == detail_path + "/cancel" and route.request.method == "POST"
            schema = "/api/orders/{order_id}/cancel"
            descriptor = methods("live")[("POST", schema)]
            command = decode(descriptor.input_type, route.request.post_data_buffer)
            assert command["stream_id"] == stream_id
            assert route.request.headers["x-live-runtime-id"] == runtime["runtime_id"]
            commands.append(command)
            value = dict(
                request_id=command["request_id"], status="ATTEMPT_RECORDED", order_id=order_id
            )
        descriptor = methods("live")[(route.request.method, schema)]
        route.fulfill(
            content_type="application/protobuf",
            body=pack(descriptor.output_type, value).SerializeToString(),
        )

    patterns = ["**" + detail_path + "**", "**/api/streams"]
    for pattern in patterns:
        page.route(pattern, reply)
    try:
        visit(base_url + f"/orders/{order_id}")
        expect(page.get_by_role("heading", name="订单事实", exact=True)).to_be_visible()
        page.get_by_label("当前接收会话", exact=True).click()
        page.get_by_title(stream_id, exact=True).click()
        page.get_by_role("button", name="请求撤销此订单", exact=True).click()
        expect(page.get_by_text("内核已确认操作", exact=True)).to_be_visible()
        assert len(commands) == 1
        # A requested cancellation did not rewrite the observed order or budget.
        expect(page.get_by_text("12.340000000000000001", exact=True)).to_be_visible()
        expect(page.get_by_text("订单结果待核对", exact=True)).to_be_visible()
        screenshot("cancel-request-retains-budget")
    finally:
        for pattern in patterns:
            page.unroute(pattern)
