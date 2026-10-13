"""Explicit Google-only proxy selection; Moodle remains a direct connection."""
from __future__ import annotations

import os
import sys
import threading
import time
from urllib.parse import urlparse

import httpx

_routes = {}
_route_lock = threading.Lock()
PROBE_URL = 'https://accounts.google.com/.well-known/openid-configuration'


def google_candidates(proxy=None, mode='auto'):
    if mode == 'direct':
        return [None]
    if mode == 'manual':
        return [proxy]
    values = [windows_google_proxy(), parse_system_proxy(os.environ.get('HTTPS_PROXY')), parse_system_proxy(os.environ.get('https_proxy')), proxy, None]
    return list(dict.fromkeys(value for value in values if value)) + [None]


def select_google_route(candidates, exclude=()):
    """Probe without credentials; never replay user mutations to test a route."""
    key = tuple(candidates)
    with _route_lock:
        cached = _routes.get(key)
        if cached and cached[0] > time.monotonic() and cached[1] not in exclude:
            return cached[1]
        last = None
        for route in candidates:
            if route in exclude:
                continue
            try:
                with httpx.Client(proxy=route, trust_env=False, timeout=httpx.Timeout(6, connect=3)) as probe:
                    response = probe.get(PROBE_URL)
                    response.raise_for_status()
                _routes[key] = (time.monotonic() + 300, route)
                return route
            except httpx.HTTPError as exc:
                last = exc
        _routes.pop(key, None)
        raise httpx.ConnectError('Google network unavailable') from last


class GoogleHTTP:
    """HTTPX adapter with automatic, live system-proxy selection."""
    def __init__(self, proxy=None, mode='auto', timeout=None):
        self.proxy, self.mode = proxy, mode
        self.timeout = timeout or httpx.Timeout(20, connect=5)
        self.client, self.route = None, object()

    def _client(self, route):
        if self.client is None or self.route != route:
            self.close()
            self.client = httpx.Client(proxy=route, trust_env=False, timeout=self.timeout)
            self.route = route
        return self.client

    def request(self, method, url, **kwargs):
        candidates = google_candidates(self.proxy, self.mode)
        route = select_google_route(candidates) if self.mode == 'auto' else candidates[0]
        try:
            return self._client(route).request(method, url, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError):
            # Only failures before sending a request are safe for automatic replay.
            if self.mode != 'auto':
                raise
            with _route_lock:
                _routes.pop(tuple(candidates), None)
            route = select_google_route(candidates, exclude=(route,))
            return self._client(route).request(method, url, **kwargs)

    def get(self, url, **kwargs):
        return self.request('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self.request('POST', url, **kwargs)

    def close(self):
        if self.client is not None:
            self.client.close()
            self.client = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def parse_system_proxy(value):
    if not isinstance(value, str) or not value.strip():
        return None
    if '=' in value:
        protocols = dict(part.split('=', 1) for part in value.split(';') if '=' in part)
        value = protocols.get('https') or protocols.get('http') or ''
    value = value.strip()
    if not value:
        return None
    if '://' not in value:
        value = 'http://' + value
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'} or port == 0:
        return None
    return value.rstrip('/')


def windows_google_proxy():
    if sys.platform != 'win32':
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
            if not winreg.QueryValueEx(key, 'ProxyEnable')[0]:
                return None
            return parse_system_proxy(winreg.QueryValueEx(key, 'ProxyServer')[0])
    except OSError:
        return None


def connection_error_message(error):
    if isinstance(error, httpx.TimeoutException):
        return '连接超时。程序会自动检测 Google 代理与网络，网络恢复后可重新登录；学校服务请稍后重试。'
    if isinstance(error, (httpx.ConnectError, httpx.ProxyError)):
        return '暂时无法连接服务，已尝试可用网络。请确保电脑能访问 Google 或学校网站，然后点击重试。'
    status = getattr(error, 'status', None)
    if status == 403:
        return '服务拒绝访问（HTTP 403）。可能是应用发布配置、授权范围或学校服务权限限制；请查看维护记录。'
    if status in {400, 401}:
        return '授权无效或已过期，请返回后台重新发起登录。'
    return '连接或授权验证失败。请检查网络、Google 代理或学校登录权限，再重新登录。'
