"""Mail phase-one browser regression; disposable data only."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database
from academic_os.security import Auth
from browser_check import serve


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir())
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aos-mail-browser-") as folder:
        settings = Settings(data_dir=Path(folder), secure_cookies=False, timezone="Asia/Taipei")
        core = Core(Database(settings.database_path), settings)
        Auth(core.db, settings).set_password("mail-owner", "mail-test-password")
        docs = [{"key": f"school:{i:03}", "account": "school", "subject": f"课程通知 {i:03}",
                 "received_at": "2026-10-13T12:00:00Z", "body": "isolated test"} for i in range(120)]
        docs.extend([
            {"key": "school:old", "account": "school", "subject": "旧邮件目标",
             "received_at": "2026-01-01T00:00:00Z", "body": "needle <script>window.XSS=true</script>"},
            {"key": "personal:end", "account": "personal", "subject": "边界邮件",
             "received_at": "2026-10-13T15:59:59.999Z", "body": "personal test"},
            {"key": "school:unknown", "account": "school", "subject": "无日期记录",
             "received_at": "", "body": "unknown date"},
        ])
        with core.db.transaction() as db:
            for doc in docs:
                doc["from"] = "sender@example.test"
                db.execute("INSERT INTO mail VALUES(?,?,?,?)", (doc["key"], doc["account"], json.dumps(doc, ensure_ascii=False), doc["received_at"]))
        server, thread, url = serve(core)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url)
                page.wait_for_load_state("networkidle")
                page.get_by_label("用户名", exact=True).fill("mail-owner")
                page.get_by_label("密码", exact=True).fill("mail-test-password")
                page.get_by_role("button", name="登录工作台").click()
                nav = page.get_by_role("navigation", name="主导航").get_by_role("button", name="邮件收件箱", exact=False)
                expect(nav).to_be_visible()
                assert nav.locator("xpath=ancestor::details").count() == 0
                nav.click()
                expect(page.get_by_text("共 123 封 · 1–50", exact=True)).to_be_visible()
                page.get_by_role("button", name="下一页", exact=True).click()
                expect(page.get_by_text("共 123 封 · 51–100", exact=True)).to_be_visible()
                page.get_by_role("button", name="下一页", exact=True).click()
                expect(page.get_by_text("共 123 封 · 101–123", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="下一页", exact=True)).to_be_disabled()
                page.get_by_label("搜索邮件", exact=True).fill("needle")
                page.get_by_role("button", name="查询邮件", exact=True).click()
                expect(page.get_by_text("共 1 封 · 1–1", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="旧邮件目标", exact=True)).to_be_visible()
                assert page.get_by_text(docs[-3]["body"], exact=True).count() == 0
                page.get_by_role("button", name="查看原文与日期", exact=True).click()
                expect(page.get_by_role("dialog").get_by_text(docs[-3]["body"], exact=True)).to_be_visible()
                assert page.evaluate("window.XSS") is None
                page.get_by_role("button", name="✕", exact=True).click()
                page.reload()
                page.get_by_role("button", name="邮件收件箱", exact=False).click()
                expect(page.get_by_label("搜索邮件", exact=True)).to_have_value("needle")
                expect(page.get_by_text("共 1 封 · 1–1", exact=True)).to_be_visible()
                page.get_by_label("搜索邮件", exact=True).fill("nothing-matches")
                page.get_by_role("button", name="查询邮件", exact=True).click()
                expect(page.get_by_text("共 0 封 · 0–0", exact=True)).to_be_visible()
                expect(page.get_by_text("没有匹配的邮件", exact=True)).to_be_visible()
                page.get_by_role("button", name="清空筛选", exact=True).click()
                expect(page.get_by_text("共 123 封 · 1–50", exact=True)).to_be_visible()
                page.get_by_label("邮件账号", exact=True).select_option("personal")
                page.get_by_label("邮件收件日期", exact=True).select_option("custom")
                page.get_by_label("邮件开始日期", exact=True).fill("2026-10-13")
                page.get_by_label("邮件结束日期", exact=True).fill("2026-10-13")
                page.get_by_role("button", name="查询邮件", exact=True).click()
                expect(page.get_by_text("共 1 封 · 1–1", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="边界邮件", exact=True)).to_be_visible()
                page.screenshot(path=str(output / "mail-desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
                page.screenshot(path=str(output / "mail-mobile.png"), full_page=True)
                page.route("**/api/v1/mail/search?*", lambda route: route.abort())
                page.get_by_role("button", name="查询邮件", exact=True).click()
                expect(page.get_by_role("button", name="重试查询", exact=True)).to_be_visible()
                page.unroute("**/api/v1/mail/search?*")
                page.get_by_role("button", name="重试查询", exact=True).click()
                expect(page.get_by_text("共 1 封 · 1–1", exact=True)).to_be_visible()
                page.get_by_role("button", name="清空筛选", exact=True).click()
                expect(page.get_by_text("共 123 封 · 1–50", exact=True)).to_be_visible()
                page.get_by_label("邮件收件日期", exact=True).select_option("undated")
                page.get_by_role("button", name="查询邮件", exact=True).click()
                expect(page.get_by_role("button", name="无日期记录", exact=True)).to_be_visible()
                expect(page.get_by_text("共 1 封 · 1–1", exact=True)).to_be_visible()
                assert not errors, errors
                browser.close()
                print("PASS: primary mail nav, 123-mail pagination, full-store body search, lazy plain text, persisted filters, empty/reset, account/date boundary, mobile layout, failure/retry, undated.")
        finally:
            server.should_exit = True
            thread.join(10)


if __name__ == "__main__":
    main()
