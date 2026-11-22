"""Regressions from the 2026-10-25 review: alert flapping, naive timestamps, reconnect-as-stuck, blocking gateway, wake throttling."""
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database


def hub(tmp_path, **overrides):
    settings = Settings(data_dir=tmp_path, secure_cookies=False, **overrides)
    return Core(Database(settings.database_path), settings)


def iso(moment):
    return moment.isoformat()


def ago(**delta):
    return iso(datetime.now(timezone.utc) - timedelta(**delta))


def account(**extra):
    return {'id': 'acc', 'secret_ref': 'account-acc', 'email': 'student@example.test', 'calendars': [{'id': 'primary', 'read': True}], **extra}


def keys(core, prefix):
    with core.db.connect() as db:
        return [r[0] for r in db.execute("SELECT unique_key FROM notifications WHERE unique_key LIKE ?", (prefix + '%',))]


def test_transient_network_error_does_not_fake_recovery(tmp_path):
    from academic_os.notifications import Notifications
    core = hub(tmp_path, google_accounts=[account(authorized_at=ago(days=1))])
    notices = Notifications(core)
    core.source_health('gmail:acc', True)
    core.source_health('gmail:acc', False, 'google_reauthorization_required')
    notices.health_alerts()
    assert len(keys(core, 'auth-required:')) == 1
    core.source_health('gmail:acc', False, 'ConnectError')
    notices.health_alerts()
    assert core.health()['authorizations'][0]['state'] == 'reauthorization_required'
    assert keys(core, 'auth-recovered:') == []
    core.source_health('calendar:google:acc:primary', False, 'google_reauthorization_required')
    notices.health_alerts()
    assert len(keys(core, 'auth-required:')) == 1  # same incident, no second alert
    core.source_health('gmail:acc', True)
    notices.health_alerts()
    assert keys(core, 'auth-recovered:') == []  # calendar still reports reauthorization
    core.source_health('calendar:google:acc:primary', True)
    notices.health_alerts()
    notices.health_alerts()
    assert len(keys(core, 'auth-recovered:')) == 1


def test_deselected_calendar_cannot_hold_an_incident_open(tmp_path):
    from academic_os.notifications import Notifications
    calendars = [{'id': 'cal1', 'read': True}]
    core = hub(tmp_path, google_accounts=[account(authorized_at=ago(days=1), calendars=calendars)])
    notices = Notifications(core)
    core.source_health('calendar:google:acc:cal1', False, 'google_reauthorization_required')
    notices.health_alerts()
    assert core.health()['authorizations'][0]['state'] == 'reauthorization_required'
    core.settings.google_accounts[0]['calendars'] = []  # user deselects the calendar; its source row remains
    core.source_health('gmail:acc', True)
    notices.health_alerts()
    assert core.health()['authorizations'][0]['state'] == 'ok'
    assert len(keys(core, 'auth-recovered:')) == 1


def test_consumer_without_online_since_gets_one_on_next_poll(tmp_path):
    from academic_os.mail_digest import feed
    core = hub(tmp_path)
    core.db.set_meta('consumer-seen:hermes', ago(minutes=1))
    feed(core)
    assert core.db.get_meta('consumer-online-since:hermes')


def test_naive_or_invalid_authorized_at_never_breaks_health(tmp_path):
    from academic_os.mail_digest import feed
    from academic_os.notifications import Notifications
    core = hub(tmp_path, google_accounts=[account(authorized_at='2026-10-20T10:00:00')])
    state = core.health()['authorizations'][0]
    assert state['state'] in {'ok', 'expiring'} and state['expires_estimate'].startswith('2026-10-27T10:00:00')
    core = hub(tmp_path / 'b', google_accounts=[account(authorized_at=12345)])
    assert core.health()['authorizations'][0]['state'] == 'unknown_age'
    core.db.set_meta('consumer-seen:hermes', 'not-a-date')
    assert feed(core)['health']['consumers']
    Notifications(core).health_alerts()


def test_notification_tick_survives_alert_failure(tmp_path):
    from academic_os.worker import Worker
    core = hub(tmp_path)
    worker = Worker(core, handlers={})
    def broken():
        raise RuntimeError('boom')
    worker.notifications.health_alerts = broken
    worker.notification_periodic()
    assert core.db.get_meta('notice-minute') == int(time.time() // 60)


def test_reconnect_after_long_outage_is_ok_not_stuck(tmp_path):
    from academic_os.mail_digest import feed
    from academic_os.db import dumps
    core = hub(tmp_path, agent_gateway_port=8767, agent_stale_minutes=30)
    with core.db.transaction() as db:
        db.execute('INSERT INTO mail VALUES(?,?,?,?)', ('school:m1', 'school', dumps({'id': 'm1', 'body': 'b'}), ago(hours=3)))
        db.execute('UPDATE mail_feed SET collected_at=?', (ago(hours=3),))
    core.db.set_meta('consumer-online-since:hermes', ago(hours=6))
    core.db.set_meta('consumer-seen:hermes', ago(hours=2))
    core.db.set_meta('consumer-ack:hermes', ago(hours=3))
    assert next(c for c in core.health()['consumers'] if c['name'] == 'hermes')['state'] == 'stale'
    feed(core)
    assert next(c for c in core.health()['consumers'] if c['name'] == 'hermes')['state'] == 'ok'


def test_gateway_serves_slow_calls_concurrently():
    from academic_os.mcp import create_gateway_app
    from test_agent_gateway import run_gateway
    def handle(request):
        if request.url.path.endswith('/health'):
            time.sleep(.6)
        return httpx.Response(200, json={'ok': True})
    server, thread, endpoint = run_gateway(create_gateway_app('http://127.0.0.1:9', transport=httpx.MockTransport(handle)))
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'health', 'arguments': {}}}
    headers = {'Authorization': 'Bearer t', 'Accept': 'application/json, text/event-stream'}
    try:
        assert httpx.post(endpoint, json=body, headers=headers, trust_env=False, timeout=10).status_code == 200  # warms token cache
        results = []
        def call():
            results.append(httpx.post(endpoint, json=body, headers=headers, trust_env=False, timeout=10).json())
        started = time.monotonic()
        workers = [threading.Thread(target=call) for _ in range(3)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
        elapsed = time.monotonic() - started
        assert len(results) == 3 and all(json.loads(r['result']['content'][0]['text']) == {'ok': True} for r in results)
        assert elapsed < 1.5, f'calls were serialized ({elapsed:.2f}s)'
    finally:
        server.should_exit = True
        thread.join(10)


def test_forwarded_header_is_the_callers_own_under_concurrency():
    from academic_os.mcp import create_gateway_app
    from test_agent_gateway import run_gateway
    seen = []
    def handle(request):
        token = request.headers.get('authorization', '')
        if request.url.path.endswith('/items'):
            time.sleep(.2)
            seen.append(token)
            return httpx.Response(200, json=[{'echo': token}])
        return httpx.Response(200, json={'ok': True})
    server, thread, endpoint = run_gateway(create_gateway_app('http://127.0.0.1:9', transport=httpx.MockTransport(handle)))
    out = {}
    def call(token):
        body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'list_items', 'arguments': {}}}
        reply = httpx.post(endpoint, json=body, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json, text/event-stream'}, trust_env=False, timeout=10).json()
        out[token] = reply['result']
    try:
        workers = [threading.Thread(target=call, args=(f'tok{i}',)) for i in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
        for token, result in out.items():
            assert ('Bearer ' + token) in json.dumps(result)
        assert len(out) == 4
    finally:
        server.should_exit = True
        thread.join(10)


def test_wake_gate_throttles_errors_and_unchanged_backlog():
    from test_agent_gateway import wake_module
    wake = wake_module()
    pages = iter([])
    wake.call_tool = lambda url, token, name, args, timeout=20: next(pages)
    state, now = {}, 1_800_000_000
    error = {'error': 'business_api_error', 'http_status': 403}
    pages = iter([error, error, error])
    assert wake.decide('u', 't', state, now)[0] is True
    assert wake.decide('u', 't', state, now + 120) == (False, None)
    assert wake.decide('u', 't', state, now + 7 * 3600)[0] is True
    backlog = {'items': [{'key': 'k'}], 'cursor': 0, 'high_water': 3, 'health': {}}
    grown = {**backlog, 'high_water': 4}
    pages = iter([backlog, backlog, grown, grown, grown])
    state = {}
    assert wake.decide('u', 't', state, now)[0] is True
    assert wake.decide('u', 't', state, now + 120) == (False, None)  # same unacked backlog: let the last run finish
    assert wake.decide('u', 't', state, now + 240)[0] is True  # new mail arrived
    assert wake.decide('u', 't', state, now + 360) == (False, None)
    assert wake.decide('u', 't', state, now + 240 + 31 * 60)[0] is True  # still unacked after 30 min: retry
