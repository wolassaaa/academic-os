"""Cross-host Hermes access: resident Streamable HTTP MCP gateway on loopback."""
import asyncio
import json
import threading
import time

import httpx
import pytest
import uvicorn


def run_gateway(app):
    from browser_check import free_port
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            return server, thread, f'http://127.0.0.1:{port}/mcp'
        time.sleep(.05)
    raise RuntimeError('gateway did not start')


@pytest.fixture
def business(tmp_path):
    from academic_os.security import Auth
    from browser_check import serve
    from test_mail_triage import hub, mail, candidate
    core = hub(tmp_path)
    mail(core)
    item, _ = candidate(core)
    auth = Auth(core.db, core.settings)
    server, thread, url = serve(core)
    yield core, auth, url, item
    server.should_exit = True
    thread.join(10)


@pytest.fixture
def gateway(business):
    from academic_os.mcp import create_gateway_app
    core, auth, url, item = business
    server, thread, endpoint = run_gateway(create_gateway_app(url))
    yield endpoint
    server.should_exit = True
    thread.join(10)


def initialize(endpoint, token=None, host=None):
    headers = {'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if host:
        headers['Host'] = host
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 't', 'version': '0'}}}
    return httpx.post(endpoint, json=body, headers=headers, trust_env=False, timeout=10)


def test_gateway_rejects_missing_and_unknown_bearer(gateway):
    for token in (None, 'not-a-real-token'):
        response = initialize(gateway, token)
        assert response.status_code == 401
        assert response.headers['www-authenticate'].startswith('Bearer')


def test_gateway_accepts_tailnet_host_header_with_valid_token(business, gateway):
    core, auth, url, item = business
    token = auth.issue_token('hermes-remote', ['read'])
    response = initialize(gateway, token, host='hermes-box.example-tailnet.ts.net')
    assert response.status_code == 200
    assert response.json()['result']['serverInfo']['name'] == 'Academic OS'


def test_gateway_reports_unreachable_management_api():
    from academic_os.mcp import create_gateway_app
    server, thread, endpoint = run_gateway(create_gateway_app('http://127.0.0.1:1'))
    try:
        response = initialize(endpoint, 'any-token')
        assert response.status_code == 503
        assert response.json()['error'] == 'management_api_unreachable'
    finally:
        server.should_exit = True
        thread.join(10)


def test_gateway_refuses_non_loopback_upstream():
    from academic_os.mcp import create_gateway_app
    with pytest.raises(ValueError):
        create_gateway_app('http://192.168.1.5:8765')


def test_revoked_token_is_rejected_within_cache_window(business):
    from academic_os.mcp import create_gateway_app
    core, auth, url, item = business
    token = auth.issue_token('hermes-short', ['read'])
    server, thread, endpoint = run_gateway(create_gateway_app(url, cache_seconds=0))
    try:
        assert initialize(endpoint, token).status_code == 200
        with core.db.transaction() as db:
            db.execute("DELETE FROM tokens WHERE name='hermes-short'")
        assert initialize(endpoint, token).status_code == 401
    finally:
        server.should_exit = True
        thread.join(10)


def test_official_streamable_http_client_feed_ack_and_scope(business, gateway):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    core, auth, url, item = business
    full = auth.issue_token('hermes-remote', ['read', 'draft', 'sync'])
    readonly = auth.issue_token('hermes-readonly', ['read'])

    async def call(session, name, args):
        result = await session.call_tool(name, args)
        assert not result.isError
        return json.loads(result.content[0].text) if result.content else result.structuredContent['result']

    async def check():
        async with streamablehttp_client(gateway, headers={'Authorization': 'Bearer ' + readonly}) as (r, w, _), ClientSession(r, w) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {'health', 'mail_feed', 'acknowledge_mail_feed'} <= names and 'confirm_draft' not in names
            page = await call(session, 'mail_feed', {})
            denied = await call(session, 'acknowledge_mail_feed', {'consumer': 'hermes', 'cursor': page['next_cursor'], 'expected_cursor': 0})
            assert denied['http_status'] == 403
        async with streamablehttp_client(gateway, headers={'Authorization': 'Bearer ' + full}) as (r, w, _), ClientSession(r, w) as session:
            await session.initialize()
            page = await call(session, 'mail_feed', {})
            assert page['items'][0]['key'] == 'school:m1'
            await call(session, 'acknowledge_mail_feed', {'consumer': 'hermes', 'cursor': page['next_cursor'], 'expected_cursor': 0})
            assert (await call(session, 'mail_feed', {}))['items'] == []
    asyncio.run(check())


def wake_module():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'hermes' / 'academic_os_wake.py'
    spec = importlib.util.spec_from_file_location('academic_os_wake', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wake_gate_new_mail_then_quiet_then_auth_once(business, gateway):
    from academic_os.mail_digest import acknowledge
    core, auth, url, item = business
    wake = wake_module()
    token = auth.issue_token('hermes-wake', ['read', 'sync'])
    state = {}
    awake, context = wake.decide(gateway, token, state)
    assert awake and '新采集' in context
    acknowledge(core, 'hermes', core.health()['consumers'][0]['cursor'] + 1, 0)
    assert wake.decide(gateway, token, state) == (False, None)
    core.settings.google_accounts = [{'id': 'acc', 'secret_ref': 'account-acc', 'email': 'student@example.test', 'calendars': []}]
    core.source_health('gmail:acc', False, 'google_reauthorization_required')
    awake, context = wake.decide(gateway, token, state)
    assert awake and '重新授权' in context
    assert wake.decide(gateway, token, state) == (False, None)


def test_wake_gate_unreachable_reminds_at_most_every_six_hours(tmp_path):
    wake = wake_module()
    state = {}
    awake, context = wake.decide('http://127.0.0.1:1/mcp', 'token', state, now=1000)
    assert awake and '不可达' in context
    assert wake.decide('http://127.0.0.1:1/mcp', 'token', state, now=1000 + 3600) == (False, None)
    assert wake.decide('http://127.0.0.1:1/mcp', 'token', state, now=1000 + 7 * 3600)[0]


def test_wake_script_prints_hermes_gate_line(business, gateway, tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    core, auth, url, item = business
    token = auth.issue_token('hermes-wake-cli', ['read'])
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'hermes' / 'academic_os_wake.py'
    env = {**os.environ, 'ACADEMIC_OS_MCP_URL': gateway, 'ACADEMIC_OS_TOKEN': token, 'HERMES_HOME': str(tmp_path), 'PYTHONIOENCODING': 'utf-8'}
    output = subprocess.run([sys.executable, '-I', str(script)], env=env, capture_output=True, text=True, encoding='utf-8', timeout=60, check=True).stdout
    line = json.loads(output.strip().splitlines()[-1])
    assert line['wakeAgent'] is True and line['context']
    assert (tmp_path / 'scripts' / '.academic-os-wake.json').exists()


def test_settings_validate_agent_gateway_port(tmp_path):
    from academic_os.config import Settings
    assert Settings(data_dir=tmp_path).agent_gateway_port == 0
    assert Settings(data_dir=tmp_path, agent_gateway_port=8767).agent_gateway_port == 8767
    for port in (80, 8765, 8766, 70000):
        with pytest.raises(ValueError):
            Settings(data_dir=tmp_path, agent_gateway_port=port)


def test_runtime_serves_gateway_only_when_enabled(tmp_path):
    from academic_os.core import Core
    from academic_os.config import Settings
    from academic_os.db import Database
    from academic_os.runtime import Runtime
    from academic_os.security import Auth
    from browser_check import free_port
    ports = [free_port() for _ in range(3)]
    while len(set(ports)) < 3:
        ports = [free_port() for _ in range(3)]
    settings = Settings(data_dir=tmp_path / 'data', port=ports[0], dashboard_port=ports[1], agent_gateway_port=ports[2])
    core = Core(Database(settings.database_path), settings)
    token = Auth(core.db, core.settings).issue_token('hermes-runtime', ['read'])
    stop = threading.Event()
    runtime = Runtime(core)
    thread = threading.Thread(target=runtime.run, args=(stop,), daemon=True)
    thread.start()
    try:
        endpoint = f'http://127.0.0.1:{ports[2]}/mcp'
        for _ in range(200):
            try:
                if initialize(endpoint, token).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.05)
        else:
            raise AssertionError('runtime gateway never answered')
        assert initialize(endpoint).status_code == 401
        # Management API is never served on the gateway port.
        assert httpx.get(f'http://127.0.0.1:{ports[2]}/api/v1/items', trust_env=False).status_code in {401, 404}
    finally:
        stop.set()
        thread.join(70)
