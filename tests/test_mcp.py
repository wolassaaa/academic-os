import importlib.util

import httpx
import pytest


def module():
    assert importlib.util.find_spec('academic_os.mcp'), 'MCP implementation is missing'
    from academic_os import mcp
    return mcp


def test_proxy_rejects_non_loopback_and_url_credentials():
    mcp = module()
    for url in ('http://example.com', 'http://127.0.0.1@evil.test', 'http://127.0.0.1:8765/other', 'https://127.0.0.1:8765'):
        with pytest.raises(ValueError):
            mcp.APIProxy(url, 'token')


def test_tools_use_scoped_business_http_api_only():
    mcp = module()
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={'ok': True})
    proxy = mcp.APIProxy('http://127.0.0.1:8765', 'test-token', transport=httpx.MockTransport(handle))
    assert proxy.request('POST', '/drafts', {'changes': []}, 'same-key') == {'ok': True}
    assert calls[0].url.path == '/api/v1/drafts'
    assert calls[0].headers['Authorization'] == 'Bearer test-token'
    assert calls[0].headers['Idempotency-Key'] == 'same-key'


def test_confirm_tool_requires_explicit_opt_in():
    import asyncio
    mcp = module()
    server = mcp.build_server(mcp.APIProxy('http://127.0.0.1:8765', 'test-token'))
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert {'list_items', 'get_item', 'list_notifications', 'calendar_events', 'list_artifacts', 'model_spec', 'create_draft', 'sync', 'generate_plan'} <= names
    assert 'confirm_draft' not in names
    server = mcp.build_server(mcp.APIProxy('http://127.0.0.1:8765', 'test-token'), allow_confirm=True)
    assert 'confirm_draft' in {tool.name for tool in asyncio.run(server.list_tools())}


def test_real_official_stdio_handshake_and_tool_call():
    import asyncio
    import os
    import sys
    from pathlib import Path

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    env = {**os.environ, 'ACADEMIC_OS_TOKEN': 'offline-test-token', 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
    async def check():
        params = StdioServerParameters(command=sys.executable, args=['-m', 'academic_os.mcp', '--base-url', 'http://127.0.0.1:1'], env=env)
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert 'create_draft' in {tool.name for tool in tools.tools}
            assert 'confirm_draft' not in {tool.name for tool in tools.tools}
            result = await session.call_tool('health', {})
            assert 'management_api_unreachable' in result.content[0].text
    asyncio.run(check())
def test_mcp_uses_actual_port_from_portable_config(tmp_path, monkeypatch):
    from academic_os import mcp
    from academic_os.config import Settings
    config = tmp_path / 'config.json'
    Settings(data_dir=tmp_path / 'data', port=18875).save(config)
    monkeypatch.delenv('ACADEMIC_OS_URL', raising=False)
    monkeypatch.setenv('ACADEMIC_OS_TOKEN', 'test-placeholder')
    observed = []
    class Server:
        def run(self, **kwargs):
            pass
    def build(proxy, allow_confirm):
        observed.append(str(proxy.client.base_url))
        return Server()
    monkeypatch.setattr(mcp, 'build_server', build)
    mcp.main(['--config', str(config)])
    assert observed == ['http://127.0.0.1:18875/api/v1/']


def test_real_stdio_tags_digest_feed_against_business_server(tmp_path):
    import asyncio
    import json
    import os
    import sys
    from pathlib import Path
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from academic_os.security import Auth
    from test_mail_triage import hub, mail, candidate
    from browser_check import serve

    core = hub(tmp_path)
    mail(core)
    item, _ = candidate(core)
    token = Auth(core.db, core.settings).issue_token('hermes-fixture', ['read', 'draft', 'sync'])
    server, thread, url = serve(core)

    async def check():
        params = StdioServerParameters(command=sys.executable, args=['-m', 'academic_os.mcp', '--base-url', url],
                                       env={**os.environ, 'ACADEMIC_OS_TOKEN': token, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')})
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {'mail_feed', 'mail_digest', 'list_tags', 'set_item_tags'} <= names
            assert 'confirm_draft' not in names

            async def call(name, args):
                result = await session.call_tool(name, args)
                assert not result.isError
                return json.loads(result.content[0].text) if result.content else result.structuredContent['result']

            tag = await call('create_tag', {'name': 'Course label', 'kind': 'course', 'scope': 'fall', 'idempotency_key': 'tag'})
            result = await call('set_item_tags', {'item_id': item['id'], 'tag_ids': [tag['id']], 'expected_version': item['version'], 'idempotency_key': 'tag-item'})
            assert result['tags'][0]['id'] == tag['id']
            page = await call('mail_digest', {})
            assert page['items'][0]['body_excerpt'] == 'Original body'
            page = await call('mail_feed', {})
            assert page['items'][0]['key'] == 'school:m1'
            await call('acknowledge_mail_feed', {'consumer': 'hermes', 'cursor': page['next_cursor'], 'expected_cursor': 0})
            assert (await call('mail_feed', {}))['items'] == []
            assert (await call('list_items', {'view': 'formal'})) == []
    try:
        asyncio.run(check())
    finally:
        server.should_exit = True
        thread.join(10)

