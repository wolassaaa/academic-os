import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database, dumps
from academic_os.security import Auth
from browser_check import serve


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir())
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        core = Core(Database(Path(folder)/'state.sqlite3'), Settings(data_dir=Path(folder), secure_cookies=False))
        Auth(core.db, core.settings).set_password('owner', 'browser-password')
        core.ingest('moodle', [{'source_id': 'moodle:activity:assign:1', 'title': '中文作业一 - Group A', 'course': 'COMP1000', 'course_id': 7, 'kind': 'assignment', 'submission_status': 'unknown', 'due_at': (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}])
        doc = {'id': 'm1', 'key': 'school:m1', 'account': 'school', 'subject': '邮件候选', 'body': '原邮件', 'from': 'school@example.test', 'date_nodes': []}
        with core.db.transaction() as db:
            db.execute('INSERT INTO mail VALUES(?,?,?,?)', (doc['key'], 'school', dumps(doc), '2026-10-14T00:00:00Z'))
        core.ingest('gmail:school', [{'source_id': 'm1:deadline', 'title': '待核对报名', 'review_state': 'needs_analysis'}])
        core.source_health('moodle', False, 'moodle_incomplete_snapshot', {'records': 1})
        server, thread, url = serve(core)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(url)
                page.get_by_label('用户名', exact=True).fill('owner')
                page.get_by_label('密码', exact=True).fill('browser-password')
                page.get_by_role('button', name='登录工作台', exact=True).click()
                page.get_by_text('高级核对工具', exact=True).click()
                page.get_by_role('button', name='总览', exact=False).click()
                page.get_by_role('button', name='中文作业一 - Group A', exact=True).click()
                expect(page.get_by_role('dialog')).to_be_visible()
                page.get_by_role('button', name='✕', exact=True).click()
                page.get_by_role('button', name='作业与截止', exact=False).click()
                page.get_by_label('事项分类', exact=True).select_option('assignment')
                expect(page.get_by_role('button', name='中文作业一 - Group A', exact=True)).to_be_visible()
                expect(page.get_by_text('自动 · Group A', exact=True)).to_be_visible()
                expect(page.get_by_text('学校作业同步未完整成功', exact=False)).to_be_visible()
                page.get_by_role('button', name='待核对邮件', exact=False).click()
                expect(page.get_by_role('button', name='待核对报名', exact=True)).to_be_visible()
                expect(page.get_by_role('button', name='中文作业一 - Group A', exact=True)).to_have_count(0)
                page.get_by_role('button', name='核对邮件', exact=True).click()
                expect(page.get_by_role('dialog').get_by_text('原邮件', exact=True)).to_be_visible()
                page.get_by_role('button', name='✕', exact=True).click()
                page.set_viewport_size({'width': 390, 'height': 844})
                page.get_by_role('button', name='正式事项', exact=False).click()
                expect(page.get_by_role('button', name='中文作业一 - Group A', exact=True)).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
                page.screenshot(path=str(output/'classification-mobile.png'), full_page=True)
                assert not errors, errors
                browser.close()
            print('PASS: automatic course/type/group labels, Chinese undated coursework, category filtering, pending-mail separation, sync failure visibility, mobile.')
        finally:
            server.should_exit = True
            thread.join(10)


if __name__ == '__main__':
    main()

