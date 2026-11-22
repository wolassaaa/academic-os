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


with tempfile.TemporaryDirectory(prefix="board-completion-") as temp:
    core = Core(Database(Path(temp) / "state.sqlite3"), Settings(data_dir=Path(temp), secure_cookies=False))
    Auth(core.db, core.settings).set_password("owner", "a-strong-password-123")
    servers = [serve(core), serve(core, True)]
    errors = []
    violations = []
    writes = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            for width, height, label in [(1440, 1000, "desktop"), (390, 844, "mobile")]:
                items = {}
                for key in ["normal", "conflict", "uncertain", "refresh-failed", "forbidden", "expired"]:
                    item = core.create_item(
                        {
                            "title": label + " " + key,
                            "course": "TEST101",
                            "kind": "assignment",
                            "priority": 5,
                            "due_at": "2026-11-20T12:00:00+08:00",
                        },
                        "test",
                        label + key,
                    )
                    with core.db.transaction() as db:
                        item["evidence"] = [
                            {
                                "source": "moodle:fixture",
                                "evidence_text": "fixture evidence <script>not executable</script>",
                                "due_at": item["source_due_at"],
                            }
                        ]
                        if key == "conflict":
                            item["review_state"] = "conflict"
                        core._put(db, item)
                    items[key] = item
                context = browser.new_context(viewport={"width": width, "height": height})
                context.add_init_script(
                    "window.cspViolations=[];document.addEventListener('securitypolicyviolation',e=>cspViolations.push(e.violatedDirective));"
                )
                url = servers[0][2]
                context.request.post(
                    url + "/api/v1/login", data={"username": "owner", "password": "a-strong-password-123"}
                )
                page = context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on(
                    "request",
                    lambda r: (
                        writes.append((r.method, r.url, r.headers, r.post_data))
                        if r.method != "GET"
                        else None
                    ),
                )

                def ready():
                    page.wait_for_function(
                        "()=>globalThis.AcademicServiceView?.state==='ready' && globalThis.AcademicBoardPreview"
                    )
                    page.wait_for_timeout(450)

                def detail(item):
                    page.goto(url + "/board/task.html?item=" + item["id"])
                    ready()

                def open_dialog(item):
                    page.get_by_role("button", name="标记完成：" + item["title"], exact=True).click()
                    expect(page.locator(".completion-dialog")).to_be_visible()
                    expect(page.get_by_role("button", name="取消", exact=True)).to_be_focused()

                item = items["normal"]
                detail(item)
                expect(page.locator(".detail-panel")).to_contain_text(
                    "fixture evidence <script>not executable</script>"
                )
                assert page.locator(".detail-panel script").count() == 0
                open_dialog(item)
                expect(page.locator(".completion-description")).to_contain_text("不会替你向学校提交")
                expect(page.locator(".completion-description")).to_contain_text("日历已同步")
                page.keyboard.press("Escape")
                expect(page.locator(".completion-dialog")).not_to_be_visible()
                assert core.get_item(item["id"])["version"] == item["version"]
                open_dialog(item)
                page.wait_for_timeout(250)
                page.screenshot(path=str(args.screenshots / ("confirm-" + label + ".png")), full_page=True)
                page.get_by_role("button", name="确认完成", exact=True).click()
                expect(page.locator(".completion-feedback")).to_contain_text("已完成")
                expect(page.locator(".detail-panel")).to_contain_text("已完成")
                result = core.get_item(item["id"])
                assert result["status"] == "completed" and result["version"] == item["version"] + 1
                assert (
                    result["source_due_at"] == item["source_due_at"]
                    and result["evidence"] == item["evidence"]
                )
                page.reload()
                ready()
                expect(page.locator(".detail-panel")).to_contain_text("已完成")
                expect(
                    page.get_by_role("button", name="已关闭：" + item["title"], exact=True)
                ).to_be_disabled()
                page.screenshot(path=str(args.screenshots / ("completed-" + label + ".png")), full_page=True)
                # Exact deep link includes closed items, without auto-editing or saving.
                page.get_by_role("link", name="在工作台定位此事项", exact=True).click()
                expect(page.locator("#item-table")).to_contain_text(item["title"])
                expect(page.locator("#item-table tbody tr")).to_have_count(1)
                expect(page.locator("#item-table")).to_contain_text("已完成")
                page.get_by_role("button", name="查看全部事项", exact=True).click()
                assert page.locator("#item-table tbody tr").count() > 1
                # A version conflict must never silently retry at the new version.
                item = items["conflict"]
                detail(item)
                expect(page.locator(".detail-panel")).to_contain_text("截止有冲突")
                open_dialog(item)
                changed = core.update_item(
                    item["id"], {"title": item["title"] + " updated"}, item["version"], "external"
                )
                page.get_by_role("button", name="确认完成", exact=True).click()
                expect(page.locator(".completion-error")).to_contain_text("事项已被其他操作更新")
                expect(page.get_by_role("button", name="确认完成", exact=True)).to_be_disabled()
                assert core.get_item(item["id"])["status"] == "pending"
                page.screenshot(path=str(args.screenshots / ("conflict-" + label + ".png")), full_page=True)
                page.get_by_role("button", name="刷新后重新核对", exact=True).click()
                ready()
                expect(page.locator(".detail-panel")).to_contain_text(changed["title"])
                # Simulate a committed write whose response is lost, then replay the same key.
                item = items["uncertain"]
                detail(item)
                payloads = []

                def lose_response(route):
                    payloads.append((route.request.headers.get("idempotency-key"), route.request.post_data))
                    response = route.fetch()
                    assert response.status == 200
                    route.abort("failed")

                path = "**/api/v1/items/" + item["id"]
                page.route(path, lose_response)
                open_dialog(item)
                page.get_by_role("button", name="确认完成", exact=True).click()
                expect(page.locator(".completion-error")).to_contain_text("结果暂未确认")
                assert core.get_item(item["id"])["version"] == item["version"] + 1
                page.unroute(path)
                page.get_by_role("button", name="核实并重试", exact=True).click()
                expect(page.locator(".completion-feedback")).to_contain_text("已完成")
                matching = [r for r in writes if r[1].endswith("/items/" + item["id"])]
                assert (
                    len(matching) == 2
                    and matching[0][2]["idempotency-key"] == matching[1][2]["idempotency-key"]
                )
                assert matching[0][3] == matching[1][3]
                with core.db.connect() as db:
                    assert (
                        db.execute(
                            "SELECT COUNT(*) FROM audit WHERE entity_id=? AND action='item.update'",
                            (item["id"],),
                        ).fetchone()[0]
                        == 1
                    )
                # A failed list refresh cannot erase an already confirmed receipt.
                item = items["refresh-failed"]
                detail(item)
                page.route("**/api/v1/items?*", lambda route: route.abort("failed"))
                open_dialog(item)
                page.get_by_role("button", name="确认完成", exact=True).click()
                expect(page.locator(".completion-feedback")).to_contain_text("完成已确认；列表刷新失败")
                assert core.get_item(item["id"])["status"] == "completed"
                assert page.locator("[data-completion]:enabled").count() == 0
                expect(page.locator(".detail-panel")).to_contain_text("已关闭")
                page.unroute("**/api/v1/items?*")
                # Permission errors and expired sessions do not show success.
                for key, status in [("forbidden", 403), ("expired", 401)]:
                    item = items[key]
                    detail(item)
                    path = "**/api/v1/items/" + item["id"]

                    def block(route):
                        route.fulfill(status=status, json={"detail": "fixture"})

                    page.route(path, block)
                    open_dialog(item)
                    page.get_by_role("button", name="确认完成", exact=True).click()
                    if status == 403:
                        expect(page.locator(".completion-error")).to_contain_text("没有完成权限")
                        page.keyboard.press("Escape")
                    else:
                        expect(page.locator("main.dashboard")).to_be_hidden()
                        expect(page.locator(".completion-feedback")).to_be_hidden()
                    assert core.get_item(item["id"])["status"] == "pending"
                    page.unroute(path)
                page.goto(url + "/board/sources.html")
                page.wait_for_function("()=>globalThis.AcademicServiceView?.state==='ready'")
                assert not page.evaluate("document.documentElement.scrollWidth>innerWidth")
                violations.extend(page.evaluate("cspViolations"))
                context.close()
            remote = servers[1][2]
            context = browser.new_context(reduced_motion="reduce")
            context.request.post(
                remote + "/api/v1/login", data={"username": "owner", "password": "a-strong-password-123"}
            )
            page = context.new_page()
            page.goto(remote + "/board/")
            page.wait_for_function(
                "()=>globalThis.AcademicServiceView?.state==='ready' && globalThis.AcademicBoardPreview"
            )
            assert page.locator("[data-completion]:enabled").count() == 0
            assert context.request.patch(remote + "/api/v1/items/nope", data={}).status == 404
            context.close()
            browser.close()
        assert not errors, errors
        assert not violations, violations
        assert all(method == "PATCH" and "/api/v1/items/" in url for method, url, headers, body in writes), (
            writes
        )
        print(
            "PASS: desktop/mobile completion-confirmation/reload/history; exact workbench links; conflict requires re-review; committed lost-response replay has one effect; 401/403/remote protections; literal evidence and zero CSP/JS errors."
        )
    finally:
        for server, thread, url in servers:
            server.should_exit = True
            thread.join(5)
