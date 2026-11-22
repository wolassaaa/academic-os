"""Per-candidate review on disposable HTTP service, never an account service."""
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
from academic_os.security import Auth  # noqa: E402
from test_mail_triage import hub, mail, candidate  # noqa: E402


def serve(core):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(core), host='127.0.0.1', port=port, log_level='error'))

    def runner():
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as loop:
            loop.run(server.serve())
    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            return server, thread, f'http://127.0.0.1:{port}'
        time.sleep(.05)
    raise AssertionError('server did not start')


with tempfile.TemporaryDirectory(prefix='board-review-') as temp:
    core = hub(Path(temp))
    Auth(core.db, core.settings).set_password('owner', 'fixture-password-123')
    server, thread, url = serve(core)
    errors, writes = [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for width, height in [(1440, 1000), (390, 844)]:
                mid = 'review' + str(width)
                mail(core, mid)
                one, _ = candidate(core, mid=mid, due=False)
                two, _ = candidate(core, mid=mid, role='second')
                context = browser.new_context(viewport={'width': width, 'height': height}, timezone_id='Asia/Taipei', reduced_motion='reduce')
                context.request.post(url + '/api/v1/login', data={'username': 'owner', 'password': 'fixture-password-123'})
                page = context.new_page()
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.on('request', lambda r: writes.append(r) if r.method == 'POST' else None)
                page.goto(url + '/board/mail-detail.html?key=school:' + mid)

                def ready():
                    page.wait_for_function("()=>document.body.dataset.serviceState==='ready'&&!window.AcademicMailReviewBusy")

                def card(item):
                    return page.locator('.mail-candidate').filter(has=page.locator('h3', has_text=item['title']))

                ready()
                first = page.locator('.mail-candidate[data-item-id="' + one['id'] + '"]')
                second = page.locator('.mail-candidate[data-item-id="' + two['id'] + '"]')
                expect(first.locator('.mail-review-form')).to_have_count(1)
                second.get_by_label('事项标题', exact=True).fill('尚未保存的另一条草稿')
                first.get_by_label('事项标题', exact=True).fill('核对后的无日期任务')
                expect(first.get_by_label('截止时间', exact=True)).to_have_value('')
                first.get_by_role('button', name='确认为待办', exact=True).click()
                dialog = page.get_by_role('dialog')
                expect(dialog).to_be_visible()
                expect(dialog.get_by_role('button', name='取消', exact=True)).to_be_focused()
                page.keyboard.press('Escape')
                expect(dialog).not_to_be_visible()
                assert not writes
                first.get_by_role('button', name='确认为待办', exact=True).click()
                dialog.get_by_role('button', name='确认操作', exact=True).click()
                expect(first).to_contain_text('邮件待办')
                ready()
                assert core.get_item(one['id'])['source_due_at'] is None
                assert core.get_item(one['id'])['title'] == '核对后的无日期任务'
                expect(second.get_by_label('事项标题', exact=True)).to_have_value('尚未保存的另一条草稿')
                assert core.get_item(two['id'])['mail_review']['state'] == 'pending'
                second.get_by_role('button', name='忽略候选', exact=True).click()
                dialog.get_by_role('button', name='确认操作', exact=True).click()
                expect(second).to_contain_text('已忽略')
                ready()
                second.get_by_role('button', name='恢复待核对', exact=True).click()
                dialog.get_by_role('button', name='确认操作', exact=True).click()
                expect(second.get_by_label('事项标题', exact=True)).to_be_visible()
                ready()
                assert core.get_mail('school:' + mid)['body'] == 'Original body'
                assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
                page.screenshot(path=str(args.screenshots / f'review-{width}.png'), full_page=True)
                context.close()
                writes.clear()
            # Failure-path session uses a separate fictional mail and one candidate at a time.
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
            context.request.post(url + '/api/v1/login', data={'username': 'owner', 'password': 'fixture-password-123'})
            page = context.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.add_init_script("window.violations=[];document.addEventListener('securitypolicyviolation',e=>violations.push(e.violatedDirective))")

            def open_candidate(mid):
                mail(core, mid)
                item, _ = candidate(core, mid=mid)
                page.goto(url + '/board/mail-detail.html?key=school:' + mid)
                ready()
                return item, page.locator('.mail-candidate[data-item-id="' + item['id'] + '"]')

            def confirm(c):
                c.get_by_role('button', name='确认为待办', exact=True).click()
                page.get_by_role('dialog').get_by_role('button', name='确认操作', exact=True).click()

            item, c = open_candidate('lost-response')
            keys, bodies = [], []

            def lost(route):
                keys.append(route.request.headers['idempotency-key'])
                bodies.append(route.request.post_data)
                if len(keys) == 1:
                    response = route.fetch()
                    assert response.status == 200
                    route.abort('failed')
                else:
                    route.continue_()
            page.route('**/mail-review', lost)
            confirm(c)
            dialog = page.get_by_role('dialog')
            expect(dialog).to_contain_text('尚未收到可靠回执')
            dialog.get_by_role('button', name='取消', exact=True).click()
            expect(c.locator('h3')).to_be_focused()
            page.get_by_role('button', name='刷新', exact=True).click()
            ready()
            expect(c.get_by_role('button', name='核实未决操作', exact=True)).to_be_visible()
            c.get_by_role('button', name='核实未决操作', exact=True).click()
            dialog.get_by_role('button', name='核实并重试', exact=True).click()
            expect(dialog).not_to_be_visible()
            ready()
            assert len(keys) == 2 and keys[0] == keys[1] and bodies[0] == bodies[1]
            assert core.get_item(item['id'])['mail_review']['revision'] == 1
            page.unroute('**/mail-review', lost)

            item, c = open_candidate('conflict-response')
            c.get_by_label('事项标题', exact=True).fill('保留并重新核对的草稿')
            core.review_mail_item(item['id'], 'ignore', item['version'], 'external-conflict')
            confirm(c)
            expect(dialog).to_contain_text('事项已变化')
            expect(dialog.get_by_role('button', name='确认操作', exact=True)).to_be_disabled()
            dialog.get_by_role('button', name='取消', exact=True).click()
            expect(c.locator('h3')).to_be_focused()
            page.get_by_role('button', name='刷新', exact=True).click()
            ready()
            expect(c.get_by_role('button', name='恢复待核对', exact=True)).to_be_visible()
            assert core.get_item(item['id'])['mail_review']['state'] == 'ignored'

            item, c = open_candidate('stale-draft')
            c.get_by_label('事项标题', exact=True).fill('版本变更不覆盖人工草稿')
            # A source refresh advances business version while this remains pending.
            record = {'source_id': 'stale-draft:deadline:2026-10-13:date', 'title': 'New source title', 'estimate_minutes': 30, 'review_state': 'needs_analysis'}
            core.ingest('gmail:school', [record])
            page.get_by_role('button', name='刷新', exact=True).click()
            ready()
            expect(c.get_by_label('事项标题', exact=True)).to_have_value('版本变更不覆盖人工草稿')
            expect(c.get_by_role('button', name='确认为待办', exact=True)).to_be_disabled()
            c.get_by_role('button', name='放弃草稿并重新核对', exact=True).click()
            expect(c.get_by_label('事项标题', exact=True)).to_have_value(core.get_item(item['id'])['title'])

            item, c = open_candidate('forbidden')
            def forbidden(route):
                route.fulfill(status=403, content_type='application/json', body='{"detail":"forbidden"}')
            page.route('**/mail-review', forbidden)
            confirm(c)
            expect(dialog).to_contain_text('没有确认权限')
            assert core.get_item(item['id'])['mail_review']['state'] == 'pending'
            dialog.get_by_role('button', name='取消', exact=True).click()
            page.unroute('**/mail-review', forbidden)

            item, c = open_candidate('refresh-failure')
            def commit_then_stale(route):
                response = route.fetch()
                assert response.status == 200
                page.route('**/api/v1/mail/**', lambda r: r.abort('failed'))
                route.fulfill(response=response)
            page.route('**/mail-review', commit_then_stale)
            confirm(c)
            expect(page.locator('.mail-review-feedback')).to_contain_text('操作已确认；刷新失败')
            expect(c).to_contain_text('邮件待办')
            assert core.get_item(item['id'])['mail_review']['state'] == 'confirmed'
            page.unroute('**/mail-review', commit_then_stale)
            page.unroute('**/api/v1/mail/**')

            item, c = open_candidate('expired')
            page.route('**/mail-review', lambda r: r.fulfill(status=401, content_type='application/json', body='{}'))
            confirm(c)
            expect(page.locator('main')).not_to_be_visible()
            expect(page.locator('.mail-login')).to_be_visible()
            assert core.get_item(item['id'])['mail_review']['state'] == 'pending'
            assert not page.evaluate('()=>window.violations.length')
            context.close()
            browser.close()
        assert not errors, errors
        print('PASS: desktop/mobile independent candidate drafts, cancel/Escape no writes, unknown-date confirmation, ignore/restore and retained mail; lost-response exact replay, stale drafts, 409/403/401 and post-commit refresh recovery; no overflow/CSP/JS errors.')
    finally:
        server.should_exit = True
        thread.join(10)
