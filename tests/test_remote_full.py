"""Remote full mode: tailnet users (via tailscale serve identity headers) can read and do daily actions remotely;
Funnel/public traffic and account management stay as before."""
import pytest
from fastapi.testclient import TestClient

from academic_os.api import create_app
from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database
from academic_os.security import Auth

TAILNET = {'Tailscale-User-Login': 'owner@example.test', 'Tailscale-User-Name': 'Owner'}
PASSWORD = 'a-strong-password-123'


def setup(tmp_path, **overrides):
    settings = Settings(data_dir=tmp_path, secure_cookies=False, **overrides)
    core = Core(Database(settings.database_path), settings)
    auth = Auth(core.db, settings)
    auth.set_password('owner', PASSWORD)
    agent = auth.issue_token('agent', ['read', 'draft', 'sync', 'confirm'])
    remote = TestClient(create_app(core, remote=True))
    return core, remote, agent


def login(client, headers=None):
    response = client.post('/api/v1/login', json={'username': 'owner', 'password': PASSWORD}, headers=headers or {})
    assert response.status_code == 200, response.text
    return response.json()['csrf_token']


def item(core):
    return core.create_item({'title': 'Essay draft', 'course': 'COMP1001', 'kind': 'assignment', 'due_at': None, 'estimate_minutes': 60, 'priority': 2}, 'test', 'item-key')


def test_settings_validate_remote_access(tmp_path):
    assert Settings(data_dir=tmp_path).remote_access == 'dashboard'
    assert Settings(data_dir=tmp_path).remote_allowed_logins == []
    with pytest.raises(ValueError):
        Settings(data_dir=tmp_path, remote_access='everything')
    with pytest.raises(ValueError):
        Settings(data_dir=tmp_path, remote_allowed_logins='owner@example.test')


def test_default_dashboard_mode_is_unchanged_even_with_tailnet_header(tmp_path):
    core, remote, _ = setup(tmp_path)
    login(remote, TAILNET)
    assert remote.get('/api/v1/session', headers=TAILNET).json()['access'] == 'dashboard'
    assert remote.get('/api/v1/dashboard', headers=TAILNET).status_code == 200
    assert remote.get('/api/v1/board-context').status_code == 200  # the read-only remote board needs it
    for path in ('/api/v1/items', '/api/v1/health', '/api/v1/mail/search'):
        assert remote.get(path, headers=TAILNET).status_code == 404


def test_full_mode_without_identity_header_degrades_to_dashboard(tmp_path):
    core, remote, _ = setup(tmp_path, remote_access='full')
    csrf = login(remote)
    session = remote.get('/api/v1/session').json()
    assert session['remote'] is True and session['access'] == 'dashboard'
    assert remote.get('/api/v1/dashboard').status_code == 200
    assert remote.get('/api/v1/items').status_code == 404
    target = item(core)
    response = remote.patch(f"/api/v1/items/{target['id']}", json={'changes': {'status': 'completed'}, 'expected_version': target['version']}, headers={'X-CSRF-Token': csrf, 'Idempotency-Key': 'k1'})
    assert response.status_code == 404


def test_full_mode_reads_with_tailnet_identity(tmp_path):
    from test_mail_triage import mail
    core, remote, _ = setup(tmp_path, remote_access='full')
    mail(core, account='school')
    target = item(core)
    login(remote, TAILNET)
    session = remote.get('/api/v1/session', headers=TAILNET).json()
    assert session['access'] == 'full' and session['remote'] is True and session['tailnet_login'] == 'owner@example.test'
    assert [i['id'] for i in remote.get('/api/v1/items?view=formal', headers=TAILNET).json()] == [target['id']]
    assert remote.get(f"/api/v1/items/{target['id']}", headers=TAILNET).status_code == 200
    assert remote.get('/api/v1/health', headers=TAILNET).json()['mode'] == 'shadow'
    assert remote.get('/api/v1/board-context', headers=TAILNET).status_code == 200
    assert remote.get('/api/v1/mail/search', headers=TAILNET).json()['total'] == 1
    assert remote.get('/api/v1/mail/school:m1', headers=TAILNET).json()['body'] == 'Original body'
    assert remote.get('/api/v1/tags', headers=TAILNET).status_code == 200
    assert remote.get('/board/', headers=TAILNET).status_code == 200


def test_full_mode_writes_need_session_and_csrf(tmp_path):
    core, remote, _ = setup(tmp_path, remote_access='full')
    target = item(core)
    path = f"/api/v1/items/{target['id']}"
    body = {'changes': {'status': 'completed'}, 'expected_version': target['version']}
    assert remote.patch(path, json=body, headers={**TAILNET, 'Idempotency-Key': 'k1'}).status_code == 401
    csrf = login(remote, TAILNET)
    assert remote.patch(path, json=body, headers={**TAILNET, 'Idempotency-Key': 'k1'}).status_code == 403
    done = remote.patch(path, json=body, headers={**TAILNET, 'X-CSRF-Token': csrf, 'Idempotency-Key': 'k1'})
    assert done.status_code == 200 and done.json()['status'] == 'completed'
    reopened = remote.patch(path, json={'changes': {'status': 'pending'}, 'expected_version': done.json()['version']}, headers={**TAILNET, 'X-CSRF-Token': csrf, 'Idempotency-Key': 'k2'})
    assert reopened.status_code == 200 and reopened.json()['status'] == 'pending'


def test_full_mode_mail_candidate_review_and_focus(tmp_path):
    from test_mail_triage import mail, candidate
    core, remote, _ = setup(tmp_path, remote_access='full')
    mail(core)
    pending, _ = candidate(core)
    csrf = login(remote, TAILNET)
    headers = {**TAILNET, 'X-CSRF-Token': csrf}
    reviewed = remote.post(f"/api/v1/items/{pending['id']}/mail-review", json={'action': 'confirm', 'expected_version': pending['version'], 'changes': {}}, headers={**headers, 'Idempotency-Key': 'review-1'})
    assert reviewed.status_code == 200, reviewed.text
    focus = remote.get('/api/v1/board-focus', headers=TAILNET)
    assert focus.status_code == 200


def test_account_management_and_agent_paths_never_remote(tmp_path):
    core, remote, agent = setup(tmp_path, remote_access='full')
    csrf = login(remote, TAILNET)
    headers = {**TAILNET, 'X-CSRF-Token': csrf}
    for method, path in [('get', '/api/v1/connections'), ('post', '/api/v1/connections/google/authorize'), ('post', '/api/v1/connections/moodle/token'),
                         ('delete', '/api/v1/connections/moodle'), ('post', '/api/v1/setup'), ('get', '/api/v1/mail/feed'), ('post', '/api/v1/mail/feed/ack'),
                         ('put', '/api/v1/classification/rules'), ('get', '/api/v1/instance'), ('get', '/docs'), ('get', '/openapi.json')]:
        response = getattr(remote, method)(path, headers=headers, **({'json': {}} if method in {'post', 'put'} else {}))
        assert response.status_code == 404, (method, path, response.status_code)


def test_agent_bearer_tokens_do_not_work_remotely(tmp_path):
    core, remote, agent = setup(tmp_path, remote_access='full')
    assert remote.get('/api/v1/items', headers={**TAILNET, 'Authorization': 'Bearer ' + agent}).status_code == 401


def test_allowed_logins_restrict_full_mode(tmp_path):
    core, remote, _ = setup(tmp_path, remote_access='full', remote_allowed_logins=['owner@example.test'])
    login(remote, TAILNET)
    assert remote.get('/api/v1/items', headers=TAILNET).status_code == 200
    stranger = {'Tailscale-User-Login': 'friend@example.test'}
    assert remote.get('/api/v1/session', headers=stranger).json()['access'] == 'dashboard'
    assert remote.get('/api/v1/items', headers=stranger).status_code == 404


def test_remote_login_behind_proxy_accepts_forwarded_host_only(tmp_path):
    core, remote, _ = setup(tmp_path, remote_access='full')
    body = {'username': 'owner', 'password': PASSWORD}
    proxied = {'Origin': 'https://box.example-tailnet.ts.net', 'Host': '127.0.0.1:8766'}
    assert remote.post('/api/v1/login', json=body, headers=proxied).status_code == 403
    assert remote.post('/api/v1/login', json=body, headers={**proxied, 'X-Forwarded-Host': 'box.example-tailnet.ts.net'}).status_code == 200
    assert remote.post('/api/v1/login', json=body, headers={'Origin': 'https://evil.example', 'Host': 'box.example-tailnet.ts.net'}).status_code == 403


def test_local_app_ignores_remote_access_setting(tmp_path):
    core, _, agent = setup(tmp_path, remote_access='full')
    local = TestClient(create_app(core))
    assert local.get('/api/v1/items', headers={'Authorization': 'Bearer ' + agent}).status_code == 200
    assert local.get('/api/v1/session').json()['access'] == 'local'
