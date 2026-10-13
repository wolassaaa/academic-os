"""Owner-only account onboarding. Secrets stay in Windows Credential Manager.

OAuth flows deliberately expire on process restart; a login can simply be retried.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse, unquote

from .core import utcnow
from .providers import Google
from .security import SecretStore, token_hash
from .network import GoogleHTTP
from .vendor.moodle_client import MoodleClient

SCOPES = 'https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/calendar.calendarlist.readonly https://www.googleapis.com/auth/calendar.events'


class Connections:
    def __init__(self, core, config_path=None, secret_store=None):
        self.core = core
        self.path = Path(config_path or 'config.json').resolve()
        self.secrets = secret_store or SecretStore()
        self.lock = threading.RLock()
        self.flows = {}
        self.generation = {'google': 0, 'moodle': 0}
        bundled = Path(__file__).with_name('oauth-client.json')
        if not self.has_secret('google-oauth-client') and bundled.exists():
            self.configure_client(bundled.read_text(encoding='utf-8'))

    def save(self, change):
        with self.core.configuration_lock:
            candidate = copy.deepcopy(self.core.settings)
            change(candidate)
            candidate.__post_init__()
            candidate.save(self.path)
            # Keep the Settings instance used by Auth and workers alive.
            self.core.settings.__dict__.update(candidate.__dict__)
            with self.core.db.transaction() as db:
                self.core.audit(db, 'owner', 'connections_updated', 'connections', {})
                self.core._enqueue(db, 'sources_sync', {}, 'connections:' + secrets.token_hex(12))
                self.core._enqueue(db, 'calendar_sync', {}, 'connections-calendar:' + secrets.token_hex(12))

    def has_secret(self, name):
        try:
            return bool(self.secrets.get(name))
        except RuntimeError:
            return False

    def summary(self):
        with self.lock:
            status = [{'kind': f['kind'], 'status': f.get('status', 'waiting'), 'expires_at': f['expires']} for f in self.flows.values() if f['expires'] > time.time()]
        return {'google_client_configured': self.has_secret('google-oauth-client'),
                'google_proxy': self.core.settings.google_proxy,
                'google_network_mode': self.core.settings.google_network_mode,
                'google_accounts': [{k: a[k] for k in ('id', 'email', 'calendars', 'gmail', 'last_checked') if k in a} for a in self.core.settings.google_accounts],
                'moodle': {k: v for k, v in self.core.settings.moodle.items() if k != 'secret_ref'},
                'flows': status}

    def flow_status(self, session):
        session_hash = token_hash(session)
        with self.lock:
            return {'flows': [{'id': state, 'kind': flow['kind'], 'status': flow.get('status', 'waiting') if flow['expires'] > time.time() else 'expired', 'message': flow.get('message', '')} for state, flow in self.flows.items() if flow['session_hash'] == session_hash]}

    def fail_flow(self, state, message):
        with self.lock:
            flow = self.flows.get(state)
            if flow and flow.get('status') not in {'done', 'processing'}:
                flow['status'], flow['message'] = 'failed', message

    def configure_client(self, raw):
        try:
            data = json.loads(raw)['installed']
            credential = {k: data[k] for k in ('client_id', 'client_secret')}
            if not all(isinstance(v, str) and v for v in credential.values()):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise ValueError('请上传 Google OAuth 桌面应用客户端 JSON（installed 类型）。') from None
        self.secrets.set('google-oauth-client', json.dumps(credential))
        return {'ok': True}

    def new_flow(self, kind, session, **extra):
        state = secrets.token_urlsafe(32)
        with self.lock:
            self.flows = {k: v for k, v in self.flows.items() if v['expires'] > time.time()}
            if len(self.flows) >= 20:
                raise ValueError('授权请求过多，请稍后重试。')
            self.flows[state] = {'kind': kind, 'session_hash': token_hash(session), 'expires': time.time() + 600, 'generation': self.generation[kind], **extra}
        return state, self.flows[state]

    def validate_flow(self, flow):
        if flow['expires'] < time.time() or flow['generation'] != self.generation[flow['kind']]:
            raise ValueError('连接设置已变更，请重新发起授权。')
        with self.core.db.connect() as db:
            row = db.execute('SELECT * FROM sessions WHERE token_hash=? AND remote=0', (flow['session_hash'],)).fetchone()
        if not row or row['expires_at'] < time.time():
            raise ValueError('后台登录已失效，请重新登录后台。')

    def claim(self, state, kind):
        with self.lock:
            flow = self.flows.get(state)
            if not flow or flow['kind'] != kind or flow['expires'] < time.time() or flow.get('status'):
                raise ValueError('授权已失效或已使用，请重新登录。')
            self.validate_flow(flow)
            flow['status'] = 'processing'
            return flow

    def begin_google(self, session, account_id=None, force_consent=False):
        client = json.loads(self.secrets.get('google-oauth-client'))
        account = self.account(account_id) if account_id else None
        verifier = secrets.token_urlsafe(48)
        state, flow = self.new_flow('google', session, client=client, verifier=verifier, account_id=account_id)
        flow['redirect'] = f'http://127.0.0.1:{self.core.settings.port}/api/v1/connections/google/callback'
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        parameters = {'client_id': client['client_id'], 'redirect_uri': flow['redirect'], 'response_type': 'code', 'scope': SCOPES, 'access_type': 'offline', 'prompt': 'consent select_account' if force_consent or not account else 'select_account', 'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'}
        if account:
            parameters['login_hint'] = account.get('email', '')
        return {'url': 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(parameters), 'flow_id': state}

    def exchange_google(self, flow, code):
        with GoogleHTTP(proxy=self.core.settings.google_proxy, mode=self.core.settings.google_network_mode, timeout=20) as http:
            response = http.post('https://oauth2.googleapis.com/token', data={**flow['client'], 'code': code, 'code_verifier': flow['verifier'], 'redirect_uri': flow['redirect'], 'grant_type': 'authorization_code'})
            if response.status_code != 200:
                raise ValueError('Google 授权交换失败，请检查网络、客户端设置并重新授权。')
            value = response.json()
            if not value.get('refresh_token') and flow.get('account_id'):
                account = self.account(flow['account_id'])
                old = json.loads(self.secrets.get(account['secret_ref']))
                # An old refresh token identifies its original account, not this login.
                # Verify the new authorization-code token before reusing it.
                if not value.get('access_token') or old.get('client_id') != flow['client']['client_id']:
                    raise ValueError('请点击“修复授权”重新登录账号。')
                identity = http.get('https://gmail.googleapis.com/gmail/v1/users/me/profile', headers={'Authorization': 'Bearer ' + value['access_token']})
                if identity.status_code != 200 or identity.json().get('emailAddress', '').casefold() != account.get('email', '').casefold():
                    raise ValueError('登录的账号与所选账号不同，请重新登录正确账号。')
                value['refresh_token'] = old.get('refresh_token')
        if value.get('scope') and not set(SCOPES.split()) <= set(value['scope'].split()):
            raise ValueError('Google 权限未完整授予，请重新登录并勾选邮箱、通知发送和日历权限。')
        if not value.get('refresh_token'):
            raise ValueError('Google 未返回离线授权，请点击“修复授权”重新登录。')
        return {**flow['client'], 'refresh_token': value['refresh_token']}

    def probe_google(self, credential):
        class TemporarySecrets:
            def get(self, name):
                return json.dumps(credential)
        provider = Google(TemporarySecrets(), 'temporary', self.core.settings.google_proxy, network_mode=self.core.settings.google_network_mode)
        try:
            profile = provider.request('GET', '/gmail/v1/users/me/profile')
            calendars, page, seen = [], None, set()
            for _ in range(100):
                result = provider.request('GET', '/calendar/v3/users/me/calendarList', params={'maxResults': 250, **({'pageToken': page} if page else {})})
                calendars.extend({k: c[k] for k in ('id', 'summary', 'accessRole', 'primary', 'timeZone') if k in c} for c in result.get('items', []))
                page = result.get('nextPageToken')
                if not page:
                    return {'email': profile['emailAddress'], 'calendars': calendars}
                if page in seen:
                    break
                seen.add(page)
            raise ValueError('Google 日历分页未完成，请重试。')
        finally:
            provider.http.close()

    def finish_google(self, state, code):
        flow = self.claim(state, 'google')
        try:
            credential = self.exchange_google(flow, code)
            profile = self.probe_google(credential)
            requested = flow.get('account_id')
            if requested and self.account(requested).get('email', '').casefold() != profile['email'].casefold():
                raise ValueError('登录的账号与所选账号不同，请使用添加账号。')
            with self.lock:
                self.validate_flow(flow)
                self.adopt_google(credential, profile, requested)
            flow['status'] = 'done'
        except Exception:
            flow['status'] = 'failed'
            flow['message'] = '连接验证未完成，请重新登录；仍失败时使用修复授权。'
            raise
        finally:
            flow.pop('client', None)
            flow.pop('verifier', None)
        return {'ok': True}

    def adopt_google(self, credential, profile, requested=None):
        email = profile['email']
        existing = next((a for a in self.core.settings.google_accounts if a.get('email', '').casefold() == email.casefold()), None)
        account_id = requested or (existing['id'] if existing else 'google-' + hashlib.sha256(email.casefold().encode()).hexdigest()[:16])
        ref = 'account-' + account_id
        self.secrets.set(ref, json.dumps(credential))
        def update(settings):
            account = next((a for a in settings.google_accounts if a['id'] == account_id), None)
            if account is None:
                account = {'id': account_id, 'calendars': [], 'gmail': True}
                settings.google_accounts.append(account)
            account.update(secret_ref=ref, email=email, last_checked=utcnow())
        self.save(update)
        return account_id

    def account(self, account_id):
        account = next((a for a in self.core.settings.google_accounts if a['id'] == account_id), None)
        if account is None:
            raise KeyError(account_id)
        return account

    def check_google(self, account_id):
        account = self.account(account_id)
        result = self.probe_google(json.loads(self.secrets.get(account['secret_ref'])))
        self.core.source_health('google:' + account_id, True)
        return result

    def select_calendars(self, account_id, calendars):
        generation = self.generation['google']
        profile = self.check_google(account_id)
        available = {c['id']: c for c in profile['calendars']}
        if len(calendars) > 100 or len({c['id'] for c in calendars}) != len(calendars):
            raise ValueError('日历选择重复或过多。')
        selected = []
        for c in calendars:
            if c['id'] not in available or (c.get('write') and available[c['id']].get('accessRole') not in {'owner', 'writer'}):
                raise ValueError('该日历不存在或没有写入权限。')
            selected.append({'id': c['id'], 'name': available[c['id']].get('summary', c['id']), 'read': bool(c.get('read', True)), 'write': bool(c.get('write', False))})
        def update(settings):
            if generation != self.generation['google']:
                raise ValueError('账号连接已变更，请重新选择日历。')
            target = next((('google:' + account_id, c['id']) for c in selected if c['write']), None)
            if target:
                with self.core.db.connect() as db:
                    mappings = db.execute("SELECT provider,calendar_id FROM calendar_mappings WHERE provider LIKE 'google:%' AND COALESCE(json_extract(document,'$.deleted'),0)=0 AND COALESCE(json_extract(document,'$.purpose'),'execution')!='legacy_deadline'").fetchall()
                if any((row['provider'], row['calendar_id']) != target for row in mappings):
                    raise ValueError('已有执行日程映射，请先迁移或清理旧日程再切换写入日历。')
            account = next(a for a in settings.google_accounts if a['id'] == account_id)
            account['calendars'] = selected
            account['last_checked'] = utcnow()
        with self.lock:
            self.save(update)
        return {'ok': True}

    @staticmethod
    def site_url(value):
        parsed = urlparse(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Moodle 地址必须是完整的 HTTPS 学校站点地址。')
        return value.rstrip('/')

    def probe_moodle(self, site, token):
        client = MoodleClient(site, token)
        info = client.call('core_webservice_get_site_info')
        courses = client.call('core_enrol_get_users_courses', {'userid': info['userid']})
        return {'site_name': info.get('sitename', ''), 'username': info.get('username', ''), 'courses': len(courses)}

    def connect_moodle(self, site, token, flow=None):
        with self.lock:
            generation = self.generation['moodle']
        site = self.site_url(site)
        info = self.probe_moodle(site, token)
        with self.lock:
            if generation != self.generation['moodle']:
                raise ValueError('学校连接已被断开，请重新发起。')
            if flow:
                self.validate_flow(flow)
            ref = 'moodle-' + secrets.token_hex(12)
            self.secrets.set(ref, json.dumps({'site_url': site, 'token': token}))
            self.save(lambda s: setattr(s, 'moodle', {'enabled': True, 'secret_ref': ref, 'site_url': site, **info, 'last_checked': utcnow()}))
        return {'ok': True, **info}

    def register_moodle_scheme(self):
        if sys.platform != 'win32':
            raise ValueError('学校浏览器登录目前需要 Windows 版启动器。')
        import winreg
        command = [sys.executable]
        if not getattr(sys, 'frozen', False):
            command += ['-m', 'academic_os.portable']
        command += ['--moodle-callback', '%1', '--config', str(self.path)]
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Classes\academicos') as key:
            winreg.SetValueEx(key, '', 0, winreg.REG_SZ, 'URL:Academic OS Moodle Login')
            winreg.SetValueEx(key, 'URL Protocol', 0, winreg.REG_SZ, '')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Classes\academicos\shell\open\command') as key:
            winreg.SetValueEx(key, '', 0, winreg.REG_SZ, subprocess.list2cmdline(command))

    def begin_moodle(self, session, site):
        site = self.site_url(site or 'https://buelearning.hkbu.edu.hk')
        self.register_moodle_scheme()
        passport = secrets.token_urlsafe(32)
        state, _ = self.new_flow('moodle', session, site=site, fingerprint=hashlib.md5((site + passport).encode()).hexdigest())
        return {'url': site + '/admin/tool/mobile/launch.php?' + urlencode({'service': 'moodle_mobile_app', 'passport': passport, 'urlscheme': 'academicos'}), 'flow_id': state}

    def finish_moodle(self, uri):
        if not isinstance(uri, str) or len(uri) > 8192 or not uri.startswith('academicos://token='):
            raise ValueError('无效的学校登录回调。')
        try:
            payload = unquote(uri.split('=', 1)[1])
            # Windows may append a URL path slash after the Base64 authority.
            # Only remove an extra character; a valid Base64 trailing '/' stays.
            if len(payload) % 4 == 1 and payload.endswith('/'):
                payload = payload[:-1]
            decoded = base64.b64decode(payload, validate=True).decode()
            fingerprint, token, *_ = decoded.split(':::')
            if not token:
                raise ValueError()
        except (ValueError, UnicodeError):
            raise ValueError('无效的学校登录回调。') from None
        with self.lock:
            state = next((k for k, f in self.flows.items() if f['kind'] == 'moodle' and hmac.compare_digest(f['fingerprint'], fingerprint)), None)
        flow = self.claim(state, 'moodle')
        try:
            result = self.connect_moodle(flow['site'], token, flow)
            flow['status'] = 'done'
            return result
        except Exception:
            flow['status'] = 'failed'
            raise

    def disconnect(self, kind, account_id=None):
        with self.lock:
            if kind == 'google':
                self.account(account_id)
                self.save(lambda s: setattr(s, 'google_accounts', [a for a in s.google_accounts if a['id'] != account_id]))
            elif kind == 'moodle':
                self.save(lambda s: setattr(s, 'moodle', {}))
            else:
                raise ValueError('未知连接类型。')
            self.generation[kind] += 1
        return {'ok': True}

    def import_existing(self):
        """Known local legacy credentials only; never accept arbitrary file paths."""
        import keyring
        root = Path(r'D:\AgentNode\Core\Hermes')
        results = []
        for path in sorted(root.glob('google_token*.json')):
            try:
                if path.is_symlink():
                    continue
                raw = json.loads(path.read_text(encoding='utf-8-sig'))
                credential = {k: raw[k] for k in ('client_id', 'client_secret', 'refresh_token')}
                profile = self.probe_google(credential)
                self.adopt_google(credential, profile)
                if not self.has_secret('google-oauth-client'):
                    self.secrets.set('google-oauth-client', json.dumps({k: credential[k] for k in ('client_id', 'client_secret')}))
                results.append({'kind': 'google', 'ok': True, 'email': profile['email']})
            except Exception:
                results.append({'kind': 'google', 'ok': False, 'error': '已有授权不可用，请检查 Google 代理或重新登录。'})
        try:
            site = keyring.get_password('academic-os', 'moodle-site-url')
            token = keyring.get_password('academic-os', 'moodle-token')
            if site and token:
                self.connect_moodle(site, token)
                results.append({'kind': 'moodle', 'ok': True})
        except Exception:
            results.append({'kind': 'moodle', 'ok': False, 'error': '已有学校授权不可用，请重新登录。'})
        return {'results': results}
