"""Disposable desktop/mobile tag and original-text digest acceptance."""
import sys
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database, dumps
from academic_os.security import Auth
from browser_check import serve


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir())
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='aos-tags-browser-') as folder:
        core = Core(Database(Path(folder) / 'state.sqlite3'), Settings(data_dir=Path(folder), secure_cookies=False))
        Auth(core.db, core.settings).set_password('owner', 'browser-password')
        item = core.create_item({'title': '课程作业', 'course': 'COMP'})
        doc = {'id': 'm1', 'key': 'school:m1', 'account': 'school', 'subject': '课程邮件', 'from': 'school@example.test',
               'body': '<script>window.XSS=true</script> 作业要求：阅读第一章并完成练习。' + '内容'*800, 'date_nodes': [], 'received_at': '2026-10-14T01:00:00Z'}
        with core.db.transaction() as db:
            db.execute('INSERT INTO mail VALUES(?,?,?,?)', (doc['key'], 'school', dumps(doc), doc['received_at']))
        server, thread, url = serve(core)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url)
                page.get_by_label('用户名', exact=True).fill('owner')
                page.get_by_label('密码', exact=True).fill('browser-password')
                page.get_by_role('button', name='登录工作台', exact=True).click()
                page.get_by_text('高级核对工具', exact=True).click()
                page.get_by_role('button', name='标签管理', exact=False).click()
                page.get_by_text('高级：补充人工标签', exact=True).click()
                page.get_by_label('标签名称', exact=True).fill('COMP1000')
                page.get_by_label('标签类型', exact=True).select_option('course')
                page.get_by_label('所属范围', exact=True).fill('2026秋')
                page.get_by_role('button', name='创建标签', exact=True).click()
                expect(page.get_by_text('COMP1000', exact=True)).to_be_visible()
                page.get_by_label('标签名称', exact=True).fill('Group A')
                page.get_by_label('标签类型', exact=True).select_option('group')
                page.get_by_label('所属课程（作业／小组可选）', exact=True).select_option(label='COMP1000 · 2026秋')
                page.get_by_role('button', name='创建标签', exact=True).click()
                expect(page.get_by_text('Group A', exact=True)).to_be_visible()
                page.get_by_role('button', name='作业与截止', exact=False).click()
                page.get_by_role('button', name='标签', exact=True).click()
                dialog = page.get_by_role('dialog')
                dialog.get_by_role('checkbox', name='课程 / COMP1000 · 2026秋', exact=True).check()
                dialog.get_by_role('checkbox', name='小组 / Group A · COMP1000 / 2026秋', exact=True).check()
                dialog.get_by_role('button', name='保存标签', exact=True).click()
                expect(dialog).not_to_be_visible()
                assert len([t for t in core.get_item(item['id'])['tags'] if t['origin'] == 'manual']) == 2
                expect(page.locator('#item-table').get_by_text('COMP1000', exact=True)).to_be_visible()
                page.get_by_text('标签组合筛选（同时包含）', exact=True).click()
                page.get_by_label('标签组合筛选', exact=True).select_option([t['id'] for t in core.get_item(item['id'])['tags'] if t['origin'] == 'manual'])
                expect(page.get_by_role('button', name='课程作业', exact=True)).to_be_visible()
                page.get_by_role('button', name='标签管理', exact=False).click()
                page.locator('.row').filter(has_text='Group A').get_by_role('button', name='重命名', exact=True).click()
                dialog.get_by_label('标签名称', exact=True).fill('Team A')
                dialog.get_by_role('button', name='保存名称', exact=True).click()
                expect(dialog).not_to_be_visible()
                page.locator('.row').filter(has_text='Team A').get_by_role('button', name='归档标签', exact=True).click()
                expect(page.get_by_text('标签状态已更新', exact=True)).to_be_visible()
                page.set_viewport_size({'width': 390, 'height': 844})
                page.get_by_role('button', name='邮件收件箱', exact=False).click()
                page.get_by_role('button', name='生成内容简报', exact=True).click()
                expect(dialog.get_by_role('heading', name='课程邮件', exact=True)).to_be_visible()
                expect(dialog.get_by_text('已截断 · 查看原文获取完整内容', exact=True)).to_be_visible()
                assert page.evaluate('window.XSS') is None
                assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
                page.screenshot(path=str(output/'tags-digest-mobile.png'), full_page=True)
                dialog.get_by_role('button', name='查看邮件原文', exact=True).click()
                expect(dialog.get_by_text(doc['body'], exact=True)).to_be_visible()
                page.get_by_role('button', name='✕', exact=True).click()
                page.reload()
                page.get_by_role('button', name='作业与截止', exact=False).click()
                expect(page.locator('#item-table').get_by_text('Team A（已归档）', exact=True)).to_be_visible()
                assert not errors, errors
                browser.close()
            print('PASS: scoped course/group tags, multiple assignment, persistence, original-content digest, truncation, full text link, mobile, plain text.')
        finally:
            server.should_exit = True
            thread.join(10)


if __name__ == '__main__':
    main()
