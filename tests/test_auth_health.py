"""Token health is visible and pushed: reauthorization codes survive, Testing-mode age is tracked, alerts fire once."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database


def hub(tmp_path, **overrides):
    settings = Settings(data_dir=tmp_path, secure_cookies=False, **overrides)
    return Core(Database(settings.database_path), settings)


def ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def account(authorized_days=None, **extra):
    value = {'id': 'acc', 'secret_ref': 'account-acc', 'email': 'student@example.test', 'calendars': [{'id': 'primary', 'read': True}], 'gmail': True, **extra}
    if authorized_days is not None:
        value['authorized_at'] = ago(authorized_days)
    return value


def authorization(core):
    states = core.health()['authorizations']
    assert [a['account_id'] for a in states] == ['acc']
    return states[0]


def test_publishing_mode_is_validated(tmp_path):
    assert Settings(data_dir=tmp_path).google_oauth_publishing == 'testing'
    with pytest.raises(ValueError):
        Settings(data_dir=tmp_path, google_oauth_publishing='beta')


def test_worker_keeps_reauthorization_code_instead_of_class_name(tmp_path, monkeypatch):
    from academic_os import worker as module
    from academic_os.providers import ProviderError
    core = hub(tmp_path, google_accounts=[account()])
    def expired(*args, **kwargs):
        raise ProviderError(400, 'google_reauthorization_required')
    monkeypatch.setattr(module, 'collect_gmail', expired)
    module.Worker(core, handlers={}).collect({'acc': object()})
    source = next(s for s in core.health()['sources'] if s['id'] == 'gmail:acc')
    assert source['error'] == 'google_reauthorization_required'


def test_authorization_states(tmp_path):
    core = hub(tmp_path, google_accounts=[account(1)])
    state = authorization(core)
    assert state['state'] == 'ok' and state['publishing'] == 'testing'
    assert state['expires_estimate'] == (datetime.fromisoformat(state['authorized_at']) + timedelta(days=7)).isoformat()

    core = hub(tmp_path / 'b', google_accounts=[account(5.5)])
    assert authorization(core)['state'] == 'expiring'

    core = hub(tmp_path / 'c', google_accounts=[account()])
    assert authorization(core)['state'] == 'unknown_age'

    core = hub(tmp_path / 'd', google_accounts=[account(30)], google_oauth_publishing='production')
    state = authorization(core)
    assert state['state'] == 'ok' and state['expires_estimate'] is None

    core = hub(tmp_path / 'e', google_accounts=[account(1)])
    core.source_health('calendar:google:acc:primary', False, 'google_reauthorization_required')
    state = authorization(core)
    assert state['state'] == 'reauthorization_required'
    assert state['failing_sources'] == ['calendar:google:acc:primary']


def queued(core, prefix):
    with core.db.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM notifications WHERE unique_key LIKE ? ORDER BY created_at", (prefix + '%',))]


def test_reauthorization_alert_fires_once_then_recovery_once(tmp_path):
    from academic_os.notifications import Notifications
    core = hub(tmp_path, google_accounts=[account(1)], feishu={'chat_id': 'chat'})
    notices = Notifications(core)
    core.source_health('gmail:acc', True)
    core.source_health('gmail:acc', False, 'google_reauthorization_required')
    notices.health_alerts()
    notices.health_alerts()
    alerts = queued(core, 'auth-required:')
    assert {a['channel'] for a in alerts} == {'workbench', 'feishu'}
    assert len(alerts) == 2
    assert '重新授权' in json.loads(alerts[0]['document'])['title']

    core.source_health('gmail:acc', True)
    notices.health_alerts()
    notices.health_alerts()
    assert len(queued(core, 'auth-recovered:')) == 2
    # A later, separate outage is a new incident.
    core.source_health('gmail:acc', False, 'google_reauthorization_required')
    notices.health_alerts()
    assert len(queued(core, 'auth-required:')) == 4


def test_expiry_warning_fires_once_per_authorization(tmp_path):
    from academic_os.notifications import Notifications
    core = hub(tmp_path, google_accounts=[account(5.5)])
    notices = Notifications(core)
    notices.health_alerts()
    notices.health_alerts()
    warnings = queued(core, 'auth-expiring:')
    assert len(warnings) == 1 and warnings[0]['channel'] == 'workbench'
    assert '7 天' in json.loads(warnings[0]['document'])['body']


def test_alerts_never_use_the_failing_mail_account_for_email(tmp_path):
    from academic_os.notifications import Notifications
    core = hub(tmp_path, google_accounts=[account(1)], notifications={'email_recipients': ['me@example.test'], 'google_account': 'acc'})
    core.source_health('gmail:acc', False, 'google_reauthorization_required')
    Notifications(core).health_alerts()
    assert 'email' not in {a['channel'] for a in queued(core, 'auth-required:')}


def test_adopt_google_records_authorized_at_only_for_a_new_refresh_token(tmp_path):
    from academic_os.connections import Connections
    from test_connections import MemorySecrets
    core = hub(tmp_path)
    service = Connections(core, tmp_path / 'config.json', MemorySecrets())
    profile = {'email': 'student@example.test', 'calendars': []}
    first = service.adopt_google({'client_id': 'c', 'client_secret': 's', 'refresh_token': 'r1'}, profile)
    stamp = service.account(first)['authorized_at']
    service.adopt_google({'client_id': 'c', 'client_secret': 's', 'refresh_token': 'r1'}, profile, first)
    assert service.account(first)['authorized_at'] == stamp
    service.core.settings.google_accounts[0]['authorized_at'] = ago(3)
    service.adopt_google({'client_id': 'c', 'client_secret': 's', 'refresh_token': 'r2'}, profile, first)
    assert datetime.fromisoformat(service.account(first)['authorized_at']) > datetime.now(timezone.utc) - timedelta(minutes=1)
