"""Real browser mail triage check; no live data, credentials or remote providers."""
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
    with tempfile.TemporaryDirectory(prefix='aos-triage-browser-') as folder:
        core = Core(Database(Path(folder) / 'state.sqlite3'), Settings(data_dir=Path(folder), secure_cookies=False))
        Auth(core.db, core.settings).set_password('owner', 'triage-password')
        due = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        doc = {'id': 'm1', 'key': 'school:m1', 'account': 'school', 'subject': '多日期学校邮件',
               'from': 'school@example.test', 'body': '<script>window.XSS=true</script> 原始学校邮件',
               'received_at': due, 'date_nodes': [{'date': due[:10], 'role': 'registration'}, {'date': due[:10], 'role': 'deadline'}]}
        with core.db.transaction() as db:
            db.execute('INSERT INTO mail VALUES(?,?,?,?)', (doc['key'], 'school', dumps(doc), due))
        items = core.ingest('gmail:school', [
            {'source_id': 'm1:registration:date', 'title': '报名候选', 'due_at': due, 'review_state': 'needs_analysis', 'evidence_text': '报名日期原文'},
            {'source_id': 'm1:deadline:date', 'title': '提交候选', 'due_at': None, 'review_state': 'needs_analysis', 'evidence_text': '提交日期原文'}])
        server, thread, url = serve(core)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url)
                page.get_by_label('用户名', exact=True).fill('owner')
                page.get_by_label('密码', exact=True).fill('triage-password')
                page.get_by_role('button', name='登录工作台', exact=True).click()
                page.get_by_role('button', name='作业与截止', exact=False).click()
                expect(page.get_by_role('button', name='报名候选', exact=True)).to_have_count(0)
                page.get_by_role('button', name='邮件收件箱', exact=False).click()
                page.get_by_label('邮件核对状态', exact=True).select_option('pending')
                page.get_by_role('button', name='查询邮件', exact=True).click()
                expect(page.get_by_text('待核对 2 项', exact=True)).to_be_visible()
                page.get_by_role('button', name='查看原文与日期', exact=True).click()
                dialog = page.get_by_role('dialog')
                expect(dialog.get_by_text(doc['body'], exact=True)).to_be_visible()
                assert page.evaluate('window.XSS') is None
                cards = dialog.locator('.mail-candidate')
                first = cards.filter(has_text='报名日期原文')
                first.get_by_label('候选标题', exact=True).fill('核对后的邮件待办')
                first.get_by_role('button', name='确认成待办', exact=True).click()
                expect(dialog.get_by_text('邮件待办', exact=True)).to_be_visible()
                second = dialog.locator('.mail-candidate').filter(has_text='提交日期原文')
                second.get_by_role('button', name='忽略候选', exact=True).click()
                expect(dialog.get_by_text('已忽略', exact=True)).to_be_visible()
                page.get_by_role('button', name='✕', exact=True).click()
                expect(page.get_by_text('共 0 封 · 0–0', exact=True)).to_be_visible()
                page.get_by_label('邮件核对状态', exact=True).select_option('ignored')
                page.get_by_role('button', name='查询邮件', exact=True).click()
                expect(page.get_by_text('已忽略 1 项', exact=True)).to_be_visible()
                page.get_by_role('button', name='查看原文与日期', exact=True).click()
                dialog.locator('.mail-candidate').filter(has_text='提交日期原文').get_by_role('button', name='恢复待核对', exact=True).click()
                expect(dialog.get_by_text('待核对', exact=True)).to_be_visible()
                page.get_by_role('button', name='✕', exact=True).click()
                page.get_by_role('button', name='作业与截止', exact=False).click()
                expect(page.get_by_role('button', name='核对后的邮件待办', exact=True)).to_be_visible()
                page.get_by_role('button', name='查看关联邮件', exact=True).click()
                expect(dialog.get_by_text(doc['body'], exact=True)).to_be_visible()
                second = dialog.locator('.mail-candidate').filter(has_text='提交日期原文')
                second.get_by_role('button', name='确认成待办', exact=True).click()
                expect(dialog.get_by_text('邮件待办', exact=True)).to_have_count(2)
                assert core.get_item(items[1]['id'])['source_due_at'] is None
                page.screenshot(path=str(output / 'triage-desktop.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
                page.screenshot(path=str(output / 'triage-mobile.png'), full_page=True)
                page.get_by_role('button', name='✕', exact=True).click()
                # A candidate conflict is resolvable without admitting it to formal lists.
                conflict_item = core.ingest('gmail:school', [{'source_id': 'm1:conflict', 'title': '冲突候选', 'course': 'COMP', 'due_at': due, 'review_state': 'needs_analysis'}])[0]
                core.ingest('gmail:personal', [{'source_id': 'p1:conflict', 'title': '冲突候选', 'course': 'COMP', 'due_at': (datetime.now(timezone.utc)+timedelta(days=5)).isoformat(), 'review_state': 'needs_analysis'}])
                page.get_by_role('button', name='↻ 刷新', exact=True).click()
                page.get_by_role('button', name='运行记录', exact=False).click()
                expect(page.get_by_role('button', name='保留本地', exact=True)).to_be_enabled()
                page.get_by_role('button', name='保留本地', exact=True).click()
                page.get_by_role('button', name='确认处理', exact=True).click()
                expect(dialog).not_to_be_visible()
                assert not core.list_conflicts()
                assert conflict_item['id'] not in {i['id'] for i in core.list_items(view='formal')}
                page.reload()
                page.get_by_role('button', name='作业与截止', exact=False).click()
                expect(page.get_by_role('button', name='核对后的邮件待办', exact=True)).to_be_visible()
                assert not errors, errors
                browser.close()
            print('PASS: mail filtering, two independent candidates, confirm/edit, ignore/restore, no-date preservation, bidirectional links, restart, desktop/mobile, plain text.')
        finally:
            server.should_exit = True
            thread.join(10)


if __name__ == '__main__':
    main()
