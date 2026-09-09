"""Operate all three installed React applications in isolated Chromium with synthetic data.

Uses the existing installed-process harness, an explicit disposable test database,
and no broker credentials. Live command-loss UI checks use intercepted synthetic HTTP.
"""

import argparse
import base64
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from installed.processes import InstalledApplication
from playwright.sync_api import expect, sync_playwright

from northstar_quant.web.protobuf import decode, methods, pack


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--screenshot", type=Path, required=True)
    args = parser.parse_args()
    parsed = urlsplit(os.environ.get("NORTHSTAR_TEST_DATABASE_URL", ""))
    if (
        parsed.scheme != "postgresql+psycopg"
        or parsed.path != "/northstar_quant_test"
        or parsed.query
        or parsed.fragment
    ):
        parser.error("explicit disposable northstar_quant_test required")
    environment = dict(os.environ, NORTHSTAR_DATABASE_URL=parsed.geturl())
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "NORTHSTAR_SIMNOW_CONFIG",
        "NORTHSTAR_LIVE_AUTH",
        "NORTHSTAR_LIVE_URL",
        "NORTHSTAR_TUSHARE_TOKEN",
    ):
        environment.pop(key, None)
    study = tomllib.loads(args.study.read_text())
    spec = dict(study["source"])
    filename = spec.pop("file")
    annotation = "浏览器验收 · " + str(uuid4())
    with tempfile.TemporaryDirectory(prefix="northstar-react-browser-") as temporary:
        runtime = Path(temporary)
        environment["NORTHSTAR_DATA_DIR"] = str(runtime / "sources")
        environment["NORTHSTAR_DATA_SECRET_DIR"] = str(runtime / "secrets")
        from northstar_quant.data_management.storage_identity import initialize

        for share in ("MARKET", "RESEARCH"):
            root = runtime / share.lower()
            root.mkdir()
            identity = str(uuid4())
            initialize(root, identity)
            environment[f"NORTHSTAR_{share}_DIR"] = str(root)
            environment[f"NORTHSTAR_{share}_STORAGE_ID"] = identity

        app = InstalledApplication(str(args.executable.resolve()), runtime, environment)
        app.command("maintenance", "init-db")
        authentication = app.command("maintenance", "init-auth", str(runtime / "auth"))
        app.environment["NORTHSTAR_LIVE_AUTH"] = authentication["web_auth"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1500, "height": 1050}, accept_downloads=True
            )
            page = context.new_page()
            errors = []
            network = []
            page.on("requestfailed", lambda req: network.append((req.url, req.failure)))
            page.on("pageerror", lambda error: errors.append(str(error)))

            def fulfill(route, path, value):
                descriptor = methods("live")[("GET", path)].output_type
                route.fulfill(
                    content_type="application/protobuf",
                    body=pack(descriptor, value).SerializeToString(),
                )

            def choose(label, text):
                page.get_by_label(label, exact=True).click()
                page.locator(".ant-select-dropdown:visible .ant-select-item-option").filter(
                    has_text=text
                ).first.click()
                page.keyboard.press("Escape")

            def screenshot(name):
                if name != "failure":
                    expect(page.locator(".facts").get_by_text("—", exact=True)).to_have_count(0)
                    expect(page.locator(".ant-spin-spinning")).to_have_count(0)
                page.evaluate("window.scrollTo(0, 0)")
                target = args.screenshot.resolve()
                page.screenshot(
                    path=str(target.with_name(target.stem + "-" + name + target.suffix)),
                    full_page=True,
                    animations="disabled",
                )

            try:
                imported = app.seed_source(
                    {
                        "content_base64": base64.b64encode(
                            (args.study.parent / filename).read_bytes()
                        ).decode(),
                        "filename": filename,
                        "source_name": spec["source_name"],
                        "spec": spec,
                        **study["archive"],
                        "request_id": str(uuid4()),
                    }
                )
                with app.web("data-api") as data_url:
                    page.goto(data_url + "/sync")
                    expect(page.get_by_role("heading", name="Tushare 自动同步")).to_be_visible()
                    page.get_by_label("Tushare token", exact=True).fill(
                        "synthetic-browser-test-token"
                    )
                    page.get_by_role("button", name="保存 token", exact=True).click()
                    expect(page.get_by_text("已配置（不回显）", exact=True)).to_be_visible()
                    page.reload()
                    expect(page.get_by_label("Tushare token", exact=True)).to_have_value("")
                    page.get_by_role("button", name="开始同步全部数据", exact=True).click()
                    expect(page.get_by_text("已启用", exact=True)).to_be_visible()
                    page.goto("about:blank")
                # Both frontend and API have exited. Only the independent processor
                # now owns completion; reopening the Web reads its durable outcome.
                with app.data_worker(synthetic_tushare=True) as processor:
                    imported = app.await_attempt(imported)
                    assert imported["status"] == "PUBLISHED", imported
                    import time

                    deadline = time.monotonic() + 20
                    while True:
                        sync = app.command("data", "sync")
                        if any(item["status"] == "BLOCKED" for item in sync["jobs"]):
                            break
                        assert time.monotonic() < deadline, sync
                        time.sleep(0.2)
                    assert processor.poll() is None
                    with app.api("data-api") as restarted:
                        assert (
                            json.loads(
                                app.request(restarted + f"/api/attempts/{imported['attempt_id']}")
                            )
                            == imported
                        )
                        assert processor.poll() is None
                with app.web("data-api") as data_url:
                    page.goto(data_url + "/sync")
                    expect(
                        page.get_by_text(
                            "Synthetic acceptance: provider permission denied", exact=True
                        ).first
                    ).to_be_visible()
                    screenshot("sync")
                    page.goto(data_url + f"/attempts/{imported['attempt_id']}")
                    expect(page.get_by_text("PUBLISHED", exact=True)).to_be_visible(timeout=30000)
                    page.get_by_role("link", name="查看原文来源", exact=True).click()
                    with page.expect_download() as downloaded:
                        page.get_by_role("link", name="下载归档原文", exact=True).click()
                    destination = runtime / "download.csv"
                    downloaded.value.save_as(destination)
                    assert destination.read_bytes() == (args.study.parent / filename).read_bytes()
                    page.goto(data_url + "/")
                    expect(
                        page.get_by_role("heading", name="数据管理中心", exact=True)
                    ).to_be_visible()
                    expect(page.get_by_text("无排队任务", exact=True)).to_be_visible()
                    screenshot("data")
                with app.api("data-api"), app.web("research-api") as url:
                    page.goto(url + "/factors/trend.return")
                    choose("固定数据快照", imported["snapshot_id"][:8])
                    page.get_by_label("收益窗口 · bars", exact=True).fill("2")
                    page.get_by_role("button", name="固定参数并计算", exact=True).click()
                    page.wait_for_url(re.compile("/factor-runs/"))
                    factor_path = urlsplit(page.url).path
                    expect(page.get_by_text("WARMING_UP", exact=True).first).to_be_visible()
                    page.get_by_label("追加研究说明", exact=True).fill(annotation)
                    page.get_by_role("button", name="保存说明", exact=True).click()
                    expect(page.get_by_text("说明已追加", exact=True)).to_be_visible()
                    runs = []
                    for strategy, name in (
                        ("trend.momentum", "浏览器动量"),
                        ("mean_reversion.range", "浏览器区间反转"),
                    ):
                        page.goto(url + "/configurations/new?strategy=" + strategy)
                        page.get_by_label("配置名称", exact=True).fill(name)
                        page.get_by_role("button", name="保存不可变配置", exact=True).click()
                        page.wait_for_url(url + "/")
                        choose("固定数据快照", imported["snapshot_id"][:8])
                        # The home has two configuration forms; select the run form explicitly.
                        run_form = (
                            page.locator(".ant-card")
                            .filter(has=page.get_by_text("运行固定研究", exact=True))
                            .first
                        )
                        run_form.get_by_label("固定策略配置", exact=True).click()
                        page.locator(".ant-select-dropdown:visible .ant-select-item-option").filter(
                            has_text=name
                        ).first.click()
                        page.get_by_role("button", name="运行固定研究", exact=True).click()
                        page.wait_for_url(re.compile("/runs/"))
                        runs.append(urlsplit(page.url).path.split("/")[-1])
                        expect(
                            page.get_by_role("tab", name="策略与风险决定", exact=True)
                        ).to_be_visible()
                    page.goto(url + "/")
                    for identity in runs:
                        choose("选择两个研究结果", identity[:12])
                    page.get_by_role("button", name="比较固定结果", exact=True).click()
                    expect(page.get_by_text("mean_reversion.range", exact=True)).to_be_visible()
                    version_form = (
                        page.locator(".ant-card")
                        .filter(has=page.get_by_text("登记固定策略版本", exact=True))
                        .first
                    )
                    version_form.get_by_label("固定策略配置", exact=True).click()
                    page.locator(".ant-select-dropdown:visible .ant-select-item-option").filter(
                        has_text="浏览器区间反转"
                    ).first.click()
                    page.get_by_label("策略版本名称", exact=True).fill("浏览器固定候选")
                    choose("引用此配置的研究结果", runs[-1][:12])
                    page.get_by_role("button", name="登记固定版本", exact=True).click()
                    page.wait_for_url(re.compile("/strategy-versions/"))
                    version_path = urlsplit(page.url).path
                    with page.expect_download() as download_info:
                        page.get_by_role("button", name="发布并下载固定候选", exact=True).click()
                    candidate_path = runtime / "candidate.json"
                    download_info.value.save_as(candidate_path)
                    candidate = json.loads(candidate_path.read_text())
                    assert (
                        candidate["document"]["configuration"]["config"]["strategy"]["strategy_id"]
                        == "mean_reversion.range"
                    )
                    page.goto(url + "/paper")
                    choose("固定数据快照", imported["snapshot_id"][:8])
                    choose("固定策略配置", "浏览器区间反转")
                    page.get_by_role("button", name="创建文件 Paper", exact=True).click()
                    page.wait_for_url(re.compile(r"/paper/.+"))
                    paper_path = urlsplit(page.url).path
                    page.get_by_role("button", name="推进下一条观察", exact=True).click()
                    expect(page.locator(".facts").get_by_text("1", exact=True)).to_be_visible()
                    page.goto(url + "/")
                    screenshot("research")
                with app.api("data-api"), app.web("research-api") as url:
                    page.goto(url + factor_path)
                    expect(page.get_by_text(annotation, exact=False)).to_be_visible()
                    page.goto(url + version_path)
                    expect(page.get_by_text("已发布：", exact=False)).to_be_visible()
                    page.goto(url + paper_path)
                    expect(page.locator(".facts").get_by_text("1", exact=True)).to_be_visible()
                with app.live() as owner:
                    original = app.command("status")
                    with app.web() as url:
                        page.goto(url)
                        expect(page.get_by_text("AVAILABLE", exact=True)).to_be_visible()
                        screenshot("live")
                        # Synthetic browser transport: never forwarded to any broker/kernel command.
                        observed = original["runtime_id"]
                        stream = {
                            "stream_id": "browser-synthetic",
                            "live_runtime": {"runtime_id": observed},
                            "connection": "RECEIVING",
                            "paused": True,
                            "received": 2,
                            "cursor": 2,
                            "market_age_seconds": 0,
                            "reason": "SYNTHETIC_BROWSER_ONLY",
                            "steps": [],
                            "archives": [],
                            "state": {},
                            "account_progress": {"status": "UNBOUND"},
                            "binding": {"request": {"query_batch_id": str(uuid4())}},
                        }
                        page.route(
                            "**/api/streams/browser-synthetic",
                            lambda route: fulfill(route, "/api/streams/{stream_id}", stream),
                        )
                        page.route(
                            "**/api/streams/browser-synthetic/opening-budgets",
                            lambda route: fulfill(
                                route,
                                "/api/streams/{stream_id}/opening-budgets",
                                {"budgets": [], "order_checks": []},
                            ),
                        )
                        commands = []

                        def lost(route):
                            commands.append(
                                {
                                    "body": decode(
                                        methods("live")[
                                            ("POST", "/api/streams/{stream_id}/control")
                                        ].input_type,
                                        route.request.post_data_buffer,
                                    ),
                                    "runtime": route.request.headers.get("x-live-runtime-id"),
                                }
                            )
                            route.abort("failed")

                        page.route("**/api/streams/browser-synthetic/control", lost)
                        page.goto(url + "/streams/browser-synthetic")
                        page.get_by_role("button", name="暂停影子计算", exact=True).click()
                        expect(page.get_by_text("操作结果未知", exact=True)).to_be_visible()
                        assert len(commands) == 1 and commands[0]["runtime"] == observed
                        page.reload()
                        expect(page.get_by_text("操作结果未知", exact=True)).to_be_visible()
                        assert len(commands) == 1
                        page.get_by_role(
                            "button", name="已核查记录，解除本页操作锁", exact=True
                        ).click()
                        replacement = dict(original, runtime_id=str(uuid4()))
                        page.route(
                            "**/api/live/status",
                            lambda route: fulfill(route, "/api/live/status", replacement),
                        )
                        expect(
                            page.get_by_text("Live 内核已更换，原页面控制已禁用", exact=True)
                        ).to_be_visible(timeout=10000)
                        expect(
                            page.get_by_role("button", name="恢复影子计算", exact=True)
                        ).to_be_disabled()
                        page.unroute("**/api/live/status")
                    app.assert_live(owner, original)
                    with app.web() as url:
                        page.goto(url)
                        expect(page.get_by_text("AVAILABLE", exact=True)).to_be_visible()
                        assert app.command("status")["runtime_id"] == observed
                with app.web() as url:
                    page.goto(url)
                    expect(page.get_by_text("Live 内核不可用", exact=True)).to_be_visible()
                assert not errors, errors
                print(
                    json.dumps(
                        {
                            "status": "passed",
                            "applications": ["data_hub", "research", "live"],
                            "snapshot": imported["snapshot_id"],
                            "factor": factor_path,
                            "runs": runs,
                            "candidate": candidate["candidate_id"],
                            "browser_errors": errors,
                            "broker_connected": False,
                            "live_control_evidence": "synthetic intercepted HTTP only",
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            except Exception:
                print(
                    "BROWSER DIAGNOSTICS",
                    errors,
                    network,
                    page.evaluate("Object.fromEntries(Object.entries(sessionStorage))"),
                    flush=True,
                )
                screenshot("failure")
                print(app.logs())
                raise
            finally:
                browser.close()


if __name__ == "__main__":
    main()
