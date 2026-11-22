"""Exact board review payload against existing API, using disposable data."""
from fastapi.testclient import TestClient

from academic_os.api import create_app
from academic_os.notifications import Notifications
from academic_os.security import Auth
from test_mail_triage import candidate, hub, mail


def logged(core):
    auth = Auth(core.db, core.settings)
    auth.set_password('owner', 'fixture-password-123')
    client = TestClient(create_app(core))
    result = client.post('/api/v1/login', json={'username': 'owner', 'password': 'fixture-password-123'})
    return client, {'X-CSRF-Token': result.json()['csrf_token']}, auth


def test_board_exact_payload_preserves_unknown_id_and_other_candidate(tmp_path):
    core = hub(tmp_path)
    original = mail(core)
    one, record = candidate(core, due=False)
    two, _ = candidate(core, role='another')
    client, headers, _ = logged(core)
    path = '/api/v1/items/' + one['id'] + '/mail-review'
    body = {'action': 'confirm', 'expected_version': one['version'], 'changes': {'title': 'Human title', 'source_due_at': None}}
    headers['Idempotency-Key'] = 'board-one'
    first = client.post(path, json=body, headers=headers)
    assert first.status_code == 200
    confirmed = first.json()
    assert confirmed['id'] == one['id'] and confirmed['source_due_at'] is None
    assert confirmed['mail_review']['revision'] == 1
    assert client.post(path, json=body, headers=headers).json() == confirmed
    assert core.get_item(two['id'])['mail_review']['state'] == 'pending'
    core.ingest('gmail:school', [{**record, 'title': 'Changed source', 'due_at': '2026-10-18T12:00:00Z'}])
    current = core.get_item(one['id'])
    assert current['title'] == 'Human title' and current['source_due_at'] is None
    assert current['mail_review']['state'] == 'confirmed'
    assert current['review_state'] == 'conflict'
    assert core.get_mail('school:m1')['body'] == original['body']


def test_board_replay_still_requires_csrf_permission_and_local_route(tmp_path):
    core = hub(tmp_path)
    mail(core)
    item, _ = candidate(core)
    client, headers, auth = logged(core)
    path = '/api/v1/items/' + item['id'] + '/mail-review'
    body = {'action': 'ignore', 'expected_version': item['version'], 'changes': {}}
    headers['Idempotency-Key'] = 'board-ignore'
    assert client.post(path, json=body, headers=headers).status_code == 200
    assert client.post(path, json=body, headers={'Idempotency-Key': 'board-ignore'}).status_code == 403
    agent = TestClient(create_app(core))
    token = auth.issue_token('agent', ['read', 'draft', 'sync'])
    assert agent.post(path, json=body, headers={'Authorization': 'Bearer ' + token, 'Idempotency-Key': 'board-ignore'}).status_code == 403
    assert client.post(path, json=body, headers={**headers, 'Idempotency-Key': 'new-stale-key'}).status_code == 409
    assert TestClient(create_app(core, remote=True)).post(path, json=body, headers=headers).status_code == 404


def test_board_restore_then_confirm_opens_reminder_gate_once(tmp_path):
    core = hub(tmp_path)
    mail(core)
    item, _ = candidate(core)
    notices = Notifications(core)
    arrival = notices.queue('workbench', {'title': 'New mail'}, 'board-arrival')
    client, csrf, _ = logged(core)
    path = '/api/v1/items/' + item['id'] + '/mail-review'
    assert notices.generate()['considered'] == 0
    for action in ['ignore', 'restore', 'confirm']:
        current = core.get_item(item['id'])
        changes = {'title': current['title'], 'source_due_at': current['source_due_at']} if action == 'confirm' else {}
        body = {'action': action, 'expected_version': current['version'], 'changes': changes}
        result = client.post(path, json=body, headers={**csrf, 'Idempotency-Key': 'board-' + action})
        assert result.status_code == 200
        assert notices.generate()['considered'] == (1 if action == 'confirm' else 0)
    assert core.get_item(item['id'])['mail_review']['revision'] == 3
    assert any(n['id'] == arrival for n in notices.list())
    assert any(n['item_id'] == item['id'] for n in notices.list())
    assert core.list_jobs() == []  # No calendar side effect.
