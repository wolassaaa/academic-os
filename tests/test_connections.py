import json
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from academic_os.api import create_app
from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database
from academic_os.security import Auth


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def get(self, key):
        if key not in self.values:
            raise RuntimeError('missing')
        return self.values[key]

    def set(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


def context(tmp_path):
    from academic_os.connections import Connections
    settings = Settings(data_dir=tmp_path / 'data')
    core = Core(Database(settings.database_path), settings)
    Auth(core.db, settings).set_password('owner', 'eight888')
    service = Connections(core, tmp_path / 'config.json', MemorySecrets())
    app = create_app(core, config_path=tmp_path / 'config.json', connections=service)
    client = TestClient(app)
    login = client.post('/api/v1/login', json={'username': 'owner', 'password': 'eight888'})
    headers = {'X-CSRF-Token': login.json()['csrf_token']}
    return core, service, client, headers


def test_owner_only_and_remote_absent(tmp_path):
    core, service, client, headers = context(tmp_path)
    assert client.get('/api/v1/connections').status_code == 200
    assert client.post('/api/v1/connections/google/client', json={'client_json': '{}'}).status_code == 403
    token = Auth(core.db, core.settings).issue_token('agent', ['read', 'sync'])
    assert client.get('/api/v1/connections', headers={'Authorization': 'Bearer ' + token}).status_code == 403
    assert TestClient(create_app(core, remote=True)).get('/api/v1/connections').status_code == 404


def test_google_pkce_replay_and_session_binding(tmp_path, monkeypatch):
    core, service, client, headers = context(tmp_path)
    payload = {'client_json': json.dumps({'installed': {'client_id': 'id', 'client_secret': 'secret'}})}
    assert client.post('/api/v1/connections/google/client', json=payload, headers=headers).status_code == 200
    begin = client.post('/api/v1/connections/google/authorize', json={}, headers=headers).json()
    query = parse_qs(urlparse(begin['url']).query)
    assert query['code_challenge_method'] == ['S256']
    assert 'secret' not in begin['url']
    state = query['state'][0]
    monkeypatch.setattr(service, 'exchange_google', lambda flow, code: {'client_id': 'id', 'client_secret': 'secret', 'refresh_token': 'refresh'})
    monkeypatch.setattr(service, 'probe_google', lambda cred: {'email': 'real@example.com', 'calendars': [{'id': 'primary', 'summary': 'Calendar', 'accessRole': 'owner'}]})
    assert client.get('/api/v1/connections/google/callback', params={'state': state, 'code': 'code'}).status_code == 200
    assert client.get('/api/v1/connections/google/callback', params={'state': state, 'code': 'code'}).status_code == 422
    assert core.settings.google_accounts[0]['email'] == 'real@example.com'
    assert 'refresh' not in (tmp_path / 'config.json').read_text()
    assert 'secret' not in json.dumps(client.get('/api/v1/connections').json()).replace('secret_ref', '')
    begin = client.post('/api/v1/connections/google/authorize', json={}, headers=headers).json()
    state = parse_qs(urlparse(begin['url']).query)['state'][0]
    client.post('/api/v1/logout', json={}, headers=headers)
    assert client.get('/api/v1/connections/google/callback', params={'state': state, 'code': 'code'}).status_code == 422


def test_moodle_sso_verifies_passport_and_checks_real_identity(tmp_path, monkeypatch):
    import base64
    import hashlib
    core, service, client, headers = context(tmp_path)
    monkeypatch.setattr(service, 'register_moodle_scheme', lambda: None)
    monkeypatch.setattr(service, 'probe_moodle', lambda site, token: {'site_name': 'University', 'username': 'student', 'courses': 3})
    result = client.post('/api/v1/connections/moodle/authorize', json={'site_url': 'https://school.example/moodle'}, headers=headers).json()
    passport = parse_qs(urlparse(result['url']).query)['passport'][0]
    fingerprint = hashlib.md5(('https://school.example/moodle' + passport).encode()).hexdigest()
    uri = 'academicos://token=' + base64.b64encode((fingerprint + ':::real-token:::private-token').encode()).decode()
    assert client.post('/api/v1/connections/moodle/callback', json={'uri': 'academicos://token=bad'}).status_code == 422
    assert client.post('/api/v1/connections/moodle/callback', json={'uri': uri}).status_code == 200
    assert core.settings.moodle['enabled'] is True
    assert client.post('/api/v1/connections/moodle/callback', json={'uri': uri}).status_code == 422
    assert 'real-token' not in (tmp_path / 'config.json').read_text()


@pytest.mark.parametrize('suffix', ['/', '%2F'])
def test_moodle_windows_callback_terminal_slash_keeps_flow_validation(tmp_path, monkeypatch, suffix):
    import base64
    import hashlib
    core, service, client, headers = context(tmp_path)
    monkeypatch.setattr(service, 'register_moodle_scheme', lambda: None)
    seen = []

    def probe(site, token):
        seen.append(token)
        return {'site_name': 'University', 'username': 'student', 'courses': 3}

    monkeypatch.setattr(service, 'probe_moodle', probe)
    result = client.post('/api/v1/connections/moodle/authorize', json={}, headers=headers).json()
    passport = parse_qs(urlparse(result['url']).query)['passport'][0]
    fingerprint = hashlib.md5(('https://buelearning.hkbu.edu.hk' + passport).encode()).hexdigest()
    uri = 'academicos://token=' + base64.b64encode((fingerprint + ':::test-token:::private-token').encode()).decode()
    assert client.post('/api/v1/connections/moodle/callback', json={'uri': uri + suffix}).status_code == 200
    assert seen == ['test-token']
    assert core.settings.moodle['enabled']
    assert client.post('/api/v1/connections/moodle/callback', json={'uri': uri + suffix}).status_code == 422


def test_moodle_legitimate_base64_slash_is_not_removed(tmp_path, monkeypatch):
    import base64
    import hashlib
    _, service, client, headers = context(tmp_path)
    monkeypatch.setattr(service, 'register_moodle_scheme', lambda: None)
    seen = []
    monkeypatch.setattr(service, 'probe_moodle', lambda site, token: (seen.append(token) or {'site_name': 'University', 'username': 'student', 'courses': 1}))
    result = client.post('/api/v1/connections/moodle/authorize', json={}, headers=headers).json()
    passport = parse_qs(urlparse(result['url']).query)['passport'][0]
    fingerprint = hashlib.md5(('https://buelearning.hkbu.edu.hk' + passport).encode()).hexdigest()
    token = 'x\uffff'
    payload = base64.b64encode((fingerprint + ':::' + token).encode()).decode()
    assert payload.endswith('/') and len(payload) % 4 == 0
    assert client.post('/api/v1/connections/moodle/callback', json={'uri': 'academicos://token=' + payload}).status_code == 200
    assert seen == [token]


def test_calendar_selection_enforces_write_permission(tmp_path, monkeypatch):
    core, service, client, headers = context(tmp_path)
    core.settings.google_accounts = [{'id': 'a', 'secret_ref': 'a', 'calendars': []}]
    monkeypatch.setattr(service, 'check_google', lambda account: {'email': 'x', 'calendars': [{'id': 'read', 'accessRole': 'reader'}, {'id': 'write', 'accessRole': 'owner'}]})
    with pytest.raises(ValueError):
        service.select_calendars('a', [{'id': 'read', 'read': True, 'write': True}])
    service.select_calendars('a', [{'id': 'write', 'read': True, 'write': True}])
    assert Settings.load(tmp_path / 'config.json').google_accounts[0]['calendars'][0]['write']


def test_disconnected_calendar_cache_does_not_block_planning(tmp_path):
    from datetime import datetime, timezone
    from academic_os.calendars import calendar_busy
    core, service, client, headers = context(tmp_path)
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    event = {'id': 'old', 'start': {'dateTime': '2026-10-12T10:00:00Z'}, 'end': {'dateTime': '2026-10-12T11:00:00Z'}}
    with core.db.transaction() as db:
        db.execute('INSERT INTO calendar_events VALUES(?,?,?,?)', ('google:a', 'primary', 'old', json.dumps(event)))
    core.settings.google_accounts = [{'id': 'a', 'secret_ref': 'a', 'calendars': [{'id': 'primary', 'read': True}]}]
    assert calendar_busy(core, now)
    core.settings.google_accounts[0]['calendars'][0]['read'] = False
    assert not calendar_busy(core, now)
    core.settings.google_accounts = []
    assert not calendar_busy(core, now)


def test_worker_uses_connections_saved_after_start(tmp_path, monkeypatch):
    from academic_os.worker import Worker
    core, service, client, headers = context(tmp_path)
    worker = Worker(core)
    observed = []
    monkeypatch.setattr(worker, 'collect', lambda providers, accounts: observed.extend(providers) or {})
    worker.connected_job('sources_sync', {})
    core.settings.google_accounts = [{'id': 'new', 'secret_ref': 'new', 'calendars': []}]
    worker.connected_job('sources_sync', {})
    assert observed == ['new']


def test_logout_during_google_exchange_cannot_save_account(tmp_path, monkeypatch):
    core, service, client, headers = context(tmp_path)
    service.configure_client(json.dumps({'installed': {'client_id': 'id', 'client_secret': 'secret'}}))
    result = service.begin_google(client.cookies.get('aos_local'))
    state = parse_qs(urlparse(result['url']).query)['state'][0]
    def exchange(flow, code):
        Auth(core.db, core.settings).logout(client.cookies.get('aos_local'))
        return {'refresh_token': 'r'}
    monkeypatch.setattr(service, 'exchange_google', exchange)
    monkeypatch.setattr(service, 'probe_google', lambda cred: {'email': 'real@example.com'})
    with pytest.raises(ValueError):
        service.finish_google(state, 'code')
    assert not core.settings.google_accounts


def test_disconnect_during_moodle_probe_cannot_reenable(tmp_path, monkeypatch):
    core, service, client, headers = context(tmp_path)
    def probe(site, token):
        service.disconnect('moodle')
        return {'username': 'student'}
    monkeypatch.setattr(service, 'probe_moodle', probe)
    with pytest.raises(ValueError):
        service.connect_moodle('https://school.example', 'token')
    assert not core.settings.moodle


def test_existing_execution_mapping_blocks_calendar_target_switch(tmp_path, monkeypatch):
    core, service, client, headers = context(tmp_path)
    core.settings.google_accounts = [{'id': 'a', 'secret_ref': 'a', 'calendars': [{'id': 'old', 'write': True}]}]
    monkeypatch.setattr(service, 'check_google', lambda account: {'calendars': [{'id': 'new', 'accessRole': 'owner'}]})
    with core.db.transaction() as db:
        db.execute('INSERT INTO calendar_mappings VALUES(?,?,?,?,?)', ('google:a', 'old', 'event', 'item', '{}'))
    with pytest.raises(ValueError, match='迁移'):
        service.select_calendars('a', [{'id': 'new', 'write': True}])


def test_disconnect_during_mirror_stops_remaining_writes(tmp_path):
    from academic_os.calendars import CalendarSync
    core, service, client, headers = context(tmp_path)
    core.settings.mode = 'live'
    core.settings.feishu = {'calendar_id': 'target'}
    core.settings.google_accounts = [{'id': 'a', 'secret_ref': 'a', 'calendars': [{'id': 'primary', 'read': True}]}]
    with core.db.transaction() as db:
        for index in range(2):
            event = {'id': str(index), 'start': {'dateTime': '2026-10-12T10:00:00Z'}, 'end': {'dateTime': '2026-10-12T11:00:00Z'}}
            db.execute('INSERT INTO calendar_events VALUES(?,?,?,?)', ('google:a', 'primary', str(index), json.dumps(event)))
    calls = []
    class Mirror:
        def mirror_event(self, *args):
            calls.append(args)
            if len(calls) == 1:
                service.disconnect('google', 'a')
            return {'event_id': 'mirror'}
    CalendarSync(core, {}, Mirror()).mirror()
    assert len(calls) == 1
