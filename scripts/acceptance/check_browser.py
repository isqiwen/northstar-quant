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

from playwright.sync_api import expect, sync_playwright
from sqlalchemy import create_engine, text
from support.authority_browser import check_authority
from support.market import seed_market
from support.processes import InstalledApplication
from support.studies import seed_learning

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
        "NORTHSTAR_LIVE_AUTH",
        "NORTHSTAR_LIVE_URL",
        "NORTHSTAR_TUSHARE_TOKEN",
    ):
        environment.pop(key, None)
    for key in tuple(environment):
        if key.startswith("NORTHSTAR_SIMNOW_"):
            environment.pop(key)
    environment["NORTHSTAR_BROKER_PROFILE"] = "simnow_dev"
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
        # The explicit disposable database belongs to this acceptance run. A previous
        # failed run must not contribute duplicate receipts or missing temporary files.
        engine = create_engine(parsed.geturl())
        try:
            with engine.begin() as connection:
                connection.execute(text("DROP SCHEMA public CASCADE"))
                connection.execute(text("CREATE SCHEMA public"))
        finally:
            engine.dispose()
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

            def visit(url):
                page.goto(url)
                if url == "about:blank":
                    return
                # A navigation may follow an API restart, which revokes its sessions.
                page.locator('input[autocomplete="current-password"], .workspace').first.wait_for(
                    state="visible"
                )
                password = page.get_by_label("工作台密码", exact=True)
                if password.is_visible():
                    password.fill(app.workspace_password)
                    page.get_by_role("button", name="登录", exact=True).click()
                    expect(page.locator(".workspace")).to_be_visible()

            def fulfill(route, path, value):
                descriptor = methods("live")[("GET", path)].output_type
                route.fulfill(
                    content_type="application/protobuf",
                    body=pack(descriptor, value).SerializeToString(),
                )

            def choose(label, text, *, search=False):
                page.get_by_label(label, exact=True).click()
                if search:
                    page.get_by_label(label, exact=True).fill(text)
                page.locator(".ant-select-dropdown:visible .ant-select-item-option").filter(
                    has_text=text
                ).first.click()
                page.keyboard.press("Escape")
                expect(page.locator(".ant-select-dropdown:visible")).to_have_count(0)

            def screenshot(name):
                if name != "failure":
                    expect(page.locator(".facts").get_by_text("—", exact=True)).to_have_count(0)
                    expect(page.locator(".ant-spin-spinning")).to_have_count(0)
                # Wait for the actual dialog, not merely its text in a scale-in frame.
                # Fast-forwarding Ant's CSS animation during capture can leave only its mask.
                for modal in page.locator(".ant-modal:visible").all():
                    expect(modal).to_have_css("transform", "none")
                    expect(modal).to_have_css("opacity", "1")
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
                    visit(data_url + "/sync")
                    expect(page.get_by_role("heading", name="Tushare 自动同步")).to_be_visible()
                    page.get_by_role("button", name="退出登录", exact=True).click()
                    expect(
                        page.get_by_role("heading", name="登录 Northstar Data Hub")
                    ).to_be_visible()
                    assert context.request.get(data_url + "/api/sync").status == 401
                    screenshot("login")
                    visit(data_url + "/sync")
                    page.get_by_label("Tushare token", exact=True).fill(
                        "synthetic-browser-test-token"
                    )
                    page.get_by_role("button", name="保存 token", exact=True).click()
                    expect(page.get_by_text("已配置（不回显）", exact=True)).to_be_visible()
                    page.reload()
                    expect(page.get_by_label("Tushare token", exact=True)).to_have_value("")
                    page.get_by_role("button", name="开始同步全部数据", exact=True).click()
                    expect(page.get_by_text("已启用", exact=True)).to_be_visible()
                    visit("about:blank")
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
                market = seed_market(app)
                with app.web("data-api") as data_url:
                    visit(data_url)
                    expect(page.get_by_role("heading", name="期货数据工作台")).to_be_visible()
                    expect(page.get_by_text("已发现合约", exact=True)).to_be_visible()
                    screenshot("overview")
                    visit(
                        data_url
                        + "/browse?dataset=1min&scope=RB2610.SHF&start=2026-09-01&end=2026-09-03"
                    )
                    expect(page.get_by_role("button", name="查询数据", exact=True)).to_be_enabled()
                    page.get_by_role("button", name="查询数据", exact=True).click()
                    expect(page.get_by_text("440 条记录", exact=True)).to_be_visible()
                    page.locator("summary").filter(has_text="查询读取统计").click()
                    expect(page.get_by_text("文件身份仍完整核验", exact=False)).to_be_visible()
                    screenshot("range-cost")
                    expect(
                        page.get_by_role("img", name="固定数据 K 线、成交量与持仓量")
                    ).to_be_visible()
                    expect(page.get_by_role("button", name="导出所选范围")).to_be_disabled()
                    expect(page.get_by_text("行情图 · 当前第 1–200 条", exact=True)).to_be_visible()
                    page.locator(".ant-pagination-item-2").first.click()
                    expect(
                        page.get_by_text("行情图 · 当前第 201–400 条", exact=True)
                    ).to_be_visible()
                    screenshot("browse")
                    visit(
                        data_url
                        + "/quality?dataset=1min&scope=RB2610.SHF&start=2026-09-01&end=2026-09-04"
                    )
                    page.get_by_role("button", name="查询数据", exact=True).click()
                    expect(page.locator(".coverage-days button")).to_have_count(4)
                    expect(
                        page.locator(".coverage-days").get_by_text("响应已校验", exact=True)
                    ).to_have_count(3)
                    page.locator(".coverage-days button").filter(has_text="2026-09-01").click()
                    screenshot("quality")
                    page.get_by_role("link", name="查看同步任务", exact=True).click()
                    expect(page.get_by_role("dialog")).to_be_visible()
                    expect(
                        page.get_by_role("dialog")
                        .get_by_text(market["request_id"], exact=False)
                        .first
                    ).to_be_visible()
                    expect(
                        page.get_by_role("dialog").get_by_role(
                            "cell", name="成交量、持仓量或金额无效", exact=True
                        )
                    ).to_be_visible()
                    screenshot("quality-issues")
                    page.get_by_role("button", name="重处理已留存响应", exact=True).click()
                    expect(page.get_by_text("已排队重处理留存响应", exact=True)).to_be_visible()
                    page.reload()
                    expect(
                        page.get_by_role("button", name="重处理已留存响应", exact=True)
                    ).to_be_disabled()
                    with app.data_worker(synthetic_tushare=True):
                        deadline = time.monotonic() + 20
                        while True:
                            sync = app.command("data", "sync")
                            result = next(
                                j for j in sync["jobs"] if j["request_id"] == market["request_id"]
                            )
                            if result["status"] == "VALIDATED":
                                break
                            assert time.monotonic() < deadline, result
                            time.sleep(0.2)
                    assert result["receipt_id"] == market["receipt_id"], result
                    assert result["attempts"] == market["attempts"], result
                    page.reload()
                    expect(
                        page.get_by_role("button", name="重处理已留存响应", exact=True)
                    ).to_be_enabled()
                    screenshot("reprocessed")
                    visit(
                        data_url
                        + "/versions?dataset=1min&scope=RB2610.SHF&start=2026-09-01&end=2026-09-03"
                    )
                    page.get_by_role("button", name="查询数据", exact=True).click()
                    page.locator(".ant-table-tbody input[type=checkbox]").nth(0).check()
                    page.locator(".ant-table-tbody input[type=checkbox]").nth(1).check()
                    page.get_by_role("button", name="比较所选版本", exact=True).click()
                    expect(
                        page.get_by_role("dialog").get_by_text(
                            "新增 0 行，删除 0 行，修改 1 行，未变 439 行", exact=True
                        )
                    ).to_be_visible()
                    dialog = page.get_by_role("dialog")
                    expect(dialog.get_by_role("cell", name="open", exact=True)).to_be_visible()
                    expect(dialog.get_by_role("cell", name="3100", exact=True)).to_be_visible()
                    expect(dialog.get_by_role("cell", name="3100.5", exact=True)).to_be_visible()
                    screenshot("revision-diff")
                    page.get_by_role("dialog").locator(".ant-modal-close").click()
                    page.get_by_role("link", name="浏览此版本", exact=True).first.click()
                    page.get_by_role("button", name="查询数据", exact=True).click()
                    expect(page.get_by_text("440 条记录", exact=True)).to_be_visible()
                    page.reload()
                    page.get_by_role("button", name="查询数据", exact=True).click()
                    expect(page.get_by_text("440 条记录", exact=True)).to_be_visible()
                    visit(data_url + "/sync")
                    expect(
                        page.get_by_text(
                            "Synthetic acceptance: provider permission denied",
                            exact=True,
                        ).first
                    ).to_be_visible()
                    screenshot("sync")
                    visit(data_url + f"/attempts/{imported['attempt_id']}")
                    expect(page.get_by_text("PUBLISHED", exact=True)).to_be_visible(timeout=30000)
                    page.get_by_role("link", name="查看原文来源", exact=True).click()
                    with page.expect_download() as downloaded:
                        page.get_by_role("link", name="下载归档原文", exact=True).click()
                    destination = runtime / "download.csv"
                    downloaded.value.save_as(destination)
                    assert destination.read_bytes() == (args.study.parent / filename).read_bytes()
                    visit(data_url + "/")
                    expect(
                        page.get_by_role("heading", name="期货数据工作台", exact=True)
                    ).to_be_visible()
                    expect(page.get_by_text("后台自动同步已启用", exact=False)).to_be_visible()
                    screenshot("data")
                    seed_market(app, compact=True)
                    visit(
                        data_url
                        + "/browse?dataset=1min&scope=RB2610.SHF&start=2026-09-01&end=2026-09-03"
                    )
                    page.get_by_role("button", name="查询数据", exact=True).click()
                    expect(page.get_by_text("固定 2 个分片", exact=True)).to_be_visible()
                    page.get_by_role("button", name="合并固定版本", exact=True).click()
                    expect(
                        page.get_by_role("heading", name="固定版本合并", exact=True)
                    ).to_be_visible()
                    expect(page.get_by_text("PENDING", exact=True)).to_be_visible()
                    fixed_url = page.url
                    visit("about:blank")
                with app.data_worker(synthetic_tushare=True):
                    with app.web("data-api") as data_url:
                        visit(data_url + urlsplit(fixed_url).path)
                        expect(page.get_by_text("SUCCEEDED", exact=True)).to_be_visible(
                            timeout=30000
                        )
                        expect(page.get_by_text("440 条记录", exact=True)).to_be_visible()
                        expect(page.get_by_role("button", name="导出所选范围")).to_be_disabled()
                        page.locator(".ant-pagination-item-2").first.click()
                        expect(
                            page.get_by_text("行情图 · 当前第 201–400 条", exact=True)
                        ).to_be_visible()
                        screenshot("compaction")
                from datetime import date, timedelta

                study_snapshots = [imported["snapshot_id"]]
                original_day = spec["trading_day"]
                for shift in (1, 2):
                    shifted_day = (
                        date.fromisoformat(original_day) + timedelta(days=shift)
                    ).isoformat()
                    shifted_spec = {
                        k: v.replace(original_day, shifted_day) if isinstance(v, str) else v
                        for k, v in spec.items()
                    }
                    shifted = app.seed_source(
                        {
                            "content_base64": base64.b64encode(
                                (args.study.parent / filename)
                                .read_text()
                                .replace(original_day, shifted_day)
                                .encode()
                            ).decode(),
                            "filename": f"study-{shift}.csv",
                            "source_name": spec["source_name"],
                            "spec": shifted_spec,
                            **study["archive"],
                            "request_id": str(uuid4()),
                        },
                        wait=True,
                    )
                    assert shifted["status"] == "PUBLISHED", shifted
                    study_snapshots.append(shifted["snapshot_id"])
                learning_snapshots = seed_learning(app, spec, study["archive"])
                with (
                    app.api("data-api"),
                    app.research_worker(),
                    app.web("research-api") as url,
                ):
                    visit(url + "/factors/trend.return")
                    choose("固定数据快照", imported["snapshot_id"][:8])
                    page.get_by_label("收益窗口 · bars", exact=True).fill("2")
                    page.get_by_role("button", name="固定参数并计算", exact=True).click()
                    page.wait_for_url(re.compile("/factor-runs/"))
                    factor_path = urlsplit(page.url).path
                    expect(page.get_by_text("WARMING_UP", exact=True).first).to_be_visible()
                    analysis = (
                        page.locator(".ant-card")
                        .filter(has=page.get_by_text("前瞻收益与稳定性诊断", exact=True))
                        .first
                    )
                    analysis.locator(".ant-table-row-expand-icon").first.click()
                    expect(analysis.get_by_text(re.compile(r"因子未就绪：[1-9]"))).to_be_visible()
                    expect(
                        analysis.get_by_role("columnheader", name="日内 Spearman", exact=True)
                    ).to_be_visible()
                    screenshot("factor-analysis")
                    page.get_by_label("追加研究说明", exact=True).fill(annotation)
                    page.get_by_role("button", name="保存说明", exact=True).click()
                    expect(page.get_by_text("说明已追加", exact=True)).to_be_visible()
                    runs = []
                    for strategy, name in (
                        ("trend.momentum", "浏览器动量"),
                        ("mean_reversion.range", "浏览器区间反转"),
                    ):
                        visit(url + "/configurations/new?strategy=" + strategy)
                        page.get_by_label("配置名称", exact=True).fill(name)
                        page.get_by_role("button", name="保存不可变配置", exact=True).click()
                        page.wait_for_url(url + "/")
                        visit(url + "/experiments/new")
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
                        page.get_by_role("button", name="提交回测", exact=True).click()
                        page.wait_for_url(re.compile("/tasks/"))
                        page.get_by_role("button", name="查看研究报告", exact=True).click()
                        page.wait_for_url(re.compile("/runs/"))
                        runs.append(urlsplit(page.url).path.split("/")[-1])
                        expect(
                            page.get_by_role("tab", name="策略与风险决定", exact=True)
                        ).to_be_visible()
                    visit(url + "/")
                    visit(url + "/experiments")
                    for identity in runs:
                        choose("选择两个研究结果", identity[:12])
                    page.get_by_role("button", name="比较固定结果", exact=True).click()
                    expect(
                        page.get_by_text("mean_reversion.range", exact=True).first
                    ).to_be_visible()
                    expect(page.get_by_text("策略参数与因子绑定", exact=True)).to_be_visible()
                    screenshot("comparison")
                    page.get_by_role("tab", name="参数实验", exact=True).click()
                    page.get_by_label("研究假设", exact=True).fill("浏览器固定参数实验")
                    for label, snapshot in zip(
                        ("训练快照", "验证快照", "测试快照"),
                        study_snapshots,
                        strict=True,
                    ):
                        choose(label, snapshot[:8])
                    for name in ("浏览器动量", "浏览器区间反转"):
                        choose("候选配置（2–64 个，仅策略或因子参数不同）", name)
                    page.get_by_role("button", name="固定计划并提交", exact=True).click()
                    study_row = page.locator("tr").filter(
                        has=page.get_by_text("浏览器固定参数实验", exact=True)
                    )
                    expect(study_row.get_by_text("完成", exact=True)).to_be_visible(timeout=60000)
                    study_row.locator(".ant-table-row-expand-icon").click()
                    expect(page.get_by_role("cell", name="测试", exact=True)).to_have_count(1)
                    expect(page.get_by_role("link", name="查看任务", exact=True)).to_have_count(5)
                    screenshot("parameter-study")
                    study_row.locator(".ant-table-row-expand-icon").click()
                    choose("实验方法", "训练期线性收益模型")
                    page.get_by_label("研究假设", exact=True).fill("浏览器训练期拟合")
                    for label, snapshot in zip(
                        ("训练快照", "验证快照", "测试快照"), learning_snapshots, strict=True
                    ):
                        choose(label, snapshot[:8])
                    choose("账户、风险与成本模板（1 个；策略由训练生成）", "浏览器动量")
                    page.get_by_role("button", name="固定计划并提交", exact=True).click()
                    learned_row = page.locator("tr").filter(
                        has=page.get_by_text("浏览器训练期拟合", exact=True)
                    )
                    expect(learned_row.get_by_text("完成", exact=True)).to_be_visible(timeout=60000)
                    learned_row.locator(".ant-table-row-expand-icon").click()
                    expect(page.get_by_role("cell", name="测试", exact=True)).to_have_count(1)
                    expect(page.get_by_role("link", name="查看任务", exact=True)).to_have_count(7)
                    screenshot("learned-study")
                    visit(url + "/candidates")
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
                    choose("引用此配置的研究结果", runs[-1][:12], search=True)
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
                    visit(url + "/paper")
                    choose("固定数据快照", imported["snapshot_id"][:8])
                    choose("固定策略配置", "浏览器区间反转")
                    page.get_by_role("button", name="创建文件 Paper", exact=True).click()
                    page.wait_for_url(re.compile(r"/paper/.+"))
                    paper_path = urlsplit(page.url).path
                    page.get_by_role("button", name="推进下一条观察", exact=True).click()
                    expect(page.locator(".facts").get_by_text("1", exact=True)).to_be_visible()
                    visit(url + "/")
                    screenshot("research")
                with (
                    app.api("data-api"),
                    app.research_worker(),
                    app.web("research-api") as url,
                ):
                    visit(url + factor_path)
                    expect(page.get_by_text(annotation, exact=False)).to_be_visible()
                    expect(page.get_by_text("前瞻收益与稳定性诊断", exact=True)).to_be_visible()
                    visit(url + version_path)
                    expect(page.get_by_text("已发布：", exact=False)).to_be_visible()
                    visit(url + paper_path)
                    expect(page.locator(".facts").get_by_text("1", exact=True)).to_be_visible()
                # Accept in the actual browser with no worker, then stop both Web services.
                with app.api("data-api"), app.web("research-api") as url:
                    visit(url + "/experiments/new")
                    choose("固定数据快照", imported["snapshot_id"][:8])
                    choose("固定策略配置", "浏览器动量")
                    page.get_by_role("button", name="提交回测", exact=True).click()
                    page.wait_for_url(re.compile("/tasks/"))
                    task_id = urlsplit(page.url).path.split("/")[-1]
                    expect(page.get_by_text("排队中", exact=True)).to_be_visible()
                    screenshot("queued")
                    visit(url + "/factors/trend.return")
                    choose("固定数据快照", learning_snapshots[0][:8])
                    page.get_by_label("收益窗口 · bars", exact=True).fill("2")
                    page.get_by_role("button", name="固定参数并计算", exact=True).click()
                    page.wait_for_url(re.compile("/factor-runs/"))
                    queued_factor_path = urlsplit(page.url).path
                    queued_factor = queued_factor_path.rsplit("/", 1)[-1]
                    expect(page.get_by_text("QUEUED", exact=True)).to_be_visible()

                assert app.command("research", "task", task_id)["status"] == "QUEUED"
                with app.research_worker():
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        completed_task = app.command("research", "task", task_id)
                        if completed_task["status"] == "SUCCEEDED":
                            break
                        assert completed_task["status"] not in {
                            "FAILED",
                            "INTERRUPTED",
                        }, completed_task
                        time.sleep(0.2)
                    assert completed_task["status"] == "SUCCEEDED", completed_task
                    import sqlite3

                    # The two Web processes and Data Hub remain stopped. Inspect only
                    # the durable completion marker, then verify result through the API.
                    with sqlite3.connect(
                        f"file:{app.environment['NORTHSTAR_RESEARCH_DATABASE']}?mode=ro", uri=True
                    ) as connection:
                        while time.monotonic() < deadline:
                            state = connection.execute(
                                "SELECT status FROM factor_runs WHERE attempt_id=?",
                                (queued_factor.replace("-", ""),),
                            ).fetchone()[0]
                            if state not in {"QUEUED", "RUNNING"}:
                                break
                            time.sleep(0.1)
                        assert state == "SUCCEEDED", state

                with app.web("research-api") as url:
                    visit(url + queued_factor_path)
                    expect(page.get_by_text("SUCCEEDED", exact=True)).to_be_visible()
                    expect(page.get_by_text("仅描述性分析", exact=True).first).to_be_visible()
                    screenshot("factor-completed-offline")
                    visit(url + "/tasks/" + task_id)
                    expect(
                        page.get_by_role("button", name="查看研究报告", exact=True)
                    ).to_be_visible()
                    screenshot("task")
                    page.get_by_role("button", name="查看研究报告", exact=True).click()
                    expect(page.get_by_role("img", name="账户回撤")).to_be_visible()
                    screenshot("report")
                    page.get_by_role("tab", name="评价条件", exact=True).click()
                    expect(page.get_by_text("已完成固定输入窗口", exact=True)).to_be_visible()
                    expect(page.get_by_text("不交易、无利息的初始现金", exact=True)).to_be_visible()
                    screenshot("evaluation")
                    page.get_by_role("tab", name="敞口与保证金", exact=True).click()
                    expect(
                        page.get_by_role("columnheader", name="手续费预占", exact=True)
                    ).to_be_visible()
                    expect(
                        page.get_by_role("columnheader", name="总名义敞口", exact=True)
                    ).to_be_visible()
                    expect(
                        page.get_by_role("columnheader", name="多头手数", exact=True)
                    ).to_be_visible()
                    screenshot("exposure")
                    page.get_by_role("tab", name="订单过程", exact=True).click()
                    expect(
                        page.get_by_role("columnheader", name="委托手数", exact=True)
                    ).to_be_visible()
                    expect(
                        page.get_by_role("cell", name="RISK_AUTHORIZED", exact=True).first
                    ).to_be_visible()
                    screenshot("orders")
                    page.get_by_role("tab", name="跨日结算", exact=True).click()
                    expect(
                        page.get_by_role("columnheader", name="盯市盈亏", exact=True)
                    ).to_be_visible()
                    screenshot("settlements")
                    page.get_by_role("tab", name="费用与保证金条款", exact=True).click()
                    page.get_by_text(
                        "该研究使用配置中的模拟费用和保证金假设，未绑定历史条款。",
                        exact=True,
                    ).wait_for(state="visible")
                    screenshot("terms")
                print(
                    "Research browser: queued task completed with frontend/API/Data Hub stopped",
                    flush=True,
                )
                local_order = app.seed_order_journal()
                with app.live() as owner:
                    original = app.command("status")
                    with app.web() as url:
                        visit(url)
                        expect(page.get_by_text("AVAILABLE", exact=True)).to_be_visible()
                        screenshot("live")
                        visit(url + "/materials")
                        expect(
                            page.get_by_role("button", name="选择并核验候选文件")
                        ).to_be_enabled()
                        page.locator('input[type="file"]').set_input_files(str(candidate_path))
                        expect(
                            page.get_by_text("候选已接收，未授予执行权限", exact=True)
                        ).to_be_visible()
                        expect(page.get_by_text("RECEIVED", exact=True)).to_be_visible()
                        screenshot("received-material")
                        visit(url + "/streams")
                        choose("本地固定配置", candidate["document"]["configuration"]["name"])
                        screenshot("received-configuration")
                        visit(url + "/orders")
                        expect(
                            page.get_by_role("heading", name="订单与预占", exact=True)
                        ).to_be_visible()
                        expect(page.get_by_role("cell", name="UNKNOWN", exact=True)).to_be_visible()
                        expect(page.get_by_role("cell", name="303", exact=True)).to_be_visible()
                        screenshot("local-orders")
                        visit(url + "/orders/" + local_order["order_id"])
                        expect(page.get_by_text("订单结果待核对", exact=True)).to_be_visible()
                        expect(
                            page.get_by_role("cell", name="发送尝试已保存", exact=True)
                        ).to_be_visible()
                        expect(
                            page.get_by_role("cell", name="柜台订单回报", exact=True)
                        ).to_be_visible()
                        screenshot("local-order-events")
                        visit(url + "/orders/" + local_order["fee_order_id"])
                        expect(page.get_by_text("费用待确认手数", exact=True)).to_be_visible()
                        expect(page.get_by_text("订单结果待核对", exact=True)).to_be_visible()
                        expect(page.get_by_text("FILLED", exact=True)).to_be_visible()
                        screenshot("local-order-pending-fees")
                        assert app.command("status")["order_sending"] is False
                        check_authority(page, url, visit, screenshot, original)
                        visit(url)
                        page.get_by_role("link", name="运行诊断", exact=True).click()
                        expect(page.get_by_text("数据库盘空闲字节", exact=True)).to_be_visible()
                        expect(
                            page.locator(".facts").get_by_text("OBSERVED", exact=True)
                        ).to_be_visible()
                        screenshot("diagnostics")
                        visit(url)
                        # Exercise selection using synthetic browser transport.
                        page.route(
                            "**/api/live/instances",
                            lambda route: fulfill(
                                route,
                                "/api/live/instances",
                                {
                                    "instances": [
                                        {
                                            "instance_id": "sim",
                                            "environment": "SANDBOX",
                                            "broker_profile": "simnow_dev",
                                        },
                                        {
                                            "instance_id": "other",
                                            "environment": "SANDBOX",
                                            "broker_profile": "simnow_trading",
                                        },
                                    ],
                                    "production_available": False,
                                },
                            ),
                        )
                        selected = []

                        def selected_status(route):
                            selected.append(route.request.headers.get("x-live-instance-id"))
                            fulfill(route, "/api/live/status", original)

                        page.route("**/api/live/status", selected_status)
                        page.reload()
                        choose("运行实例", "other")
                        expect(page.get_by_text("AVAILABLE", exact=True)).to_be_visible()
                        assert "other" in selected
                        choose("运行实例", "sim")
                        expect(page.get_by_text("AVAILABLE", exact=True)).to_be_visible()
                        page.unroute("**/api/live/status")
                        page.unroute("**/api/live/instances")

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
                        visit(url + "/streams/browser-synthetic")
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
                        visit(url)
                        expect(page.get_by_text("AVAILABLE", exact=True)).to_be_visible()
                        assert app.command("status")["runtime_id"] == observed
                with app.web() as url:
                    visit(url)
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
            except Exception as failure:
                try:
                    storage = page.evaluate("Object.fromEntries(Object.entries(sessionStorage))")
                except Exception:
                    storage = {"unavailable_at": page.url}
                print(
                    "BROWSER DIAGNOSTICS",
                    repr(failure),
                    errors,
                    network,
                    storage,
                    flush=True,
                )
                screenshot("failure")
                print(app.logs())
                raise
            finally:
                browser.close()


if __name__ == "__main__":
    main()
