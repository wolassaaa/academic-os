"""Personal workflow decisions never manufacture school submission facts."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from academic_os.api import create_app
from academic_os.core import Core
from academic_os.db import dumps
from academic_os.notifications import Notifications
from academic_os.security import Auth
from test_mail_triage import hub


def school(core, **changes):
    record = {'source_id': 'assignment-1', 'title': 'Fictional assignment', 'course': 'TEST101', 'submission_status': 'not_started', 'due_at': '2026-12-20T04:00:00Z', 'estimate_minutes': 30}
    return core.ingest('moodle:fixture', [{**record, **changes}])[0]


def test_personal_completed_survives_changed_source_without_forging_submission(tmp_path):
    core = hub(tmp_path)
    first = school(core)
    done = core.update_item(first['id'], {'status': 'completed'}, first['version'], actor='user:fixture')
    refreshed = school(core, title='Source changed the assignment title')
    assert refreshed['id'] == first['id'] and refreshed['status'] == 'completed'
    assert refreshed['personal_status'] == done['personal_status']
    assert done['personal_status']['state'] == 'completed'
    assert done['personal_status']['origin'] == 'explicit'
    assert refreshed['evidence'][0]['submission_status'] == 'not_started'
    with core.db.connect() as db:
        source = json.loads(db.execute('SELECT document FROM source_records WHERE item_id=?', (first['id'],)).fetchone()[0])
    assert source['submission_status'] == 'not_started'
    assert Notifications(core).generate()['considered'] == 0


@pytest.mark.parametrize('status', ['pending', 'in_progress', 'submitted', 'cancelled'])
def test_explicit_personal_state_is_distinct_from_later_school_submission(tmp_path, status):
    core = hub(tmp_path)
    item = school(core)
    personal = core.update_item(item['id'], {'status': status}, item['version'])
    observed = school(core, submission_status='submitted')
    assert observed['status'] == status
    assert observed['personal_status'] == personal['personal_status']
    assert observed['evidence'][0]['submission_status'] == 'submitted'


def test_source_only_status_and_unknown_keep_existing_semantics(tmp_path):
    core = hub(tmp_path)
    item = school(core, submission_status='submitted')
    assert item['status'] == 'submitted' and not item.get('personal_status')
    unknown = school(core, submission_status='unknown')
    assert unknown['status'] == 'submitted'
    assert unknown['evidence'][0]['submission_status'] == 'unknown'
    reopened = school(core, submission_status='not_started')
    assert reopened['status'] == 'pending' and not reopened.get('personal_status')


def test_new_source_only_completed_is_not_misclassified_as_legacy_after_restart(tmp_path):
    core = hub(tmp_path)
    item = school(core, submission_status='completed')
    assert item['status'] == 'completed'
    restarted = Core(core.db, core.settings)
    reopened = school(restarted, submission_status='not_started')
    assert reopened['status'] == 'pending'
    assert reopened['personal_status'] is None


def test_old_active_item_does_not_turn_future_source_completion_into_personal_history(tmp_path):
    core = hub(tmp_path)
    item = school(core)
    with core.db.transaction() as db:
        item.pop('personal_status', None)  # A pending pre-upgrade item.
        core._put(db, item)
    upgraded = Core(core.db, core.settings)
    school(upgraded, submission_status='completed')
    reopened = school(Core(core.db, core.settings), submission_status='not_started')
    assert reopened['status'] == 'pending'
    assert reopened['personal_status'] is None


def test_explicit_reopen_replaces_personal_completion_without_changing_school_facts(tmp_path):
    core = hub(tmp_path)
    item = school(core)
    done = core.update_item(item['id'], {'status': 'completed'}, item['version'])
    reopened = core.update_item(item['id'], {'status': 'pending'}, done['version'])
    refreshed = school(core, title='Source later edit', submission_status='submitted')
    assert refreshed['status'] == 'pending' and refreshed['personal_status'] == reopened['personal_status']
    assert refreshed['evidence'][0]['submission_status'] == 'submitted'


def test_legacy_completion_migration_backs_up_and_preserves_business_history(tmp_path):
    core = hub(tmp_path)
    item = school(core)
    item.pop('personal_status', None)  # Emulate pre-v0.8 JSON, not a new source-only record.
    item.update(status='completed', scheduled_start='2026-12-19T01:00:00Z', scheduled_end='2026-12-19T02:00:00Z')
    with core.db.transaction() as db:
        core._put(db, item)
        db.execute("INSERT INTO operations VALUES('fixture-history','fingerprint',?)", (dumps(item),))
        before = tuple(db.execute('SELECT version,updated_at FROM items WHERE id=?', (item['id'],)).fetchone())
        history = [tuple(r) for r in db.execute('SELECT * FROM audit')]
    restarted = Core(core.db, core.settings)
    migrated = restarted.get_item(item['id'])
    assert migrated['personal_status'] == {'state': 'completed', 'origin': 'legacy_completed', 'actor': None, 'updated_at': None}
    for key in ['id', 'version', 'updated_at', 'scheduled_start', 'scheduled_end', 'evidence', 'classification']:
        assert migrated[key] == item[key]
    backups = list((tmp_path / 'backups').glob('personal-status-*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        old = json.loads(db.execute('SELECT document FROM items WHERE id=?', (item['id'],)).fetchone()[0])
        assert 'personal_status' not in old and old['status'] == 'completed'
    Core(core.db, core.settings)
    assert list((tmp_path / 'backups').glob('personal-status-*.sqlite3')) == backups
    with core.db.connect() as db:
        assert tuple(db.execute('SELECT version,updated_at FROM items WHERE id=?', (item['id'],)).fetchone()) == before
        assert [tuple(r) for r in db.execute('SELECT * FROM audit')] == history
        assert json.loads(db.execute("SELECT result FROM operations WHERE key='fixture-history'").fetchone()[0]) == item
    assert school(restarted, title='Changed historical source')['status'] == 'completed'


def test_legacy_backup_failure_leaves_item_without_partial_metadata(tmp_path, monkeypatch):
    core = hub(tmp_path)
    item = school(core)
    with core.db.transaction() as db:
        item.pop('personal_status', None)  # Legacy records have no discriminator.
        item['status'] = 'completed'
        core._put(db, item)
    def failed_backup(target):
        raise OSError('fictional backup failure')
    monkeypatch.setattr(core.db, 'backup', failed_backup)
    with pytest.raises(OSError, match='fictional backup failure'):
        Core(core.db, core.settings)
    assert core.get_item(item['id']) == item


def test_completion_api_receipt_is_exact_and_personal_metadata_is_not_directly_editable(tmp_path):
    core = hub(tmp_path)
    item = school(core)
    auth = Auth(core.db, core.settings)
    auth.set_password('owner', 'fixture-password-123')
    client = TestClient(create_app(core))
    session = client.post('/api/v1/login', json={'username': 'owner', 'password': 'fixture-password-123'}).json()
    headers = {'X-CSRF-Token': session['csrf_token'], 'Idempotency-Key': 'fixture-complete'}
    body = {'expected_version': item['version'], 'changes': {'status': 'completed'}}
    path = '/api/v1/items/' + item['id']
    first = client.patch(path, json=body, headers=headers)
    assert first.status_code == 200
    assert first.json()['personal_status']['state'] == 'completed'
    assert client.patch(path, json=body, headers=headers).json() == first.json()
    assert client.patch(path, json=body, headers={'Idempotency-Key': 'fixture-complete'}).status_code == 403
    invalid = client.patch(path, json={'expected_version': first.json()['version'], 'changes': {'personal_status': {'state': 'submitted'}}}, headers={**headers, 'Idempotency-Key': 'forged'})
    assert invalid.status_code == 422
    assert core.get_item(item['id'])['version'] == item['version'] + 1


def test_notification_complete_is_an_explicit_personal_decision(tmp_path):
    core = hub(tmp_path)
    item = school(core)
    notices = Notifications(core)
    notice = notices.queue('workbench', {'title': 'Fictional reminder'}, 'fixture-notice', item['id'])
    notices.action(notice, 'complete', actor='user:fixture')
    completed = core.get_item(item['id'])
    assert completed['personal_status']['origin'] == 'explicit'
    assert completed['personal_status']['actor'] == 'user:fixture'
    changed = school(core, title='Updated after reminder completion')
    assert changed['status'] == 'completed' and changed['personal_status'] == completed['personal_status']


def test_concurrent_startup_preserves_legacy_completion_with_one_backup(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    core = hub(tmp_path)
    item = school(core)
    with core.db.transaction() as db:
        item.pop('personal_status', None)  # Legacy records have no discriminator.
        item['status'] = 'completed'
        core._put(db, item)
    def restart(_):
        return Core(core.db, core.settings).get_item(item['id'])
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(restart, range(2)))
    assert results[0]['personal_status'] == results[1]['personal_status']
    assert results[0]['version'] == item['version']
    assert len(list((tmp_path / 'backups').glob('personal-status-*.sqlite3'))) == 1
