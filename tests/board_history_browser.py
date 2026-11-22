"""Completion and school-observation separation on disposable HTTP services."""
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
p.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
p.add_argument('--screenshots', type=Path, required=True)
args = p.parse_args()
args.screenshots.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(args.source_root / 'src'))
from academic_os.api import create_app  # noqa: E402
from academic_os.config import Settings  # noqa: E402
from academic_os.core import Core  # noqa: E402
from academic_os.db import Database  # noqa: E402
from academic_os.security import Auth  # noqa: E402


def serve(core, remote=False):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(core, remote=remote), host='127.0.0.1', port=port, log_level='error'))
    def runner():
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as loop:
            loop.run(server.serve())
    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            return server, thread, f'http://127.0.0.1:{port}'
        time.sleep(.05)
    raise AssertionError('test server did not start')


with tempfile.TemporaryDirectory(prefix='board-history-') as directory:
    settings = Settings(data_dir=Path(directory), secure_cookies=False)
    core = Core(Database(settings.database_path), settings)
    Auth(core.db, settings).set_password('owner', 'fixture-password-123')
    servers = [serve(core), serve(core, remote=True)]
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for width, height in [(1440, 1000), (390, 844)]:
                title = '虚构历史作业 ' + str(width) + ' · 完成后仍能找到并继续跟进'
                record = {'source_id': 'fixture-' + str(width), 'title': title, 'course': 'HISTORY101', 'submission_status': 'not_started', 'due_at': '2026-12-20T12:00:00+08:00'}
                item = core.ingest('moodle:fictional-school', [record])[0]
                context = browser.new_context(viewport={'width': width, 'height': height}, reduced_motion='reduce')
                url = servers[0][2]
                context.request.post(url + '/api/v1/login', data={'username': 'owner', 'password': 'fixture-password-123'})
                page = context.new_page()
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.add_init_script("window.violations=[];document.addEventListener('securitypolicyviolation',e=>violations.push(e.violatedDirective))")
                def ready():
                    page.wait_for_function("()=>window.AcademicServiceView?.state==='ready'&&window.AcademicBoardPreview")
                page.goto(url + '/board/tasks.html')
                ready()
                expect(page.get_by_role('button', name='已关闭/历史', exact=True)).to_be_visible()
                page.get_by_role('button', name='完成：' + title, exact=True).click()
                expect(page.get_by_role('button', name='取消', exact=True)).to_be_focused()
                page.get_by_role('button', name='确认完成', exact=True).click()
                expect(page.locator('.completion-feedback')).to_contain_text('已完成')
                expect(page.locator('.all-task-list')).not_to_contain_text(title)
                page.get_by_role('button', name='已关闭/历史', exact=True).click()
                expect(page.locator('.all-task-list')).to_contain_text(title)
                expect(page.locator('.all-task-list')).to_contain_text('来源仍显示未提交')
                assert 'history=all' in page.url
                page.get_by_label('历史状态').select_option('submitted')
                expect(page.locator('.all-task-list')).not_to_contain_text(title)
                page.get_by_label('历史状态').select_option('pending-submission')
                page.get_by_label('筛选课程').select_option(label='HISTORY101')
                page.get_by_role('searchbox', name='搜索任务或课程').fill(title)
                page.get_by_label('开始日期').fill('2026-12-20')
                page.get_by_label('结束日期').fill('2026-12-20')
                page.get_by_role('button', name='应用时段', exact=True).click()
                page.reload()
                ready()
                expect(page.get_by_label('历史状态')).to_have_value('pending-submission')
                expect(page.get_by_role('searchbox', name='搜索任务或课程')).to_have_value(title)
                expect(page.get_by_label('开始日期')).to_have_value('2026-12-20')
                expect(page.get_by_label('筛选课程').locator('option:checked')).to_have_text('HISTORY101')
                row = page.locator('.task-row').filter(has=page.get_by_role('link', name=title, exact=True))
                expect(row).to_contain_text('已完成')
                expect(row.get_by_text('已完成', exact=True)).to_be_visible()
                assert row.get_by_role('button', name='重新打开：' + title, exact=True).bounding_box()['width'] >= 80
                assert row.locator('.time-track,.remaining-label').count() == 0
                page.screenshot(path=str(args.screenshots / f'history-{width}.png'), full_page=True)
                page.get_by_role('link', name=title, exact=True).click()
                ready()
                expect(page.get_by_role('link', name='返回历史', exact=True)).to_be_visible()
                expect(page.locator('.submission-followup')).to_contain_text('来源仍显示未提交')
                page.get_by_role('button', name='重新打开：' + title, exact=True).click()
                expect(page.get_by_role('dialog')).to_contain_text('不会撤销学校的提交记录')
                expect(page.get_by_role('button', name='取消', exact=True)).to_be_focused()
                page.keyboard.press('Escape')
                assert core.get_item(item['id'])['status'] == 'completed'
                page.get_by_role('button', name='重新打开：' + title, exact=True).click()
                page.screenshot(path=str(args.screenshots / f'reopen-confirm-{width}.png'), full_page=True)
                page.get_by_role('button', name='确认重新打开', exact=True).click()
                expect(page.locator('.personal-status-fact')).to_have_text('待完成')
                expect(page.locator('.source-submission-fact')).to_contain_text('未提交')
                expect(page.locator('.completion-feedback')).to_contain_text('已重新打开')
                reopened = core.get_item(item['id'])
                assert reopened['status'] == 'pending' and reopened['id'] == item['id']
                assert reopened['evidence'][0]['submission_status'] == 'not_started'
                core.ingest('moodle:fictional-school', [{**record, 'submission_status': 'submitted'}])
                page.reload()
                ready()
                expect(page.locator('.personal-status-fact')).to_have_text('待完成')
                expect(page.locator('.source-submission-fact')).to_contain_text('已提交')
                expect(page.locator('.submission-followup')).to_have_count(0)
                page.get_by_role('link', name='返回历史', exact=True).click()
                ready()
                expect(page.locator('.all-task-list')).not_to_contain_text(title)
                expect(page.get_by_label('开始日期')).to_have_value('2026-12-20')
                expect(page.get_by_role('searchbox', name='搜索任务或课程')).to_have_value(title)
                page.get_by_role('button', name='待完成', exact=True).click()
                expect(page.locator('.all-task-list')).to_contain_text(title)
                page.reload()
                ready()
                expect(page.locator('.all-task-list')).to_contain_text(title)
                assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
                assert not page.evaluate('()=>window.violations.length')
                # A committed reopen whose response was lost replays one exact intent.
                closed = core.update_item(item['id'], {'status': 'completed'}, core.get_item(item['id'])['version'])
                page.goto(url + '/board/task.html?item=' + item['id'])
                ready()
                requests = []
                path = '**/api/v1/items/' + item['id']
                def lose(route):
                    requests.append((route.request.headers.get('idempotency-key'), route.request.post_data))
                    assert route.fetch().status == 200
                    route.abort('failed')
                page.route(path, lose)
                page.get_by_role('button', name='重新打开：' + title, exact=True).click()
                page.get_by_role('button', name='确认重新打开', exact=True).click()
                expect(page.locator('.completion-error')).to_contain_text('结果暂未确认')
                assert core.get_item(item['id'])['version'] == closed['version'] + 1
                page.unroute(path)
                def retry(route):
                    requests.append((route.request.headers.get('idempotency-key'), route.request.post_data))
                    route.continue_()
                page.route(path, retry)
                page.get_by_role('button', name='核实并重试', exact=True).click()
                expect(page.locator('.completion-feedback')).to_contain_text('已重新打开')
                assert len(requests) == 2 and requests[0] == requests[1]
                assert core.get_item(item['id'])['version'] == closed['version'] + 1
                page.unroute(path)
                # Concurrent change cannot be silently overwritten.
                closed = core.update_item(item['id'], {'status': 'cancelled'}, core.get_item(item['id'])['version'])
                page.reload()
                ready()
                page.get_by_role('button', name='重新打开：' + title, exact=True).click()
                core.update_item(item['id'], {'priority': 5}, closed['version'])
                page.get_by_role('button', name='确认重新打开', exact=True).click()
                expect(page.locator('.completion-error')).to_contain_text('事项已被其他操作更新')
                assert core.get_item(item['id'])['status'] == 'cancelled'
                page.keyboard.press('Escape')
                # A stale/offline view must disable reopen controls, no mutation.
                page.route('**/api/v1/items?*', lambda route: route.abort('failed'))
                page.get_by_role('button', name='刷新', exact=True).click()
                expect(page.locator('body')).to_have_attribute('data-service-state', 'stale')
                assert page.locator('[data-reopen]:enabled').count() == 0
                context.close()
            remote = browser.new_context()
            remote.request.post(servers[1][2] + '/api/v1/login', data={'username': 'owner', 'password': 'fixture-password-123'})
            page = remote.new_page()
            page.goto(servers[1][2] + '/board/tasks.html?history=all')
            page.wait_for_function("()=>window.AcademicServiceView?.state==='ready'&&window.AcademicBoardPreview")
            assert page.locator('[data-reopen]:enabled').count() == 0
            expect(page.locator('.all-task-list')).to_contain_text('重新打开')
            assert page.locator('.submission-followup').count() == 0
            remote.close()
            browser.close()
        assert not errors, errors
        print('PASS: desktop1440/mobile390 complete/history/filter/reload/reopen/sync facts; cancel, exact retry, conflict, stale disabled, remote readonly; no JS/CSP/overflow errors.')
    finally:
        for server, thread, _ in servers:
            server.should_exit = True
            thread.join(10)
