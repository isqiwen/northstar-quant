"""First-owner browser acceptance against actual independently installed workspaces."""

from playwright.sync_api import Browser, expect

from northstar_quant.web.auth_pb2 import BrowserSession, LoginRequest
from northstar_quant.web.protobuf import decode
from support.processes import InstalledApplication


def check_workspace(app: InstalledApplication, browser: Browser) -> None:
    for role, name in (
        ("data-api", "Data Hub"),
        ("research-api", "Research"),
        ("live-api", "Live"),
    ):
        context = browser.new_context()
        errors = []
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            for first_visit in (True, False):
                with app.web(role, authenticate=False) as url:
                    page.goto(url)
                    heading = ("创建账号" if first_visit else "登录") + " Northstar " + name
                    expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
                    state = context.request.get(url + "/api/browser-session")
                    assert (
                        decode(BrowserSession.DESCRIPTOR, state.body())["setup_required"]
                        == first_visit
                    )
                    assert not decode(BrowserSession.DESCRIPTOR, state.body())["authenticated"]
                    page.get_by_label("用户名", exact=True).fill("owner")
                    page.get_by_label("工作台密码", exact=True).fill(app.workspace_password)
                    if first_visit:
                        page.get_by_label("确认密码", exact=True).fill("mismatch")
                        page.get_by_role("button", name="创建账号", exact=True).click()
                        expect(page.get_by_text("两次密码不一致", exact=True)).to_be_visible()
                        page.get_by_label("确认密码", exact=True).fill(app.workspace_password)
                    page.get_by_role(
                        "button", name="创建账号" if first_visit else "登录", exact=True
                    ).click()
                    expect(page.get_by_role("button", name="退出登录", exact=True)).to_be_visible()
                    state = context.request.get(url + "/api/browser-session")
                    assert decode(BrowserSession.DESCRIPTOR, state.body())["authenticated"]
                    duplicate = context.request.post(
                        url + "/api/setup",
                        data=LoginRequest(
                            username="intruder", password="other"
                        ).SerializeToString(),
                        headers={"Content-Type": "application/protobuf", "Origin": url},
                    )
                    assert duplicate.status == 409
                    page.get_by_role("button", name="退出登录", exact=True).click()
                    expect(
                        page.get_by_role("heading", name="登录 Northstar " + name, exact=True)
                    ).to_be_visible()
                    assert not decode(
                        BrowserSession.DESCRIPTOR,
                        context.request.get(url + "/api/browser-session").body(),
                    )["authenticated"]
                    page.goto("about:blank")
            assert not errors, errors
            print(
                f"{name}: browser setup, duplicate refusal, logout and restart identity passed",
                flush=True,
            )
        finally:
            context.close()
