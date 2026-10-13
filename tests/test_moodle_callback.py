import json

import httpx

from academic_os.config import Settings


def test_callback_failure_is_reported_without_saving_token(tmp_path):
    from academic_os.moodle_callback import complete_callback
    path = tmp_path / 'config.json'
    settings = Settings(data_dir=tmp_path / 'data', port=1765)
    settings.save(path)
    calls, notices = [], []

    def request(url, uri):
        calls.append((url, uri))
        return httpx.Response(422, json={'error': '授权已失效或已使用，请重新登录。'})

    code = complete_callback('academicos://token=PRIVATE', path, request=request, notify=notices.append)
    assert code == 1
    assert calls[0][0] == 'http://127.0.0.1:1765/api/v1/connections/moodle/callback'
    assert '重新' in notices[0]
    report = (settings.data_dir / 'moodle-login-result.json').read_text(encoding='utf-8')
    assert 'PRIVATE' not in report
    assert json.loads(report)['error_code'] == 'expired_flow'


def test_callback_success_is_reported_and_returns_zero(tmp_path):
    from academic_os.moodle_callback import complete_callback
    path = tmp_path / 'config.json'
    settings = Settings(data_dir=tmp_path / 'data')
    settings.save(path)
    notices = []
    assert complete_callback('academicos://token=PRIVATE', path, request=lambda *args: httpx.Response(200, json={'username': 'PRIVATE'}), notify=notices.append) == 0
    assert '成功' in notices[0]
    assert json.loads((settings.data_dir / 'moodle-login-result.json').read_text())['ok']
    assert 'PRIVATE' not in (settings.data_dir / 'moodle-login-result.json').read_text()


def test_callback_network_exception_does_not_leak_request(tmp_path):
    from academic_os.moodle_callback import complete_callback
    path = tmp_path / 'config.json'
    settings = Settings(data_dir=tmp_path / 'data')
    settings.save(path)
    notices = []

    def request(*args):
        raise httpx.ConnectError('PRIVATE-TOKEN')

    assert complete_callback('academicos://token=PRIVATE', path, request=request, notify=notices.append) == 1
    report = (settings.data_dir / 'moodle-login-result.json').read_text()
    assert 'PRIVATE' not in report + str(notices)
    assert json.loads(report)['error_code'] == 'local_backend_unavailable'


def test_school_rejection_has_safe_category_in_api_and_callback(tmp_path, monkeypatch):
    from test_connections import context
    from academic_os.moodle_callback import complete_callback
    from academic_os.vendor.moodle_client import MoodleApiError
    _, service, client, headers = context(tmp_path)

    def probe(*args):
        raise MoodleApiError('core_webservice_get_site_info', 'invalidtoken')

    monkeypatch.setattr(service, 'probe_moodle', probe)
    result = client.post('/api/v1/connections/moodle/token', json={'site_url': 'https://school.example', 'token': 'PRIVATE'}, headers=headers)
    assert result.status_code == 502
    assert result.json()['code'] == 'invalidtoken'
    assert 'PRIVATE' not in result.text
    service.core.settings.save(tmp_path / 'config.json')
    notices = []
    assert complete_callback('academicos://token=PRIVATE', tmp_path / 'config.json', request=lambda *args: httpx.Response(502, json=result.json()), notify=notices.append) == 1
    assert 'invalidtoken' in notices[0]
