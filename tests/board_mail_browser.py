"""Read-only mail board acceptance with fictional data on disposable HTTP servers."""

from pathlib import Path
import argparse
import asyncio
import socket
import sys
import tempfile
import threading
import time

import uvicorn
from playwright.sync_api import sync_playwright, expect

p = argparse.ArgumentParser()
p.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
p.add_argument("--screenshots", type=Path, required=True)
args = p.parse_args()
args.screenshots.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(args.source_root / "src"))
from academic_os.api import create_app  # noqa: E402
from academic_os.db import dumps  # noqa: E402
from academic_os.security import Auth  # noqa: E402
from test_mail_triage import hub, mail, candidate  # noqa: E402


def serve(core, remote=False):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(core, remote=remote), host="127.0.0.1", port=port, log_level="error")
    )

    def runner():
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as loop:
            loop.run(server.serve())

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            return server, thread, f"http://127.0.0.1:{port}"
        time.sleep(0.05)
    raise AssertionError("server did not start")


with tempfile.TemporaryDirectory(prefix="board-mail-") as temp:
    core = hub(Path(temp))
    Auth(core.db, core.settings).set_password("owner", "fixture-password-123")
    for i in range(22):
        mail(core, "m" + str(i))
    doc = core.get_mail("school:m1")
    doc["subject"] = "算法作业与提交日期 · 虚构示例"
    doc["body"] = "needle & mark\n<script>window.mailInjected=true</script>\n" + ("邮件原文内容。" * 200)
    with core.db.transaction() as db:
        db.execute("UPDATE mail SET document=? WHERE id=?", (dumps(doc), doc["key"]))
    one, _ = candidate(core, due=False)
    two, _ = candidate(core, role="second")
    core.review_mail_item(two["id"], "confirm", two["version"], "fixture-confirm")
    ignored, _ = candidate(core, role="ignored", due=False)
    core.review_mail_item(ignored["id"], "ignore", ignored["version"], "fixture-ignore")
    servers = [serve(core), serve(core, True)]
    errors, violations, writes, requests = [], [], [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for width, height in [(1440, 1000), (390, 844)]:
                context = browser.new_context(
                    viewport={"width": width, "height": height}, reduced_motion="reduce"
                )
                url = servers[0][2]
                context.request.post(
                    url + "/api/v1/login", data={"username": "owner", "password": "fixture-password-123"}
                )
                page = context.new_page()
                page.add_init_script(
                    "window.violations=[];document.addEventListener('securitypolicyviolation',e=>violations.push(e.violatedDirective))"
                )
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on(
                    "request",
                    lambda r: (requests.append(r.url), writes.append(r.url) if r.method != "GET" else None),
                )
                assert page.goto(url + "/board/mail.html").status == 200

                def ready():
                    page.wait_for_function("()=>document.body.dataset.serviceState==='ready'")

                ready()
                expect(page.locator(".mail-card")).to_have_count(20)
                expect(page.locator(".mail-pager")).to_contain_text("共 22 封")
                page.get_by_role("button", name="下一页", exact=True).click()
                ready()
                expect(page.locator(".mail-card")).to_have_count(2)
                search = page.get_by_role("searchbox", name="搜索邮件")
                search.fill("needle & mark")
                page.get_by_role("button", name="应用筛选", exact=True).click()
                ready()
                expect(page.locator(".mail-card")).to_have_count(1)
                expect(page.locator(".mail-card")).to_contain_text(
                    "<script>window.mailInjected=true</script>"
                )
                assert not page.evaluate("!!window.mailInjected")
                page.get_by_label("邮件核对状态", exact=True).select_option("pending")
                page.get_by_label("收件日期范围", exact=True).select_option("custom")
                page.get_by_label("收件开始日期", exact=True).fill("2026-10-13")
                page.get_by_label("收件结束日期", exact=True).fill("2026-10-13")
                page.get_by_role("button", name="应用筛选", exact=True).click()
                ready()
                page.screenshot(path=str(args.screenshots / ("mail-" + str(width) + ".png")), full_page=True)
                page.get_by_role("link", name=doc["subject"], exact=True).click()
                ready()
                expect(page.locator("summary.mail-original-toggle")).to_be_visible()
                page.locator("summary.mail-original-toggle").click()
                expect(page.locator(".mail-original")).to_be_visible()
                expect(page.locator(".mail-original")).to_have_text(doc["body"])
                expect(page.locator(".mail-candidate")).to_have_count(3)
                expect(page.locator(".mail-candidates")).to_contain_text("未知，不补造时刻")
                assert not page.evaluate("!!window.mailInjected")
                ignored_card = page.locator('.mail-candidate[data-item-id="' + ignored["id"] + '"]')
                ignored_card.get_by_role("link", name="在工作台逐条核对", exact=True).click()
                expect(page.locator("#item-table tbody tr")).to_have_count(1)
                page.get_by_role("button", name="核对邮件", exact=True).click()
                expect(page.get_by_role("button", name="恢复待核对", exact=True)).to_be_visible()
                page.keyboard.press("Escape")
                page.go_back()
                ready()
                pending = page.locator('.mail-candidate[data-item-id="' + one["id"] + '"]')
                pending.get_by_role("link", name="在工作台逐条核对", exact=True).click()
                expect(page.locator("#item-table tbody tr")).to_have_count(1)
                expect(page.locator("#item-table")).to_contain_text(one["title"])
                page.go_back()
                ready()
                page.locator('.mail-candidate[data-item-id="' + two["id"] + '"]').get_by_role(
                    "link", name="在看板查看事项", exact=True
                ).click()
                expect(page.get_by_role("link", name="查看关联原邮件 1", exact=True)).to_be_visible()
                page.get_by_role("link", name="查看关联原邮件 1", exact=True).click()
                ready()
                page.screenshot(
                    path=str(args.screenshots / ("detail-" + str(width) + ".png")), full_page=True
                )
                assert not page.evaluate("document.documentElement.scrollWidth>innerWidth")
                page.get_by_role("link", name="返回邮件列表", exact=True).click()
                ready()
                expect(page.get_by_role("searchbox", name="搜索邮件")).to_have_value("needle & mark")
                expect(page.get_by_label("邮件核对状态", exact=True)).to_have_value("pending")
                page.route("**/api/v1/mail/digest?*", lambda route: route.fulfill(status=503, json={}))
                page.get_by_role("button", name="刷新", exact=True).click()
                expect(page.locator(".mail-status-text")).to_contain_text("已保留上次读取结果")
                search.fill("another query")
                page.get_by_role("button", name="应用筛选", exact=True).click()
                expect(page.locator(".mail-card")).to_have_count(0)
                page.unroute("**/api/v1/mail/digest?*")
                page.get_by_role("button", name="清除筛选", exact=True).click()
                ready()
                # Hold an old response while a newer search completes.
                held = []

                def hold(route):
                    if "q=slow" in route.request.url:
                        held.append(route)
                    else:
                        route.continue_()

                page.route("**/api/v1/mail/digest?*", hold)
                search.fill("slow")
                page.get_by_role("button", name="应用筛选", exact=True).click()
                page.wait_for_timeout(200)
                assert held
                search.fill("needle & mark")
                page.get_by_role("button", name="应用筛选", exact=True).click()
                ready()
                held[0].fulfill(
                    status=200,
                    json={
                        "items": [],
                        "total": 0,
                        "limit": 20,
                        "offset": 0,
                        "accounts": ["school"],
                        "timezone": "Asia/Taipei",
                    },
                )
                page.wait_for_timeout(100)
                expect(page.locator(".mail-card")).to_have_count(1)
                page.unroute("**/api/v1/mail/digest?*")
                page.goto(url + "/board/mail-detail.html?key=missing")
                expect(page.locator(".mail-status-text")).to_contain_text("找不到这封邮件")
                page.goto(url + "/board/mail.html")
                ready()
                page.route("**/api/v1/session", lambda route: route.fulfill(status=401, json={}))
                page.get_by_role("button", name="刷新", exact=True).click()
                expect(page.locator("main")).to_be_hidden()
                expect(page.locator(".mail-card")).to_have_count(0)
                violations.extend(page.evaluate("violations"))
                context.close()
            remote = servers[1][2]
            context = browser.new_context()
            context.request.post(
                remote + "/api/v1/login", data={"username": "owner", "password": "fixture-password-123"}
            )
            page = context.new_page()
            remote_paths = []
            page.on("request", lambda r: remote_paths.append(r.url))
            page.goto(remote + "/board/mail.html")
            expect(page.locator(".mail-status-text")).to_contain_text("远程看板不展示邮件原文")
            assert not any("/api/v1/mail" in u for u in remote_paths)
            context.close()
            browser.close()
        assert not errors, errors
        assert not violations, violations
        assert not writes, writes
        assert all(u.startswith(servers[0][2] + "/") for u in requests), requests
        assert core.get_mail(doc["key"])["body"] == doc["body"]
        print(
            "PASS: real HTTP mail desktop/mobile; server pagination/search/date/review filters; full literal original and multi-candidate evidence; exact pending workbench link; filter-preserving return; refresh/query failures, latest-response-wins, 404/auth/remote; zero writes/external/CSP/JS errors."
        )
    finally:
        for server, thread, url in servers:
            server.should_exit = True
            thread.join(5)
