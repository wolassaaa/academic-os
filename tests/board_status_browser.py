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


with tempfile.TemporaryDirectory(prefix='board-status-') as directory:
    settings = Settings(data_dir=Path(directory), secure_cookies=False)
    core = Core(Database(settings.database_path), settings)
    Auth(core.db, settings).set_password('owner', 'fixture-password-123')
    servers = [serve(core), serve(core, remote=True)]
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for width, height in [(1440, 1000), (390, 844)]:
                record = {'source_id': 'fixture-' + str(width), 'title': '虚构课程作业 · 个人完成不等于学校提交', 'course': 'TEST101', 'submission_status': 'not_started', 'due_at': '2026-12-20T12:00:00+08:00', 'estimate_minutes': 30}
                item = core.ingest('moodle:fictional-school', [record])[0]
                context = browser.new_context(viewport={'width': width, 'height': height}, reduced_motion='reduce')
                url = servers[0][2]
                context.request.post(url + '/api/v1/login', data={'username': 'owner', 'password': 'fixture-password-123'})
                page = context.new_page()
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.add_init_script("window.violations=[];document.addEventListener('securitypolicyviolation',e=>violations.push(e.violatedDirective))")
                page.goto(url + '/board/task.html?item=' + item['id'])
                def ready():
                    page.wait_for_function("()=>window.AcademicServiceView?.state==='ready'&&window.AcademicBoardPreview")
                ready()
                expect(page.locator('.personal-status-fact')).to_have_text('未单独标记')
                expect(page.locator('.source-submission-fact')).to_contain_text('未提交')
                page.get_by_role('button', name='标记完成：' + item['title'], exact=True).click()
                expect(page.get_by_role('dialog')).to_contain_text('不会替你向学校提交')
                page.get_by_role('button', name='确认完成', exact=True).click()
                expect(page.locator('.personal-status-fact')).to_have_text('已完成')
                expect(page.locator('.source-submission-fact')).to_contain_text('未提交')
                original_marker = core.get_item(item['id'])['personal_status']
                changed = {**record, 'title': '来源更新后的长标题：这是一项已经由用户标记完成、但学校仍显示未提交的虚构作业，请区分两个状态'}
                current = core.ingest('moodle:fictional-school', [changed])[0]
                assert current['id'] == item['id'] and current['status'] == 'completed'
                page.reload()
                ready()
                expect(page.locator('.personal-status-fact')).to_have_text('已完成')
                expect(page.locator('.source-submission-fact')).to_contain_text('未提交')
                expect(page.get_by_role('button', name='已关闭：' + current['title'], exact=True)).to_be_disabled()
                page.screenshot(path=str(args.screenshots / f'personal-completed-school-unsubmitted-{width}.png'), full_page=True)
                core.ingest('moodle:fictional-school', [{**changed, 'submission_status': 'submitted'}])
                page.reload()
                ready()
                expect(page.locator('.personal-status-fact')).to_have_text('已完成')
                expect(page.locator('.source-submission-fact')).to_contain_text('已提交')
                core.ingest('moodle:fictional-school', [{**changed, 'submission_status': 'unknown'}])
                page.reload()
                ready()
                expect(page.locator('.source-submission-fact')).to_contain_text('未能确认')
                assert core.get_item(item['id'])['personal_status'] == original_marker
                assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
                assert not page.evaluate('()=>window.violations.length')
                context.close()
            remote = browser.new_context()
            remote.request.post(servers[1][2] + '/api/v1/login', data={'username': 'owner', 'password': 'fixture-password-123'})
            page = remote.new_page()
            page.goto(servers[1][2] + '/board/task.html?item=' + item['id'])
            page.wait_for_function("()=>window.AcademicServiceView?.state==='ready'")
            assert page.locator('.source-submission-fact').count() == 0
            assert 'fictional-school' not in page.locator('main').inner_text()
            remote.close()
            browser.close()
        assert not errors, errors
        print('PASS: desktop1440/mobile390 personal completion retained after school edits; separate submitted/unsubmitted/unknown facts, long title, remote privacy; no CSP/JS/overflow errors.')
    finally:
        for server, thread, _ in servers:
            server.should_exit = True
            thread.join(10)
