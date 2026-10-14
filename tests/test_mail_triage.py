from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from academic_os.api import create_app
from academic_os.config import Settings
from academic_os.connections import Connections
from academic_os.core import Core, Conflict
from academic_os.db import Database, dumps
from academic_os.exports import write_summary
from academic_os.notifications import Notifications
from academic_os.planner import generate_plan
from academic_os.security import Auth
from test_connections import MemorySecrets


def hub(tmp_path):
    settings = Settings(data_dir=tmp_path, secure_cookies=False)
    core = Core(Database(settings.database_path), settings)
    return core


def test_concurrent_candidate_review_has_exactly_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    core = hub(tmp_path)
    mail(core)
    item, _ = candidate(core)
    barrier = Barrier(2)

    def review(action):
        barrier.wait(timeout=5)
        try:
            return core.review_mail_item(item['id'], action, item['version'], 'race-' + action)['mail_review']['state']
        except Conflict as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(review, ['confirm', 'ignore']))
    assert results.count('item_version_changed') == 1
    assert len(set(results) & {'confirmed', 'ignored'}) == 1
    assert core.get_item(item['id'])['mail_review']['revision'] == 1


def test_portable_diagnostic_exercises_offline_mail_triage(tmp_path):
    from academic_os.portable_check import diagnose_mail_triage
    assert 'mail_triage_reminder_gate' in diagnose_mail_triage(tmp_path)


def test_manual_item_stays_formal_after_mail_source_merge_and_restart(tmp_path):
    core = hub(tmp_path)
    manual = core.create_item({'title': 'Task', 'course': 'COMP', 'estimate_minutes': 30})
    record = {'source_id': 'm1:deadline', 'title': 'Task', 'course': 'COMP', 'review_state': 'needs_analysis'}
    merged = core.ingest('gmail:school', [record])[0]
    assert merged['id'] == manual['id']
    assert core.list_items(view='formal')[0]['id'] == manual['id']
    assert core.list_items(view='mail_pending') == []
    restarted = Core(core.db, core.settings)
    assert restarted.list_items(view='formal')[0]['id'] == manual['id']


@pytest.mark.parametrize('action', ['snooze', 'complete'])
def test_historical_candidate_notice_cannot_bypass_review(tmp_path, action):
    core = hub(tmp_path)
    mail(core)
    item, _ = candidate(core)
    notices = Notifications(core)
    with core.db.transaction() as db:
        db.execute("INSERT INTO notifications(id,item_id,channel,state,document,due_at,unique_key,created_at,receipt) VALUES(?,?,?,?,?,?,?,?,?)",
                   ('old', item['id'], 'email', 'delivered', dumps({'source_due_at': item['source_due_at']}), 0, 'old-key', '2026-10-13', 'history-receipt'))
    with pytest.raises(Conflict, match='mail_candidate_requires_review'):
        notices.action('old', action)
    assert core.get_item(item['id'])['status'] == 'pending'
    notice = notices.list()[0]
    assert notice['state'] == 'delivered' and notice['receipt'] == 'history-receipt'
    assert core.list_jobs() == []


def mail(core, mid='m1', account='school'):
    doc = {'id': mid, 'key': account + ':' + mid, 'account': account, 'subject': 'School notice',
           'from': 'school@example.test', 'body': 'Original body', 'received_at': '2026-10-13T00:00:00Z', 'date_nodes': []}
    with core.db.transaction() as db:
        db.execute('INSERT OR REPLACE INTO mail VALUES(?,?,?,?)', (doc['key'], account, dumps(doc), doc['received_at']))
    return doc


def candidate(core, mid='m1', role='deadline', due=True):
    date = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat() if due else None
    record = {'source_id': mid + ':' + role + ':2026-10-13:date', 'title': 'Candidate ' + role,
              'due_at': date, 'estimate_minutes': 30, 'review_state': 'needs_analysis',
              'evidence_text': 'Original candidate evidence'}
    return core.ingest('gmail:school', [record])[0], record


def test_mail_candidate_is_pending_and_formal_query_excludes_it(tmp_path):
    core = hub(tmp_path)
    doc = mail(core)
    item, _ = candidate(core)
    assert item['mail_review'] == {'state': 'pending', 'revision': 0}
    assert item['mail_keys'] == [doc['key']]
    assert core.list_items(view='formal') == []
    assert [x['id'] for x in core.list_items(view='mail_pending')] == [item['id']]
    assert len(core.list_items()) == 1


def test_confirm_preserves_id_is_idempotent_and_protects_user_fields(tmp_path):
    core = hub(tmp_path)
    mail(core)
    item, record = candidate(core)
    changes = {'title': 'Verified task', 'source_due_at': None, 'priority': 4}
    result = core.review_mail_item(item['id'], 'confirm', item['version'], 'confirm-1', changes)
    assert result['id'] == item['id'] and result['version'] == item['version'] + 1
    assert result['mail_review']['state'] == 'confirmed' and result['mail_review']['revision'] == 1
    assert result['review_state'] == 'needs_date' and result['source_due_at'] is None
    assert core.review_mail_item(item['id'], 'confirm', item['version'], 'confirm-1', changes) == result
    with pytest.raises(Conflict):
        core.review_mail_item(item['id'], 'confirm', result['version'], 'confirm-again', changes)
    incoming = {**record, 'title': 'Changed source title', 'due_at': '2026-12-01T10:00:00Z'}
    updated = core.ingest('gmail:school', [incoming])[0]
    assert updated['title'] == 'Verified task' and updated['source_due_at'] is None
    assert updated['mail_review'] == result['mail_review']
    assert updated['review_state'] == 'conflict' and core.list_conflicts()
    assert core.list_jobs() == []


def test_ignore_restore_and_two_candidates_are_independent(tmp_path):
    core = hub(tmp_path)
    mail(core)
    a, _ = candidate(core, role='registration')
    b, _ = candidate(core, role='deadline')
    ignored = core.review_mail_item(a['id'], 'ignore', a['version'], 'ignore-a')
    assert ignored['mail_review']['state'] == 'ignored'
    assert core.get_item(b['id'])['mail_review']['state'] == 'pending'
    assert len(core.get_mail('school:m1')['linked_items']) == 2
    restored = core.review_mail_item(a['id'], 'restore', ignored['version'], 'restore-a')
    assert restored['mail_review']['state'] == 'pending' and restored['id'] == a['id']
    assert core.get_mail('school:m1')['body'] == 'Original body'


def test_stale_version_mismatched_key_and_invalid_changes_rejected(tmp_path):
    core = hub(tmp_path)
    mail(core)
    item, _ = candidate(core)
    with pytest.raises(Conflict):
        core.review_mail_item(item['id'], 'confirm', 99, 'stale')
    with pytest.raises(ValueError):
        core.review_mail_item(item['id'], 'confirm', 1, 'invalid', {'title': ''})
    result = core.review_mail_item(item['id'], 'ignore', 1, 'one-key')
    with pytest.raises(Conflict):
        core.review_mail_item(item['id'], 'restore', result['version'], 'one-key')


def test_pending_and_ignored_have_no_deadline_notices_but_mail_alert_survives(tmp_path):
    core = hub(tmp_path)
    mail(core)
    item, _ = candidate(core)
    notices = Notifications(core)
    alert = notices.queue('workbench', {'title': 'New mail'}, 'mail-arrival')
    assert notices.generate()['considered'] == 0
    assert [n['id'] for n in notices.list()] == [alert]
    ignored = core.review_mail_item(item['id'], 'ignore', 1, 'ignore')
    assert notices.generate()['considered'] == 0
    restored = core.review_mail_item(item['id'], 'restore', ignored['version'], 'restore')
    confirmed = core.review_mail_item(item['id'], 'confirm', restored['version'], 'confirm')
    assert notices.generate()['considered'] == 1
    notices.generate()
    deadline = [n for n in notices.list() if n['item_id'] == item['id']]
    assert len(deadline) == 1
    assert deadline[0]['document']['mail_review_revision'] == confirmed['mail_review']['revision']


def test_old_due_notices_superseded_and_same_due_confirm_gets_fresh_key(tmp_path):
    core = hub(tmp_path)
    mail(core)
    item, _ = candidate(core)
    notices = Notifications(core)
    with core.db.transaction() as db:
        db.execute("INSERT INTO notifications(id,item_id,channel,state,document,due_at,unique_key,created_at) VALUES(?,?,?,'queued',?,0,?,?)",
                   ('old', item['id'], 'email', dumps({'title': 'Old due', 'source_due_at': item['source_due_at'], 'deadline_revision': 1}), 'old-key', item['created_at']))
    assert notices.dispatch_one(now=1) == 'old'
    assert next(n for n in notices.list() if n['id'] == 'old')['state'] == 'superseded'
    core.review_mail_item(item['id'], 'confirm', 1, 'confirm')
    assert notices.generate()['considered'] == 1
    assert any(n['id'] != 'old' and n['item_id'] == item['id'] for n in notices.list())


def test_formal_statistics_summary_and_planning_exclude_candidate(tmp_path):
    core = hub(tmp_path)
    mail(core)
    item, _ = candidate(core)
    manual = core.create_item({'title': 'Manual task', 'estimate_minutes': 30})
    text = write_summary(core).read_text(encoding='utf-8')
    assert item['id'] not in text and manual['id'] in text
    result = generate_plan(core.list_items(), [], core.settings)
    assert all(x['item_id'] != item['id'] for x in result['changes'] + result['unplaced'])
    change = {'item_id': item['id'], 'expected_version': 1, 'start': '2026-12-01T09:00:00Z', 'end': '2026-12-01T10:00:00Z'}
    with pytest.raises(Conflict, match='mail_candidate_requires_review'):
        core.create_draft([change], 'candidate-draft')


def test_mixed_school_and_manual_stay_formal(tmp_path):
    core = hub(tmp_path)
    mail(core)
    item, _ = candidate(core)
    with core.db.transaction() as db:
        raw = core._get(db, item['id'])
        raw['evidence'].append({'source': 'moodle', 'source_id': 'module:1'})
        core._put(db, raw)
    manual = core.create_item({'title': 'Manual'})
    assert {i['id'] for i in core.list_items(view='formal')} == {item['id'], manual['id']}
    assert core.list_items(view='mail_pending') == []


def test_migration_adds_metadata_without_changing_versions_mapping_or_receipts(tmp_path):
    core = hub(tmp_path)
    mail(core)
    pending, _ = candidate(core)
    with core.db.transaction() as db:
        raw = core._get(db, pending['id'])
        raw.pop('mail_review', None)
        raw.pop('mail_keys', None)
        core._put(db, raw)
        db.execute('INSERT INTO calendar_mappings VALUES(?,?,?,?,?)', ('google:test', 'c', 'e', pending['id'], '{}'))
    restarted = Core(Database(core.db.path), core.settings)
    actual = restarted.get_item(pending['id'])
    assert actual['version'] == pending['version'] and actual['deadline_revision'] == pending['deadline_revision']
    assert actual['mail_review']['state'] == 'pending'
    assert actual['mail_keys'] == ['school:m1']
    with restarted.db.connect() as db:
        assert db.execute('SELECT count(*) FROM calendar_mappings').fetchone()[0] == 1
    assert list((tmp_path / 'backups').glob('mail-triage-*.sqlite3'))


@pytest.mark.parametrize('status,review,scheduled', [('completed', 'needs_analysis', False),
    ('submitted', 'needs_analysis', False), ('cancelled', 'needs_analysis', False),
    ('pending', 'ready', False), ('pending', 'needs_analysis', True)])
def test_legacy_reviewed_closed_or_scheduled_mail_is_preserved(tmp_path, status, review, scheduled):
    core = hub(tmp_path)
    mail(core)
    item, _ = candidate(core)
    with core.db.transaction() as db:
        raw = core._get(db, item['id'])
        raw.pop('mail_review', None)
        raw.pop('mail_keys', None)
        raw.update(status=status, review_state=review)
        if scheduled:
            raw.update(scheduled_start='2026-12-01T09:00:00Z', scheduled_end='2026-12-01T10:00:00Z')
        core._put(db, raw)
    restarted = Core(Database(core.db.path), core.settings)
    assert restarted.get_item(item['id'])['mail_review']['state'] == 'confirmed'
    assert len(restarted.list_items(view='formal')) == 1


def test_api_search_detail_permissions_csrf_and_formal_dashboard(tmp_path):
    core = hub(tmp_path)
    mail(core)
    item, _ = candidate(core)
    auth = Auth(core.db, core.settings)
    auth.set_password('owner', 'a-strong-password')
    client = TestClient(create_app(core, connections=Connections(core, tmp_path / 'config.json', MemorySecrets())))
    agent = {'Authorization': 'Bearer ' + auth.issue_token('agent', ['read', 'draft'])}
    path = '/api/v1/items/' + item['id'] + '/mail-review'
    body = {'action': 'confirm', 'expected_version': 1, 'changes': {'title': 'Verified'}}
    assert client.post(path, json=body, headers={**agent, 'Idempotency-Key': 'review'}).status_code == 403
    login = client.post('/api/v1/login', json={'username': 'owner', 'password': 'a-strong-password'})
    assert client.post(path, json=body, headers={'Idempotency-Key': 'review'}).status_code == 403
    assert client.get('/api/v1/items?view=formal').json() == []
    dashboard = client.get('/api/v1/dashboard').json()
    assert dashboard['counts']['active'] == 0 and dashboard['counts']['mail_pending'] == 1
    result = client.get('/api/v1/mail/search?review=pending').json()
    assert result['total'] == 1 and result['items'][0]['review_counts']['pending'] == 1
    assert 'body' not in result['items'][0]
    assert client.get('/api/v1/mail/school%3Am1').json()['linked_items'][0]['id'] == item['id']
    headers = {'Idempotency-Key': 'review', 'X-CSRF-Token': login.json()['csrf_token']}
    assert client.post(path, json=body, headers=headers).status_code == 200
    assert client.post(path, json=body, headers=headers).json()['id'] == item['id']
    assert client.get('/api/v1/mail/search?review=pending').json()['total'] == 0
    assert len(client.get('/api/v1/items?view=formal').json()) == 1
    assert client.get('/api/v1/mail/search?review=bad').status_code == 422
    remote = TestClient(create_app(core, remote=True))
    assert remote.post(path, json=body, headers=headers).status_code == 404


def test_link_is_account_qualified_and_not_a_title_guess(tmp_path):
    core = hub(tmp_path)
    mail(core, 'm1', 'school')
    mail(core, 'm1', 'personal')
    mail(core, 'm10', 'school')
    item, _ = candidate(core, 'm1')
    assert item['mail_keys'] == ['school:m1']
    assert core.get_mail('personal:m1')['linked_items'] == []
    assert core.get_mail('school:m10')['linked_items'] == []
