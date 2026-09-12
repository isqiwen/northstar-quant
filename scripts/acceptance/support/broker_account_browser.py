"""Presentation acceptance for exact P&L and unknown cash; no broker commands."""

from urllib.parse import urlsplit
from uuid import uuid4

from northstar_quant.web.protobuf import methods, pack


def check_broker_account(page, base_url, visit, screenshot):
    from playwright.sync_api import expect

    identifier, entry = str(uuid4()), str(uuid4())
    prefix = f"/api/broker/queries/{identifier}"
    values = {
        "": {"batch_id": identifier, "instrument": "SYNTHETIC", "status": "COMPLETE"},
        "/baseline-context": {"baseline": None},
        "/ledger-context": {
            "entries": [],
            "checks": [],
            "accounting_projection": {
                "status": "INCOMPLETE",
                "through_entry_id": entry,
                "realized_pnl_before_fees": "100.010000000000000001",
                "cash": None,
                "total_fees": None,
                "fill_count": 2,
                "pending_fee_fill_ids": ["synthetic-a", "synthetic-b"],
            },
        },
        "/funds-context": {"entries": []},
    }

    def reply(route):
        assert route.request.method == "GET", "presentation cannot send broker commands"
        suffix = urlsplit(route.request.url).path.removeprefix(prefix)
        descriptor = methods("live")[("GET", "/api/broker/queries/{batch_id}" + suffix)]
        route.fulfill(
            content_type="application/protobuf",
            body=pack(descriptor.output_type, values[suffix]).SerializeToString(),
        )

    pattern = "**" + prefix + "**"
    page.route(pattern, reply)
    try:
        visit(base_url + f"/broker/{identifier}")
        page.get_by_role("tab", name="持仓与委托核对", exact=True).click()
        expect(page.get_by_text("已确认成交的账户计价", exact=True)).to_be_visible()
        expect(page.get_by_text("100.010000000000000001", exact=True)).to_be_visible()
        expect(page.get_by_text("未核定", exact=True)).to_be_visible()
        expect(page.get_by_text("未知", exact=True)).to_be_visible()
        screenshot("broker-account-unknown-cash")
        values["/ledger-context"]["accounting_projection"] = {
            "status": "UNAVAILABLE",
            "through_entry_id": entry,
            "cash": None,
        }
        page.get_by_role("button", name="刷新核对", exact=True).click()
        expect(page.get_by_text("暂不可计价", exact=True)).to_be_visible()
        expect(page.get_by_text("100.010000000000000001", exact=True)).not_to_be_visible()
    finally:
        page.unroute(pattern)
