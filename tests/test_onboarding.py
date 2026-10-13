import json
import socket
import threading
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from academic_os.config import Settings
from test_connections import context


def reconnect(tmp_path):
    core, service, client, headers = context(tmp_path)
    service.configure_client(json.dumps({'installed': {'client_id': 'id', 'client_secret': 'secret'}}))
    service.adopt_google({'client_id': 'id', 'client_secret': 'secret', 'refresh_token': 'old'}, {'email': 'owner@example.test'})
    account_id = core.settings.google_accounts[0]['id']
    result = service.begin_google(client.cookies.get('aos_local'), account_id)
    query = parse_qs(urlparse(result['url']).query)
    return service, query, service.flows[query['state'][0]]


def test_reconnect_does_not_force_consent(tmp_path):
    service, query, flow = reconnect(tmp_path)
    assert 'consent' not in query['prompt'][0]
    assert query['login_hint'] == ['owner@example.test']


def test_reconnect_preserves_token_only_for_verified_same_identity(tmp_path, monkeypatch):
    service, query, flow = reconnect(tmp_path)
    def handler(request):
        if request.url.path == '/token':
            return httpx.Response(200, json={'access_token': 'new-access'})
        assert request.headers['authorization'] == 'Bearer new-access'
        return httpx.Response(200, json={'emailAddress': 'owner@example.test'})
    monkeypatch.setattr('academic_os.connections.GoogleHTTP', lambda **kw: httpx.Client(transport=httpx.MockTransport(handler)))
    result = service.exchange_google(flow, 'code')
    assert result['refresh_token'] == 'old'
    assert 'access_token' not in result


def test_reconnect_cannot_reuse_another_identity_token(tmp_path, monkeypatch):
    service, query, flow = reconnect(tmp_path)
    def handler(request):
        return httpx.Response(200, json={'access_token': 'new-access'} if request.url.path == '/token' else {'emailAddress': 'other@example.test'})
    monkeypatch.setattr('academic_os.connections.GoogleHTTP', lambda **kw: httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError, match='账号'):
        service.exchange_google(flow, 'code')


def test_forced_repair_requests_consent_and_new_token_replaces_old(tmp_path, monkeypatch):
    service, query, flow = reconnect(tmp_path)
    result = service.begin_google('test-session', flow['account_id'], force_consent=True)
    assert 'consent' in parse_qs(urlparse(result['url']).query)['prompt'][0]
    monkeypatch.setattr('academic_os.connections.GoogleHTTP', lambda **kw: httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'refresh_token': 'new'}))))
    assert service.exchange_google(flow, 'code')['refresh_token'] == 'new'


def test_refresh_survives_restart_without_interactive_login(monkeypatch):
    from academic_os.providers import Google, ProviderError
    from test_connections import MemorySecrets
    store = MemorySecrets()
    original = {'client_id': 'id', 'client_secret': 'secret', 'refresh_token': 'offline'}
    store.set('account', json.dumps(original))
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url.path == '/token'
        assert b'grant_type=refresh_token' in request.content
        return httpx.Response(200, json={'access_token': 'access', 'expires_in': 3600})
    monkeypatch.setattr('academic_os.providers.GoogleHTTP', lambda **kw: httpx.Client(transport=httpx.MockTransport(handler)))
    for restart in range(2):
        provider = Google(store, 'account')
        assert provider.access_token() == 'access'
        assert provider.access_token() == 'access'
        provider._expires = 0
        assert provider.access_token() == 'access'
        provider.http.close()
    assert len(requests) == 4 and json.loads(store.get('account')) == original
    provider = Google(store, 'account')
    provider.http.close()
    provider.http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(400, json={'error': 'invalid_grant'})))
    with pytest.raises(ProviderError) as error:
        provider.access_token()
    assert error.value.code == 'google_reauthorization_required'
    assert json.loads(store.get('account')) == original
    provider.http.close()


def test_network_failure_keeps_existing_account_and_credential(tmp_path, monkeypatch):
    service, query, flow = reconnect(tmp_path)
    before = dict(service.secrets.values)
    def fail(*args):
        raise httpx.ConnectError('offline')
    monkeypatch.setattr(service, 'exchange_google', fail)
    with pytest.raises(httpx.ConnectError):
        service.finish_google(query['state'][0], 'code')
    assert service.secrets.values == before
    assert service.account(flow['account_id'])['email'] == 'owner@example.test'


def test_old_null_proxy_migrates_to_auto_explicit_proxy_to_manual(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'google_proxy': None}))
    assert Settings.load(path).google_network_mode == 'auto'
    path.write_text(json.dumps({'google_proxy': 'http://127.0.0.1:7897'}))
    assert Settings.load(path).google_network_mode == 'manual'


def test_settings_atomic_save_retries_short_windows_reader_lock(tmp_path, monkeypatch):
    from academic_os import config
    original = config.os.replace
    attempts = []
    def replace(source, target):
        attempts.append(target)
        if len(attempts) < 3:
            raise PermissionError('brief file reader')
        return original(source, target)
    monkeypatch.setattr(config.os, 'replace', replace)
    path = tmp_path / 'config.json'
    Settings(data_dir=tmp_path / 'data').save(path)
    assert len(attempts) == 3 and Settings.load(path).mode == 'shadow'


def test_desktop_runtime_selects_free_ports_and_publishes_verified_instance(tmp_path, monkeypatch):
    from academic_os import runtime
    from academic_os.portable import existing_instance_url
    from academic_os.core import Core
    from academic_os.db import Database
    monkeypatch.setattr(runtime.Worker, 'run', lambda self, stop: stop.wait())
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        with socket.socket() as spare:
            spare.bind(('127.0.0.1', 0))
            dashboard_port = spare.getsockname()[1]
        config = tmp_path / 'config.json'
        settings = Settings(data_dir=tmp_path / 'data', port=occupied.getsockname()[1], dashboard_port=dashboard_port)
        settings.save(config)
        core = Core(Database(settings.database_path), settings)
        service = runtime.Runtime(core, config, allow_port_fallback=True)
        stop, errors = threading.Event(), []
        def run():
            try:
                service.run(stop)
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        try:
            end = time.monotonic() + 10
            url = None
            while not url and time.monotonic() < end:
                url = existing_instance_url(config)
                time.sleep(.05)
            if errors:
                raise errors[0]
            assert url == f'http://127.0.0.1:{settings.port}'
            assert settings.port != occupied.getsockname()[1]
            assert Settings.load(config).port == settings.port
            assert httpx.get(url + '/api/v1/instance', trust_env=False).status_code == 404
        finally:
            stop.set()
            thread.join(15)
        assert not errors and not thread.is_alive()
        assert existing_instance_url(config) is None


def test_google_callback_is_safe_chinese_html_and_progress_is_owner_only(tmp_path):
    core, service, client, headers = context(tmp_path)
    response = client.get('/api/v1/connections/google/callback', params={'state': 'bad', 'error': '<script>private</script>'})
    assert response.status_code == 422
    assert response.headers['content-type'].startswith('text/html')
    assert '重新登录' in response.text and '<script>private' not in response.text
    assert client.get('/api/v1/connections/flows').status_code == 200
    client.post('/api/v1/logout', json={}, headers=headers)
    assert client.get('/api/v1/connections/flows').status_code == 401
