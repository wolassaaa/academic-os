"""Hermes cron pre-run gate for Academic OS (stdlib only; copy to ~/.hermes/scripts/).

Prints a final line {"wakeAgent": true|false, "context": ...}. The agent wakes only when there is
new collected mail, a new Google authorization problem, or Academic OS became unreachable (at most
once per UNREACHABLE_REMIND_HOURS). Quiet ticks cost no model tokens.

Environment: ACADEMIC_OS_MCP_URL (e.g. https://host.tailnet.ts.net/mcp) and ACADEMIC_OS_TOKEN.
Both may instead live in ~/.hermes/academic-os.json as {"url": ..., "token": ...}.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UNREACHABLE_REMIND_HOURS = 6
BACKLOG_RETRY_MINUTES = 30
HOME = Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes'))
STATE = Path(os.environ.get('ACADEMIC_OS_WAKE_STATE', HOME / 'scripts' / '.academic-os-wake.json'))


def settings():
    url, token = os.environ.get('ACADEMIC_OS_MCP_URL'), os.environ.get('ACADEMIC_OS_TOKEN')
    stored = HOME / 'academic-os.json'
    if (not url or not token) and stored.exists():
        data = json.loads(stored.read_text(encoding='utf-8'))
        url, token = url or data.get('url'), token or data.get('token')
    if not url or not token:
        raise SystemExit('ACADEMIC_OS_MCP_URL and ACADEMIC_OS_TOKEN are required')
    return url, token


def call_tool(url, token, name, arguments, timeout=20):
    # Stateless gateway: a single tools/call needs no initialize round trip.
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}}).encode()
    request = urllib.request.Request(url, data=body, method='POST', headers={
        'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-06-18'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        reply = json.loads(response.read().decode('utf-8'))
    if 'error' in reply:
        raise RuntimeError(reply['error'].get('message', 'mcp_error'))
    result = reply['result']
    if result.get('isError'):
        raise RuntimeError(result['content'][0].get('text', 'tool_error') if result.get('content') else 'tool_error')
    if result.get('structuredContent') is not None:
        payload = result['structuredContent']
        return payload.get('result', payload)
    return json.loads(result['content'][0]['text'])


def load_state():
    try:
        return json.loads(STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state), encoding='utf-8')


def decide(url, token, state, now=None):
    now = time.time() if now is None else now
    try:
        page = call_tool(url, token, 'mail_feed', {'consumer': 'hermes', 'limit': 1, 'body_chars': 1})
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError, ValueError, KeyError) as exc:
        reason = f'HTTP {exc.code}' if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
        last = state.get('unreachable_notified')
        if last is None or now - last >= UNREACHABLE_REMIND_HOURS * 3600:
            state['unreachable_notified'] = now
            return True, f'Academic OS 不可达（{reason}）。请检查电脑是否开机、Academic OS 是否运行、Tailscale 是否在线、令牌是否被撤销。'
        return False, None
    state.pop('unreachable_notified', None)
    if page.get('error'):
        last = state.get('error_notified')
        if last is None or now - last >= UNREACHABLE_REMIND_HOURS * 3600:
            state['error_notified'] = now
            return True, f"Academic OS 返回错误：{page['error']}（HTTP {page.get('http_status')}），请检查令牌权限。"
        return False, None
    state.pop('error_notified', None)
    reasons = []
    if page.get('items'):
        # Wake for newly arrived mail; an unchanged unacknowledged backlog (a run still working, or failing
        # before ack) is retried only every BACKLOG_RETRY_MINUTES so a broken agent does not burn tokens every tick.
        fresh = page['high_water'] != state.get('woke_high_water')
        if fresh or now - state.get('woke_at', 0) >= BACKLOG_RETRY_MINUTES * 60:
            reasons.append(f"有新采集的邮件（未确认至少 {page['high_water'] - page['cursor']} 封）" if fresh else f"仍有 {page['high_water'] - page['cursor']} 封邮件未确认，重试处理")
            state.update(woke_high_water=page['high_water'], woke_at=now)
    announced = set(state.get('announced', []))
    current = set()
    for account in (page.get('health') or {}).get('authorizations', []):
        if account['state'] in {'reauthorization_required', 'expiring'}:
            marker = f"{account['account_id']}:{account['state']}:{account.get('incident') or account.get('authorized_at')}"
            current.add(marker)
            if marker not in announced:
                label = account.get('email') or account['account_id']
                reasons.append(f'{label} 需要重新授权' if account['state'] == 'reauthorization_required' else f"{label} 的 Google 授权预计 {account['expires_estimate']} 到期")
    state['announced'] = sorted(current)
    return bool(reasons), '；'.join(reasons) or None


def main():
    url, token = settings()
    state = load_state()
    wake, context = decide(url, token, state)
    save_state(state)
    # ASCII-escaped JSON: safe whatever console code page Hermes runs the script under.
    print(json.dumps({'wakeAgent': wake, **({'context': context} if context else {})}))


if __name__ == '__main__':
    sys.exit(main())
