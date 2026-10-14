import pytest

from academic_os.core import Conflict
from academic_os.tags import Tags
from test_mail_triage import hub, mail, candidate


def test_scoped_tags_assign_rename_archive_and_sync_preserves(tmp_path):
    core = hub(tmp_path)
    tags = Tags(core)
    course = tags.create('COMP1000', 'course', '2026 fall', 'c')
    group = tags.create('Group A', 'group', course['id'], 'g')
    assignment = tags.create('Lab 1', 'assignment', course['id'], 'a')
    other = tags.create('Group A', 'group', 'other-course', 'g2')
    assert other['id'] != group['id']
    assert tags.create('COMP1000', 'course', '2026 fall', 'c') == course
    with pytest.raises(Conflict):
        tags.create('COMP1000', 'course', '2026 fall', 'other')
    item = core.ingest('moodle', [{'source_id': '1', 'title': 'Lab', 'due_at': '2027-01-01T00:00:00Z'}])[0]
    assigned = tags.assign(item['id'], [course['id'], group['id'], assignment['id']], item['version'], 'assign')
    assert tags.assign(item['id'], [course['id'], group['id'], assignment['id']], item['version'], 'assign') == assigned
    with pytest.raises(Conflict, match='item_version_changed'):
        tags.assign(item['id'], [], item['version'], 'stale')
    synced = core.ingest('moodle', [{'source_id': '1', 'title': 'Lab updated', 'due_at': '2027-01-01T00:00:00Z'}])[0]
    assert {t['id'] for t in core.get_item(synced['id'])['tags']} == {course['id'], group['id'], assignment['id']}
    renamed = tags.update(group['id'], {'name': 'Team A'}, group['version'])
    assert core.get_item(item['id'])['tags'][1:]  # Definitions are read live, not duplicated in item JSON.
    with pytest.raises(Conflict):
        tags.update(group['id'], {'name': 'Stale'}, group['version'])
    tags.update(group['id'], {'archived': True}, renamed['version'])
    assert any(t['archived'] for t in core.get_item(item['id'])['tags'])
    assert all(t['id'] != group['id'] for t in tags.list())
    assert [i['id'] for i in tags.filter_items(core.list_items(), [course['id'], assignment['id']])] == [item['id']]
    assert core.list_jobs() == []


@pytest.mark.parametrize('name,kind,scope', [('', 'custom', ''), ('x'*81, 'custom', ''), ('x', 'bad', ''), ('x', 'group', 'x'*201)])
def test_tag_validation(tmp_path, name, kind, scope):
    with pytest.raises(ValueError):
        Tags(hub(tmp_path)).create(name, kind, scope, 'invalid')


def test_tags_do_not_confirm_candidates_and_backup_preserves_relations(tmp_path):
    core = hub(tmp_path)
    tags = Tags(core)
    tag = tags.create('Needs reading', 'custom', '', 'new')
    mail(core)
    item, _ = candidate(core)
    tagged = tags.assign(item['id'], [tag['id']], item['version'], 'assign')
    assert tagged['mail_review']['state'] == 'pending'
    assert core.list_items(view='formal') == []
    backup = tmp_path / 'backup.sqlite3'
    core.db.backup(backup)
    from academic_os.core import Core
    from academic_os.db import Database
    restored = Core(Database(backup), core.settings)
    assert restored.get_item(item['id'])['tags'][0]['id'] == tag['id']
    with pytest.raises(ValueError):
        tags.assign(item['id'], ['missing'], tagged['version'], 'missing')


def test_tag_digest_feed_http_permissions_and_remote_boundary(tmp_path):
    from test_api import clients
    core, client, remote, agent = clients(tmp_path)
    doc = mail(core)
    item, _ = candidate(core)
    from academic_os.security import Auth
    read = {'Authorization': 'Bearer ' + Auth(core.db, core.settings).issue_token('reader', ['read'])}
    payload = {'name': 'Course', 'kind': 'course', 'scope': 'fall'}
    assert client.post('/api/v1/tags', json=payload, headers={**read, 'Idempotency-Key': 't'}).status_code == 403
    response = client.post('/api/v1/tags', json=payload, headers={**agent, 'Idempotency-Key': 't'})
    assert response.status_code == 200
    tag = response.json()
    assigned = client.put('/api/v1/items/' + item['id'] + '/tags', json={'tag_ids': [tag['id']], 'expected_version': item['version']}, headers={**agent, 'Idempotency-Key': 'assign'})
    assert assigned.status_code == 200
    assert client.get('/api/v1/items?tag=' + tag['id'], headers=read).json()[0]['id'] == item['id']
    assert client.get('/api/v1/items?view=formal&tag=' + tag['id'], headers=read).json() == []
    assert client.get('/api/v1/mail/digest', headers=read).json()['items'][0]['body_excerpt'] == doc['body']
    page = client.get('/api/v1/mail/feed', headers=read).json()
    ack = {'consumer': 'hermes', 'cursor': page['next_cursor'], 'expected_cursor': 0}
    assert client.post('/api/v1/mail/feed/ack', json=ack, headers=read).status_code == 403
    assert client.post('/api/v1/mail/feed/ack', json=ack, headers=agent).status_code == 200
    assert client.get('/api/v1/mail/feed', headers=agent).json()['items'] == []
    for path in ['/tags', '/mail/digest', '/mail/feed']:
        assert remote.get('/api/v1' + path, headers=agent).status_code == 404
    login = client.post('/api/v1/login', json={'username': 'owner', 'password': 'a-strong-password-123'})
    assert client.post('/api/v1/tags', json={'name': 'Missing csrf'}, headers={'Idempotency-Key': 'csrf'}).status_code == 403
    assert client.post('/api/v1/tags', json={'name': 'With csrf'}, headers={'Idempotency-Key': 'csrf', 'X-CSRF-Token': login.json()['csrf_token']}).status_code == 200


def test_portable_diagnostic_covers_tags_digest_feed(tmp_path):
    from academic_os.portable_check import diagnose_mail_triage
    checks = diagnose_mail_triage(tmp_path)
    assert {'scoped_tags_persistence', 'mail_original_digest', 'mail_durable_feed'} <= set(checks)


@pytest.mark.parametrize('status', ['queued', 'failed', 'running'])
def test_tag_assignment_preserves_already_approved_calendar_work(tmp_path, status):
    from academic_os.calendars import CalendarSync
    core = hub(tmp_path)
    item = core.create_item({'title': 'Approved schedule'})
    draft = core.create_draft([{'item_id': item['id'], 'expected_version': item['version'], 'start': '2027-01-01T09:00:00Z', 'end': '2027-01-01T10:00:00Z'}], 'draft')
    core.confirm_draft(draft['id'], 'confirm')
    scheduled = core.get_item(item['id'])
    old_job = core.list_jobs()[0]
    with core.db.transaction() as db:
        db.execute('UPDATE jobs SET status=? WHERE id=?', (status, old_job['id']))
    tags = Tags(core)
    tag = tags.create('Course', 'course', '', 'course')
    tagged = tags.assign(item['id'], [tag['id']], scheduled['version'], 'tag-item')
    pending = [j for j in core.list_jobs() if j['status'] == 'queued']
    assert len(pending) == 1
    assert pending[0]['payload']['version'] == tagged['version']
    calls = []

    class Provider:
        def put_event(self, calendar, event_id, body, etag, create):
            calls.append(body)
            return {**body, 'id': event_id, 'etag': 'fixture'}

    core.settings.mode = 'live'
    core.settings.google_accounts = [{'id': 'school', 'calendars': [{'id': 'cal', 'write': True}]}]
    assert 'event_id' in CalendarSync(core, {'school': Provider()}).push(item['id'], pending[0]['payload']['version'])
    assert len(calls) == 1
    assert tags.assign(item['id'], [tag['id']], scheduled['version'], 'tag-item') == tagged


def test_schema_upgrade_takes_a_pre_extension_database_backup(tmp_path):
    import sqlite3
    from academic_os.db import Database
    core = hub(tmp_path)
    item = core.create_item({'title': 'Existing item'})
    with core.db.connect() as db:
        db.execute('DROP TABLE item_tags')
        db.execute('DROP TABLE tags')
    Database(core.settings.database_path)
    backups = list((tmp_path / 'backups').glob('before-tags-digest-*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as saved:
        assert saved.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='tags'").fetchone()[0] == 0
        assert saved.execute('SELECT version FROM items WHERE id=?', (item['id'],)).fetchone()[0] == item['version']
