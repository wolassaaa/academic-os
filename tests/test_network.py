import httpx
import pytest

from academic_os.network import parse_system_proxy, connection_error_message


def test_windows_proxy_formats_and_no_inline_credentials():
    assert parse_system_proxy('127.0.0.1:7897') == 'http://127.0.0.1:7897'
    assert parse_system_proxy('http=127.0.0.1:7890;https=127.0.0.1:7897') == 'http://127.0.0.1:7897'
    assert parse_system_proxy('http://user:private@127.0.0.1:7897') is None
    assert parse_system_proxy('file:///private') is None


def test_timeout_error_is_actionable_and_does_not_echo_request_secrets():
    error = httpx.ConnectTimeout('private-token-in-transport')
    message = connection_error_message(error)
    assert '超时' in message and 'Google 代理' in message
    assert 'private-token' not in message


def test_auto_selects_system_proxy_reprobes_after_system_change(monkeypatch):
    from academic_os import network
    network._routes.clear()
    system = ['http://127.0.0.1:7897']
    monkeypatch.setattr(network, 'windows_google_proxy', lambda: system[0])
    monkeypatch.delenv('HTTPS_PROXY', raising=False)
    monkeypatch.delenv('https_proxy', raising=False)
    actual = httpx.Client
    observed = []
    def factory(**kwargs):
        proxy = kwargs.get('proxy')
        observed.append(proxy)
        def handler(request):
            return httpx.Response(200, json={'ok': True})
        return actual(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(network.httpx, 'Client', factory)
    with network.GoogleHTTP() as client:
        assert client.get('https://www.googleapis.com/example').status_code == 200
        system[0] = 'http://127.0.0.1:7890'
        assert client.get('https://www.googleapis.com/example').status_code == 200
    assert observed == ['http://127.0.0.1:7897'] * 2 + ['http://127.0.0.1:7890'] * 2


def test_read_timeout_does_not_replay_a_possible_accepted_write(monkeypatch):
    from academic_os import network
    actual = httpx.Client
    requests = []
    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout('accepted-but-response-lost')
    monkeypatch.setattr(network.httpx, 'Client', lambda **kw: actual(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(network, 'select_google_route', lambda *a, **kw: None)
    with network.GoogleHTTP() as client, pytest.raises(httpx.ReadTimeout):
        client.post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send', json={})
    assert len(requests) == 1


def test_network_down_then_recovers_without_restart(monkeypatch):
    from academic_os import network
    network._routes.clear()
    monkeypatch.setattr(network, 'google_candidates', lambda *a: [None])
    actual = httpx.Client
    online = [False]
    def handler(request):
        if not online[0]:
            raise httpx.ConnectError('offline')
        return httpx.Response(200, json={})
    monkeypatch.setattr(network.httpx, 'Client', lambda **kw: actual(transport=httpx.MockTransport(handler)))
    with network.GoogleHTTP() as client:
        with pytest.raises(httpx.ConnectError):
            client.get('https://www.googleapis.com/example')
        online[0] = True
        assert client.get('https://www.googleapis.com/example').status_code == 200
