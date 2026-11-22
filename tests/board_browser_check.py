"""Real HTTP checks; isolated database, test-only credentials and no live accounts."""

from __future__ import annotations
import argparse
import asyncio
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
import uvicorn
from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from academic_os import api
from academic_os.config import Settings
from academic_os.core import Core, utcnow
from academic_os.db import Database
from academic_os.security import Auth

p = argparse.ArgumentParser()
p.add_argument("--screenshots", type=Path, required=True)
a = p.parse_args()
a.screenshots.mkdir(parents=True, exist_ok=True)
api.utcnow = lambda: "2026-10-15T04:00:00+00:00"


def serve(core, remote):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            api.create_app(core, remote=remote),
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
        )
    )

    def run_server():
        # Test-only selector loop avoids Windows Proactor keepalive-reset noise.
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(server.serve())

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            return server, thread, f"http://127.0.0.1:{port}"
        time.sleep(0.05)
    raise AssertionError("server did not start")


with tempfile.TemporaryDirectory(prefix="academic-board-test-") as temp:
    core = Core(
        Database(Path(temp) / "state.sqlite3"),
        Settings(data_dir=Path(temp), timezone="America/New_York", secure_cookies=False),
    )
    Auth(core.db, core.settings).set_password("owner", "a-strong-password-123")
    for i in range(14):
        core.create_item(
            {
                "title": f"测试任务 {i:02d}"
                + (" · 很长的任务标题，检查手机中多行文本是否会横向溢出或遮挡截止时间" if i == 0 else ""),
                "course": f"TEST{i % 3}101",
                "kind": "assignment",
                "due_at": f"2026-10-{15 + i % 3:02d}T14:00:00+00:00",
                "priority": 4 if i < 8 else 2,
            },
            "test",
            f"create-{i}",
        )
    undated = core.create_item({"title": "测试无截止任务", "course": "", "kind": "task"}, "test", "undated")
    boundary = core.create_item(
        {
            "title": "测试纽约午夜边界",
            "course": "TEST0101",
            "kind": "task",
            "due_at": "2026-10-16T03:30:00+00:00",
        },
        "test",
        "boundary",
    )
    mixed = core.create_item(
        {
            "title": "测试混合来源保护",
            "course": "TEST0101",
            "kind": "task",
            "due_at": "2026-10-15T14:00:00+00:00",
        },
        "test",
        "mixed",
    )
    with core.db.transaction() as db:
        mixed["manual_origin"] = False
        mixed["mail_review"] = {"state": "pending", "revision": 1}
        mixed["evidence"] = [
            {"source": "gmail:fixture", "text": "mail evidence"},
            {"source": "moodle:fixture", "text": "school evidence"},
        ]
        core._put(db, mixed)
        db.execute(
            "INSERT INTO sources VALUES(?,?,?,?,?,?)",
            ("moodle:fixture", "partial", utcnow(), utcnow(), "test failure", "{}"),
        )
    core.ingest(
        "gmail:fixture",
        [
            {
                "source_id": "candidate",
                "title": "测试未确认邮件应隐藏",
                "source_due_at": "2026-10-15T14:00:00+00:00",
                "evidence_text": "Only a candidate",
            }
        ],
    )
    original = json.dumps(core.list_items(), sort_keys=True)
    servers = [serve(core, remote) for remote in [False, True]]
    errors = []
    violations = []
    writes = []
    requests = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            for width, height, label in [(1440, 1000, "desktop"), (390, 844, "mobile")]:
                context = browser.new_context(viewport={"width": width, "height": height})
                context.add_init_script(
                    "window.cspViolations=[];document.addEventListener('securitypolicyviolation',e=>cspViolations.push(e.violatedDirective+':'+e.blockedURI));"
                )
                page = context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on(
                    "request",
                    lambda r: (
                        requests.append(r.url),
                        writes.append(r.method + " " + r.url) if r.method != "GET" else None,
                    ),
                )
                url = servers[0][2]

                def ready():
                    page.wait_for_function("()=>globalThis.AcademicServiceView?.state==='ready'")
                    page.wait_for_function("()=>globalThis.AcademicBoardPreview")
                    page.wait_for_timeout(650)

                page.goto(url + "/board/")
                page.wait_for_function("()=>globalThis.AcademicServiceView?.state==='expired'")
                expect(page.get_by_role("link", name="前往登录", exact=True)).to_be_visible()
                expect(page.locator("main")).to_be_hidden()
                assert "测试任务" not in page.locator("body").inner_text()
                assert (
                    context.request.post(
                        url + "/api/v1/login", data={"username": "owner", "password": "a-strong-password-123"}
                    ).status
                    == 200
                )
                # Recover from initial auth failure without a reload.
                page.get_by_role("button", name="刷新", exact=True).click()
                ready()
                assert page.evaluate("AcademicBoardPreview.mode") == "service"
                expect(page.locator(".cards .task-card")).to_have_count(6)
                page.evaluate("window.scrollTo(0,0)")
                page.screenshot(path=str(a.screenshots / ("index-" + label + ".png")), full_page=True)
                page.get_by_role("button", name=__import__("re").compile("展开其余")).click()
                assert page.locator(".cards .task-card").count() > 6
                assert "测试混合来源保护" in page.locator(".cards").inner_text()
                assert "测试未确认邮件应隐藏" not in page.locator("body").inner_text()
                assert "学期未设置" in page.locator(".time-context").inner_text()
                assert (
                    page.locator("[data-preview-menu]").count() == 0
                    and page.locator(".preview-action-bar").count() == 0
                )
                assert not page.evaluate("document.documentElement.scrollWidth>innerWidth")
                # Inclusive business-timezone filtering, including a UTC next-day instant.
                page.get_by_label("开始日期", exact=True).fill("2026-10-15")
                page.get_by_label("结束日期", exact=True).fill("2026-10-15")
                page.get_by_role("button", name="应用时段", exact=True).click()
                if page.locator(".expand-priorities").is_visible():
                    page.locator(".expand-priorities").click()
                assert "测试纽约午夜边界" in page.locator(".cards").inner_text()
                expect(page.locator(".pool-list")).to_contain_text("测试无截止任务")
                page.get_by_label("保留无截止任务", exact=True).uncheck()
                expect(page.locator(".pool-list .task-row")).to_have_count(0)
                page.get_by_label("开始日期", exact=True).fill("2026-10-17")
                page.get_by_role("button", name="应用时段", exact=True).click()
                expect(page.locator(".date-filter-error")).to_be_visible()
                page.get_by_role("button", name="清除筛选", exact=True).click()
                page.get_by_role("link", name="全部待办", exact=True).first.click()
                ready()
                assert page.locator(".all-task-list .task-row").count() == len(core.list_items(view="formal"))
                page.get_by_label("搜索任务或课程").fill("不存在的搜索")
                expect(page.locator(".empty-state")).to_contain_text("没有符合条件")
                page.get_by_role("button", name="清除筛选", exact=True).click()
                page.get_by_role("link", name="测试无截止任务", exact=True).click()
                ready()
                expect(page.locator(".detail-panel")).to_contain_text("小任务未提供")
                expect(page.locator(".detail-panel")).to_contain_text("待公布")
                assert page.locator('[aria-label="小任务完成进度"]').count() == 0
                page.screenshot(path=str(a.screenshots / ("task-" + label + ".png")), full_page=True)
                page.goto(url + "/board/settings.html")
                page.wait_for_function("()=>globalThis.AcademicServiceView?.state==='ready'")
                page.wait_for_timeout(650)
                for tab, panel in [
                    ("外观与课程", "courses"),
                    ("Hermes 与 AI", "hermes"),
                    ("数据与维护", "maintenance"),
                    ("连接来源", "connections"),
                ]:
                    page.locator(".settings-tabs").get_by_role("link", name=tab, exact=True).click()
                    expect(page.locator("#" + panel)).to_be_visible()
                expect(page.locator(".live-sources")).to_contain_text("部分读取失败")
                assert not page.evaluate("document.documentElement.scrollWidth>innerWidth")
                page.wait_for_timeout(650)
                page.evaluate("window.scrollTo(0,0)")
                page.screenshot(path=str(a.screenshots / ("settings-" + label + ".png")), full_page=True)
                # Refresh failures keep read items, then successful retry replaces facts.
                page.goto(url + "/board/")
                ready()
                count = page.evaluate("AcademicBoardPreview.itemCount")
                page.route(
                    "**/api/v1/items?view=formal", lambda r: r.fulfill(status=503, json={"error": "fixture"})
                )
                page.get_by_role("button", name="刷新", exact=True).click()
                page.wait_for_function("()=>globalThis.AcademicServiceView.state==='stale'")
                assert page.evaluate("AcademicBoardPreview.itemCount") == count
                expect(page.locator("main")).to_be_visible()
                expect(page.locator(".service-status")).to_contain_text("已保留上次读取")
                page.unroute("**/api/v1/items?view=formal")
                page.route("**/api/v1/health", lambda r: r.fulfill(status=503, json={}))
                page.get_by_role("button", name="刷新", exact=True).click()
                ready()
                expect(page.locator(".service-status")).to_contain_text("来源状态需关注")
                page.unroute("**/api/v1/health")
                page.route(
                    "**/api/v1/session",
                    lambda r: r.fulfill(json={"authenticated": False, "initialized": True}),
                )
                page.get_by_role("button", name="刷新", exact=True).click()
                page.wait_for_function("()=>globalThis.AcademicServiceView.state==='expired'")
                expect(page.locator("main")).to_be_hidden()
                assert "测试任务" not in page.locator("body").inner_text()
                page.unroute("**/api/v1/session")
                page.get_by_role("button", name="刷新", exact=True).click()
                ready()
                page.route("**/api/v1/items?view=formal", lambda r: r.fulfill(json=[]))
                page.get_by_role("button", name="刷新", exact=True).click()
                ready()
                expect(page.locator(".cards")).to_contain_text("还没有待办")
                page.unroute("**/api/v1/items?view=formal")
                page.get_by_role("button", name="刷新", exact=True).click()
                ready()
                assert page.evaluate("AcademicBoardPreview.itemCount") == count
                assert json.dumps(core.list_items(), sort_keys=True) == original
                core.create_item(
                    {"title": "服务新增事项 " + label, "course": "", "kind": "task"},
                    "test",
                    "external-" + label,
                )
                original = json.dumps(core.list_items(), sort_keys=True)
                page.get_by_role("button", name="刷新", exact=True).click()
                ready()
                assert page.evaluate("AcademicBoardPreview.itemCount") == count + 1
                violations.extend(page.evaluate("cspViolations"))
                context.close()
            # All five views, including compact desktop/tablet layouts.
            for width, height, label in [
                (1440, 1000, "desktop"),
                (390, 844, "mobile"),
                (768, 1024, "tablet"),
                (1024, 900, "small-desktop"),
            ]:
                context = browser.new_context(viewport={"width": width, "height": height})
                context.add_init_script(
                    "window.cspViolations=[];document.addEventListener('securitypolicyviolation',e=>cspViolations.push(e.violatedDirective));"
                )
                page = context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on(
                    "request", lambda r: writes.append(r.method + " " + r.url) if r.method != "GET" else None
                )
                context.request.post(
                    servers[0][2] + "/api/v1/login",
                    data={"username": "owner", "password": "a-strong-password-123"},
                )
                for name in ["index", "tasks", "task", "settings", "sources"]:
                    suffix = "?item=" + undated["id"] if name == "task" else ""
                    page.goto(servers[0][2] + "/board/" + name + ".html" + suffix)
                    page.wait_for_function("()=>globalThis.AcademicServiceView?.state==='ready'")
                    page.wait_for_timeout(700)
                    assert not page.evaluate("document.documentElement.scrollWidth>innerWidth"), (name, label)
                    assert page.evaluate("document.getAnimations().length") == 0
                    violations.extend(page.evaluate("cspViolations"))
                    if label in ["desktop", "mobile"]:
                        page.screenshot(
                            path=str(a.screenshots / (name + "-" + label + ".png")), full_page=True
                        )
                context.close()
            # Remote requests never probe local config, items or health interfaces.
            context = browser.new_context(reduced_motion="reduce")
            page = context.new_page()
            remote = servers[1][2]
            remote_paths = []
            page.on("request", lambda r: remote_paths.append(r.url))
            page.on("pageerror", lambda e: errors.append(str(e)))
            assert (
                context.request.post(
                    remote + "/api/v1/login", data={"username": "owner", "password": "a-strong-password-123"}
                ).status
                == 200
            )
            page.goto(remote + "/board/")
            page.wait_for_function(
                "()=>globalThis.AcademicServiceView?.state==='ready' && globalThis.AcademicBoardPreview"
            )
            expect(page.locator(".service-status")).to_contain_text("远程只读")
            assert page.evaluate("AcademicMotion.enabled") is False
            assert not any(
                "/api/v1/items" in x or "/api/v1/health" in x or "/api/v1/config" in x for x in remote_paths
            )
            assert context.request.post(remote + "/api/v1/items", data={}).status == 404
            before = len(remote_paths)
            page.wait_for_timeout(1500)
            assert len(remote_paths) == before, "no polling"
            context.close()
            browser.close()
        assert not errors, errors
        assert not violations, violations
        assert not writes, writes
        assert json.dumps(core.list_items(), sort_keys=True) == original, (
            "read-only UI must not mutate item facts"
        )
        print(
            "PASS: real HTTP; desktop/mobile; local/remote; auth recovery/expiry; formal-only/mixed sources; timezone boundary/date filters; unknown dates/subtasks/semester; partial health; refresh preserves facts; zero UI writes/polling/CSP violations/errors."
        )
    finally:
        for server, thread, url in servers:
            server.should_exit = True
            thread.join(5)
