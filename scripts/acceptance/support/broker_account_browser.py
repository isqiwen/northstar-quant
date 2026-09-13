"""Presentation acceptance for exact P&L and unknown cash; no broker commands."""

from urllib.parse import urlsplit
from uuid import uuid4

from northstar_quant.web.protobuf import methods, pack


def check_broker_account(page, base_url, visit, screenshot):
    from playwright.sync_api import expect

    identifier, entry = str(uuid4()), str(uuid4())
    prefix = f"/api/broker/queries/{identifier}"
    flow = {
        "cash_flow_id": "synthetic-deposit",
        "amount": "1234567890.123456789012345678",
        "currency": "CNY",
        "transferred_at": "2026-09-07T01:00:01+00:00",
        "available_at": "2026-09-07T01:00:02+00:00",
        "source_reference": f"stream:{identifier}:2",
        "reverses_id": None,
    }
    reversal = {
        **flow,
        "cash_flow_id": "synthetic-reversal",
        "amount": "-1234567890.123456789012345678",
        "source_reference": f"stream:{identifier}:3",
        "reverses_id": flow["cash_flow_id"],
    }
    values = {
        "": {"batch_id": identifier, "instrument": "SYNTHETIC", "status": "COMPLETE"},
        "/baseline-context": {"baseline": None},
        "/ledger-context": {
            "entries": [{"entry_id": entry, "added_cash_flows": [flow, reversal]}],
            "checks": [],
            "accounting_projection": {
                "status": "INCOMPLETE",
                "through_entry_id": entry,
                "realized_pnl_before_fees": "100.010000000000000001",
                "cash": None,
                "total_fees": None,
                "fill_count": 2,
                "net_identified_cash_flow": "0",
                "cash_flow_count": 2,
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
        expect(
            page.get_by_text("总费用", exact=True).locator("..").get_by_text("未知", exact=True)
        ).to_be_visible()
        screenshot("broker-account-unknown-cash")
        page.get_by_role("tab", name="资金与费用", exact=True).click()
        expect(page.get_by_text("已识别资金流水（不代表完整资金核对）", exact=True)).to_be_visible()
        expect(page.get_by_text(flow["amount"], exact=True)).to_be_visible()
        expect(page.get_by_text(reversal["amount"], exact=True)).to_be_visible()
        expect(page.get_by_role("link", name="回报 2", exact=True)).to_have_attribute(
            "href", f"/streams/{identifier}"
        )
        expect(page.get_by_role("row").filter(has_text="synthetic-reversal")).to_contain_text(
            "synthetic-deposit"
        )
        screenshot("broker-cash-flow-reversal")
        page.get_by_role("tab", name="持仓与委托核对", exact=True).click()
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
