"""Real Chromium smoke check. Runs against disposable local and remote databases.

Run: .venv/Scripts/python.exe tests/browser_check.py
Requires Playwright and either its Chromium browser or installed Chrome. Never reads live data.
"""
from __future__ import annotations

import json
import re
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import uvicorn
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from academic_os.api import create_app
from academic_os.config import Settings
from academic_os.core import Core, utcnow
from academic_os.db import Database
from academic_os.notifications import Notifications


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve(core, remote=False):
    from academic_os.connections import Connections
    from test_connections import MemorySecrets
    port = free_port()
    connections = Connections(core, core.settings.data_dir / 'config.json', MemorySecrets())
    server = uvicorn.Server(uvicorn.Config(create_app(core, remote=remote, connections=connections), host="127.0.0.1", port=port, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            return server, thread, f"http://127.0.0.1:{port}"
        time.sleep(.05)
    raise RuntimeError("Temporary test server did not start")


def main():
    servers = []
    with tempfile.TemporaryDirectory(prefix="academic-os-browser-") as folder:
        settings = Settings(data_dir=Path(folder), secure_cookies=False)
        core = Core(Database(settings.database_path), settings)
        now = utcnow()
        mail = {"id": "browser-mail", "key": "test:mail", "account": "test", "subject": "浏览器核对邮件", "from": "school@example.test", "body": "<script>window.XSS=true</script> 原文只作纯文本显示", "received_at": now, "date_nodes": [{"text": "2026-10-20", "date": "2026-10-20"}], "analysis_status": "disabled"}
        with core.db.transaction() as db:
            db.execute("INSERT INTO mail VALUES(?,?,?,?)", ("test:mail", "test", json.dumps(mail), now))
            db.execute("INSERT INTO artifacts VALUES(?,?,?)", ("test:grades", "grades", json.dumps({"data": {"course": "测试课程", "grade": "A"}, "updated_at": now})))
        Notifications(core).queue("workbench", {"title": "浏览器检查提醒", "body": "真实数据库中的隔离测试记录。"}, "browser-notice")
        try:
            local, thread, url = serve(core)
            servers.append((local, thread))
            remote, thread, remote_url = serve(core, True)
            servers.append((remote, thread))
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch(headless=True)
                except PlaywrightError as error:
                    if "Executable doesn't exist" not in str(error):
                        raise
                    browser = p.chromium.launch(headless=True, channel="chrome")
                context = browser.new_context(viewport={"width": 1440, "height": 1100})
                page = context.new_page()
                errors = []
                local_requests = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: local_requests.append(request.url.split("/api/v1")[-1]) if "/api/v1" in request.url else None)
                page.goto(url)
                expect(page.get_by_role("heading", name="建立你的工作台")).to_be_visible()
                page.get_by_label("用户名", exact=True).fill("browser-owner")
                page.get_by_label("密码", exact=True).fill("eight888")
                page.get_by_label("再次输入密码", exact=True).fill("eight888")
                page.get_by_role("button", name="创建本地账号").click()
                expect(page.get_by_role("heading", name="账号与同步", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="＋ 登录 Google 账号")).to_be_disabled()
                expect(page.get_by_role("button", name="学校网页登录")).to_be_visible()
                page.get_by_text("高级核对工具", exact=True).click()
                expect(page.get_by_text("影子模式运行中", exact=False)).to_be_visible()
                page.get_by_role("button", name="作业与截止", exact=False).click()
                page.get_by_role("button", name="＋ 新建事项").click()
                page.get_by_label("事项标题", exact=True).fill("浏览器测试作业 <b>文本</b>")
                page.get_by_label("课程", exact=True).fill("测试课程")
                page.get_by_label("预计用时（分钟）").fill("60")
                due = datetime.now() + timedelta(days=4)
                page.get_by_label("来源截止 · 输入使用浏览器本地时区").fill(due.strftime("%Y-%m-%dT%H:%M"))
                page.get_by_role("button", name="保存事项", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(page.get_by_role("button", name="浏览器测试作业 <b>文本</b>", exact=True)).to_be_visible()
                page.get_by_role("button", name="编辑", exact=True).click()
                page.get_by_label("预计用时（分钟）").fill("75")
                page.get_by_role("button", name="保存事项", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(page.get_by_role("cell", name=re.compile("^75 分钟"))).to_be_visible()
                page.get_by_role("button", name="计划草稿", exact=False).first.click()
                page.get_by_role("button", name="生成计划草稿", exact=True).click()
                expect(page.get_by_role("button", name="核对并确认", exact=True)).to_be_visible()
                page.get_by_role("button", name="核对并确认", exact=True).click()
                expect(page.get_by_role("heading", name="确认学习计划", exact=True)).to_be_visible()
                page.get_by_role("button", name="确认这 1 项安排").click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(page.get_by_text("已确认", exact=True)).to_be_visible()
                assert core.list_items()[0]["scheduled_start"]
                page.get_by_role("button", name="作业与截止", exact=False).click()
                page.get_by_role("button", name="安排", exact=True).click()
                start = (datetime.now() + timedelta(days=1)).replace(hour=10, minute=0)
                end = start + timedelta(hours=1)
                page.get_by_label("开始时间 · 浏览器本地时区", exact=True).fill(start.strftime("%Y-%m-%dT%H:%M"))
                page.get_by_label("结束时间 · 浏览器本地时区", exact=True).fill(end.strftime("%Y-%m-%dT%H:%M"))
                page.get_by_role("button", name="创建安排草稿", exact=True).click()
                draft = page.locator(".draft").first
                draft.get_by_label("开始 · 浏览器本地时区", exact=True).fill((start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"))
                draft.get_by_label("结束 · 浏览器本地时区", exact=True).fill((end + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"))
                expect(draft.get_by_role("button", name="核对并确认", exact=True)).to_be_disabled()
                draft.get_by_role("button", name="将调整保存为新草稿", exact=True).click()
                expect(page.locator(".draft")).to_have_count(3)
                page.locator(".draft").first.get_by_role("button", name="核对并确认", exact=True).click()
                page.get_by_role("button", name="确认这 1 项安排").click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                item = core.list_items()[0]
                assert datetime.fromisoformat(item["scheduled_start"]).astimezone().hour == 12
                page.get_by_role("button", name="作业与截止", exact=False).click()
                page.get_by_role("button", name="编辑", exact=True).click()
                core.update_item(item["id"], {"priority": 3}, item["version"])
                page.get_by_role("button", name="保存事项", exact=True).click()
                expect(page.get_by_role("alert").filter(has_text="事项已被其他操作更新")).to_be_visible()
                page.get_by_role("button", name="✕", exact=True).click()
                page.get_by_role("button", name="↻ 刷新", exact=True).click()
                page.get_by_role("button", name="邮件收件箱", exact=False).click()
                assert not any(path.startswith("/mail/") for path in local_requests)
                expect(page.get_by_text("最近最多 100 封 · 正文按需加载", exact=True)).to_be_visible()
                assert page.get_by_text(mail["body"], exact=True).count() == 0
                page.get_by_role("button", name="查看原文与日期").click()
                expect(page.get_by_role("dialog").get_by_text(mail["body"], exact=True)).to_be_visible()
                assert "/mail/test%3Amail" in local_requests
                assert page.evaluate("window.XSS") is None
                expect(page.get_by_role("heading", name="提取的日期节点", exact=True)).to_be_visible()
                page.get_by_role("button", name="✕", exact=True).click()
                page.get_by_role("button", name="提醒中心", exact=False).click()
                page.get_by_role("button", name="稍后 1 小时").click()
                expect(page.get_by_text("稍后提醒", exact=True)).to_be_visible()
                page.get_by_role("button", name="确认收到", exact=True).click()
                expect(page.get_by_text("已收到", exact=True)).to_be_visible()
                page.get_by_role("button", name="运行记录", exact=False).click()
                expect(page.get_by_role("heading", name="课程资料与成绩档案")).to_be_visible()
                item = core.list_items()[0]
                with core.db.transaction() as db:
                    core._conflict(db, "source_date", item["id"], {"current": item["source_due_at"], "incoming": (datetime.now().astimezone() + timedelta(days=5)).isoformat(), "source": "browser-test", "source_id": "test"})
                page.get_by_role("button", name="↻ 刷新", exact=True).click()
                page.get_by_role("button", name="保留本地", exact=True).click()
                page.get_by_role("button", name="确认处理", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                assert not core.list_conflicts()
                page.get_by_role("button", name="同步来源", exact=True).click()
                expect(page.get_by_role("cell", name="sources_sync", exact=True)).to_be_visible()
                page.get_by_role("button", name="账号与同步", exact=False).click()
                expect(page.get_by_role("heading", name="Google 账号与日历")).to_be_visible()
                page.get_by_role("button", name="总览", exact=False).click()
                expect(page.locator(".toast")).to_have_count(0, timeout=15000)
                screenshot = Path(tempfile.gettempdir()) / "academic-os-desktop-check.png"
                page.screenshot(path=str(screenshot), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(Path(tempfile.gettempdir()) / "academic-os-mobile-check.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile viewport overflow"
                context.set_offline(True)
                expect(page.get_by_role("alert").filter(has_text="连接中断")).to_be_visible()
                context.set_offline(False)
                page.get_by_role("button", name="↻ 刷新", exact=True).click()
                expect(page.get_by_role("alert").filter(has_text="连接中断")).not_to_be_visible()
                remote_context = browser.new_context(viewport={"width": 390, "height": 844})
                remote_page = remote_context.new_page()
                requested = []
                remote_page.on("request", lambda request: requested.append(request.url.split("/api/v1")[-1]) if "/api/v1" in request.url else None)
                remote_page.goto(remote_url)
                remote_page.get_by_label("用户名", exact=True).fill("browser-owner")
                remote_page.get_by_label("密码", exact=True).fill("eight888")
                remote_page.get_by_role("button", name="登录工作台", exact=True).click()
                expect(remote_page.get_by_role("heading", name="把学业，安排得从容。")).to_be_visible()
                expect(remote_page.get_by_text("远程查看学业进展；修改请回到本地工作台。", exact=True)).to_be_visible()
                assert set(requested) <= {"/session", "/login", "/dashboard"}, requested
                assert remote_page.get_by_role("button", name="作业与截止", exact=False).count() == 0
                remote_page.get_by_role("button", name="退出", exact=True).click()
                expect(remote_page.get_by_role("heading", name="欢迎回来", exact=True)).to_be_visible()
                assert not errors, errors
                browser.close()
                print("PASS: setup/login, safe mail, item create/edit/version conflict, draft generation/adjustment/confirmation, notifications, conflict resolution, sources/config, mobile, offline recovery, remote read-only.")
                print(f"Screenshots: {screenshot}; {Path(tempfile.gettempdir()) / 'academic-os-mobile-check.png'}")
        finally:
            for server, thread in servers:
                server.should_exit = True
                thread.join(timeout=5)


if __name__ == "__main__":
    main()
