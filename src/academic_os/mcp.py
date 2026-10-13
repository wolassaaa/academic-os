"""Official MCP stdio adapter; every tool calls the local business HTTP API."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from mcp.server.fastmcp import FastMCP


class APIProxy:
    def __init__(self, base_url, token, transport=None):
        parsed = urlsplit(base_url)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('MCP target must be the local http://127.0.0.1:PORT management API')
        if not token:
            raise ValueError('An agent token is required')
        self.client = httpx.Client(base_url=base_url.rstrip('/') + '/api/v1/', headers={'Authorization': 'Bearer ' + token}, timeout=30, follow_redirects=False, trust_env=False, transport=transport)

    def request(self, method, path, body=None, key=None):
        headers = {'Idempotency-Key': key} if key else {}
        try:
            response = self.client.request(method, path.lstrip('/'), json=body, headers=headers)
            if response.status_code >= 300:
                return {'error': 'business_api_error', 'http_status': response.status_code, 'detail': response.json() if response.headers.get('content-type', '').startswith('application/json') else None}
            return response.json() if response.headers.get('content-type', '').startswith('application/json') else {'text': response.text}
        except httpx.HTTPError:
            return {'error': 'management_api_unreachable'}


def build_server(proxy, allow_confirm=False):
    server = FastMCP('Academic OS', instructions='Local Academic OS business API. Never access SQLite or providers directly. Keep Idempotency-Key stable across retries. Scheduling changes require a draft and explicit owner confirmation.', log_level='WARNING')

    @server.tool()
    def health() -> dict:
        """Check worker heartbeat, source freshness and shadow/live mode."""
        return proxy.request('GET', '/health')

    @server.tool()
    def list_items() -> list | dict:
        """Read academic items with evidence, status, versions and deadlines."""
        return proxy.request('GET', '/items')

    @server.tool()
    def get_item(item_id: str) -> dict:
        """Read one academic item including source evidence and current version."""
        return proxy.request('GET', '/items/' + quote(item_id, safe=''))

    @server.tool()
    def list_artifacts() -> list | dict:
        """Read imported grade, timetable and academic document summaries."""
        return proxy.request('GET', '/artifacts')

    @server.tool()
    def list_notifications() -> list | dict:
        """Read reminders, delivery state and receipts; does not send messages."""
        return proxy.request('GET', '/notifications')

    @server.tool()
    def calendar_events() -> list | dict:
        """Read cached external calendar events for availability and review."""
        return proxy.request('GET', '/calendar-events')

    @server.tool()
    def model_spec() -> dict:
        """Read the reserved model contract. Model analysis is disabled."""
        return proxy.request('GET', '/model/spec')

    @server.tool()
    def list_mail(limit: int = 100, offset: int = 0, query: str = '') -> list | dict:
        """Read paginated cached mail headers. Use get_mail for original body; model analysis is off."""
        from urllib.parse import urlencode
        return proxy.request('GET', '/mail?' + urlencode({'limit': limit, 'offset': offset, 'q': query}))

    @server.tool()
    def get_mail(mail_key: str) -> dict:
        """Read one cached original mail by its stable account-qualified key."""
        return proxy.request('GET', '/mail/' + quote(mail_key, safe=''))

    @server.tool()
    def list_calendars() -> list | dict:
        """Read configured calendar summaries."""
        return proxy.request('GET', '/calendars')

    @server.tool()
    def list_jobs() -> list | dict:
        """Read durable sync, export and calendar operation state."""
        return proxy.request('GET', '/jobs')

    @server.tool()
    def list_drafts() -> list | dict:
        """Read schedule drafts for owner review."""
        return proxy.request('GET', '/drafts')

    @server.tool()
    def list_conflicts() -> list | dict:
        """Read open conflicts; do not silently resolve them."""
        return proxy.request('GET', '/conflicts')

    @server.tool()
    def summary() -> dict:
        """Get the human-readable current summary."""
        return proxy.request('GET', '/summary')

    @server.tool()
    def create_item(title: str, idempotency_key: str, course: str = '', kind: str = 'task', due_at: str | None = None, estimate_minutes: int | None = None, priority: int = 2) -> dict:
        """Create a manual academic item. Requires draft scope."""
        return proxy.request('POST', '/items', {'title': title, 'course': course, 'kind': kind, 'due_at': due_at, 'estimate_minutes': estimate_minutes, 'priority': priority}, idempotency_key)

    @server.tool()
    def create_draft(changes: list[dict], idempotency_key: str) -> dict:
        """Propose schedule changes: item_id, expected_version, start and end with timezone. Does not confirm."""
        return proxy.request('POST', '/drafts', {'changes': changes}, idempotency_key)

    @server.tool()
    def generate_plan(idempotency_key: str, days: int = 7) -> dict:
        """Generate a conflict-aware schedule draft, leaving confirmation to the owner."""
        return proxy.request('POST', '/plans/generate', {'days': days}, idempotency_key)

    @server.tool()
    def sync(idempotency_key: str, kind: str = 'sources_sync') -> dict:
        """Queue sources_sync, calendar_sync or export. Requires sync scope."""
        return proxy.request('POST', '/sync', {'kind': kind}, idempotency_key)

    if allow_confirm:
        @server.tool()
        def confirm_draft(draft_id: str, idempotency_key: str) -> dict:
            """Confirm only an explicitly approved draft. Requires a separately granted confirm scope."""
            return proxy.request('POST', '/drafts/' + quote(draft_id, safe='') + '/confirm', {}, idempotency_key)
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default=os.environ.get('ACADEMIC_OS_URL'))
    default_root = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path.cwd()
    parser.add_argument('--config', type=Path, default=default_root / 'config.json')
    parser.add_argument('--token-ref', default=os.environ.get('ACADEMIC_OS_TOKEN_REF'))
    parser.add_argument('--allow-confirm', action='store_true')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)  # stderr; stdout belongs to MCP.
    token = os.environ.get('ACADEMIC_OS_TOKEN')
    if args.token_ref:
        from .security import SecretStore
        token = SecretStore().get(args.token_ref)
    base_url = args.base_url
    if not base_url:
        from .config import Settings
        port = Settings.load(args.config).port if args.config.exists() else 8765
        base_url = f'http://127.0.0.1:{port}'
    proxy = APIProxy(base_url, token)
    try:
        build_server(proxy, args.allow_confirm).run(transport='stdio')
    finally:
        proxy.client.close()


if __name__ == '__main__':
    main()
