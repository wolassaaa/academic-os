"""Completion/trust/deep-link acceptance on disposable real HTTP services."""

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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from academic_os.api import create_app
from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database
from academic_os.security import Auth

p = argparse.ArgumentParser()
p.add_argument("--screenshots", type=Path, required=True)
args = p.parse_args()
args.screenshots.mkdir(parents=True, exist_ok=True)


def serve(core, remote=False):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(core, remote=remote), host="127.0.0.1", port=port, log_level="error", access_log=False
        )
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
    raise AssertionError("test server did not start")


with tempfile.TemporaryDirectory(prefix="board-focus-") as temp:
    core = Core(Database(Path(temp) / "state.db"), Settings(data_dir=Path(temp), secure_cookies=False))
    Auth(core.db, core.settings).set_password("owner", "fixture-password-123")
    servers = [serve(core), serve(core, True)]
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for width, height in [(1440, 1000), (390, 844)]:
                item = core.create_item(
                    {"title": "Overdue " + str(width), "due_at": "2020-01-01T12:00:00Z"}, "test", str(width)
                )
                later = core.create_item({"title": "Undated " + str(width)}, "test", str(width) + "later")
                context = browser.new_context(
                    viewport={"width": width, "height": height}, reduced_motion="reduce"
                )
                url = servers[0][2]
                context.request.post(
                    url + "/api/v1/login", data={"username": "owner", "password": "fixture-password-123"}
                )
                page = context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.add_init_script(
                    "window.violations=[];document.addEventListener('securitypolicyviolation',e=>violations.push(e.violatedDirective))"
                )

                def ready():
                    page.wait_for_function(
                        "()=>document.body.dataset.serviceState==='ready' && !globalThis.AcademicServiceMutationBusy && globalThis.AcademicServiceView?.state==='ready' && globalThis.AcademicFocus && globalThis.AcademicBoardPreview"
                    )

                page.goto(url + "/board/")
                ready()
                page.get_by_role("button", name="移出重点：" + item["title"], exact=True).last.click()
                expect(page.locator(".focus-feedback")).to_contain_text("已移回待办池")
                page.wait_for_function("!AcademicServiceMutationBusy")
                ready()
                expect(page.locator('.cards [data-item-id="' + item["id"] + '"]')).to_have_count(0)
                expect(page.locator('.pool-list [data-item-id="' + item["id"] + '"]')).to_contain_text("逾期")
                expect(page.locator(".focus-urgency")).to_contain_text("已逾期")
                page.reload()
                ready()
                expect(page.locator('.cards [data-item-id="' + item["id"] + '"]')).to_have_count(0)
                page.get_by_label("时间范围", exact=True).select_option("custom")
                page.get_by_label("开始日期", exact=True).fill("2019-12-01")
                page.get_by_label("结束日期", exact=True).fill("2020-02-01")
                page.get_by_role("button", name="应用时段", exact=True).click()
                expect(page.locator('.cards [data-item-id="' + item["id"] + '"]')).to_have_count(0)
                page.get_by_role("button", name="清除筛选", exact=True).click()
                page.get_by_role("button", name="设为重点：" + later["title"], exact=True).click()
                expect(page.locator(".focus-feedback")).to_contain_text("已设为我的重点")
                page.wait_for_function("!AcademicServiceMutationBusy")
                expect(page.locator('.cards [data-item-id="' + later["id"] + '"]')).to_contain_text(
                    "我的重点"
                )
                # Completion and focus do not share a business version.
                assert core.get_item(item["id"]) == item
                assert core.get_item(later["id"]) == later
                page.screenshot(path=str(args.screenshots / ("focus-" + str(width) + ".png")), full_page=True)
                page.goto(url + "/board/task.html?item=" + item["id"])
                ready()
                page.get_by_role("button", name="恢复自动推荐：" + item["title"], exact=True).click()
                expect(page.locator(".focus-feedback")).to_contain_text("已恢复自动推荐")
                page.wait_for_function("!AcademicServiceMutationBusy")
                # Another tab changes the preference revision, so stale intent conflicts.
                session = context.request.get(url + "/api/v1/session").json()
                prefs = context.request.get(url + "/api/v1/board-focus").json()
                headers = {"X-CSRF-Token": session["csrf_token"], "Idempotency-Key": "external-" + str(width)}
                assert (
                    context.request.put(
                        url + "/api/v1/board-focus",
                        data={"item_id": later["id"], "mode": "pool", "expected_revision": prefs["revision"]},
                        headers=headers,
                    ).status
                    == 200
                )
                page.get_by_role("button", name="移出重点：" + item["title"], exact=True).last.click()
                expect(page.locator(".focus-feedback")).to_contain_text("其他页面更新")
                page.get_by_role("button", name="刷新后重新选择", exact=True).click()
                ready()
                # Commit but lose receipt, then replay exactly the same request.
                calls = []

                def lose(route):
                    calls.append((route.request.headers["idempotency-key"], route.request.post_data))
                    assert route.fetch().status == 200
                    route.abort("failed")

                page.route("**/api/v1/board-focus", lose)
                page.get_by_role("button", name="移出重点：" + item["title"], exact=True).last.click()
                expect(page.locator(".focus-feedback")).to_contain_text("结果暂未确认")
                page.unroute("**/api/v1/board-focus")
                page.on(
                    "request",
                    lambda r: (
                        calls.append((r.headers.get("idempotency-key"), r.post_data))
                        if r.method == "PUT"
                        else None
                    ),
                )
                page.get_by_role("button", name="核实并重试", exact=True).click()
                expect(page.locator(".focus-feedback")).to_contain_text("已移回待办池")
                page.wait_for_function("!AcademicServiceMutationBusy")
                assert len(calls) == 2 and calls[0] == calls[1]
                page.goto(url + "/board/")
                ready()

                # Optional preference outage preserves facts but disables focus writes.
                def outage(route):
                    route.fulfill(status=503, json={})

                page.route("**/api/v1/board-focus", outage)
                page.get_by_role("button", name="刷新", exact=True).click()
                expect(page.locator(".service-status")).to_contain_text("重点偏好未读到")
                assert page.locator("[data-focus]:enabled").count() == 0
                assert page.locator(".pool-list .task-row").count() > 0
                expect(page.locator('.cards [data-item-id="' + item["id"] + '" ]')).to_have_count(0)
                assert not page.evaluate("document.documentElement.scrollWidth>innerWidth")
                assert page.evaluate("violations") == []
                context.close()
            remote = servers[1][2]
            context = browser.new_context()
            context.request.post(
                remote + "/api/v1/login", data={"username": "owner", "password": "fixture-password-123"}
            )
            page = context.new_page()
            requests = []
            page.on("request", lambda r: requests.append(r.url))
            page.goto(remote + "/board/")
            page.wait_for_function("()=>globalThis.AcademicServiceView?.state==='ready'")
            assert page.locator("[data-focus]:enabled").count() == 0
            assert not any("/api/v1/board-focus" in u for u in requests)
            assert context.request.put(remote + "/api/v1/board-focus", data={}).status == 404
            context.close()
            browser.close()
        assert not errors, errors
        print(
            "PASS: focus persists on desktop/mobile; undated pin, pool overrides date range, overdue warning, reset, conflict, same-key lost-response retry, optional outage and remote read-only; business facts unchanged; no CSP/JS errors or overflow."
        )
    finally:
        for server, thread, url in servers:
            server.should_exit = True
            thread.join(5)
