"""Reopen is a confirmation decision, never a source submission edit."""
import pytest
from fastapi.testclient import TestClient
from academic_os.api import create_app
from academic_os.security import Auth
from test_mail_triage import hub


def fixture(tmp_path, initial='completed'):
    core = hub(tmp_path)
    data = {'source_id': 'fixture', 'title': 'Fictional school task', 'submission_status': 'submitted', 'due_at': '2026-12-20T12:00:00+08:00'}
    item = core.ingest('moodle:fixture', [data])[0]
    item = core.update_item(item['id'], {'status': initial}, item['version'])
    auth = Auth(core.db, core.settings)
    auth.set_password('owner', 'fixture-password-123')
    client = TestClient(create_app(core))
    session = client.post('/api/v1/login', json={'username': 'owner', 'password': 'fixture-password-123'}).json()
    return core, data, item, auth, client, session


def test_default_agent_cannot_reopen_a_closed_item(tmp_path):
    core, _, item, auth, client, _ = fixture(tmp_path)
    token = auth.issue_token('fixture-agent', ['read', 'draft', 'sync'])
    response = client.patch('/api/v1/items/' + item['id'], json={'expected_version': item['version'], 'changes': {'status': 'pending'}}, headers={'Authorization': 'Bearer ' + token, 'Idempotency-Key': 'agent-reopen'})
    assert response.status_code == 403
    assert core.get_item(item['id']) == item


@pytest.mark.parametrize('initial', ['completed', 'submitted', 'cancelled'])
def test_reopen_keeps_id_evidence_exact_receipt_and_sync_decision(tmp_path, initial):
    core, data, item, _, client, session = fixture(tmp_path, initial)
    path = '/api/v1/items/' + item['id']
    headers = {'X-CSRF-Token': session['csrf_token'], 'Idempotency-Key': 'fixture-reopen'}
    payload = {'expected_version': item['version'], 'changes': {'status': 'pending'}}
    assert client.patch(path, json=payload, headers={'Idempotency-Key': 'no-csrf'}).status_code == 403
    result = client.patch(path, json=payload, headers=headers)
    assert result.status_code == 200
    reopened = result.json()
    assert reopened['id'] == item['id'] and reopened['status'] == 'pending'
    assert reopened['version'] == item['version'] + 1
    for field in ['evidence', 'source_due_at', 'scheduled_start', 'scheduled_end', 'mail_keys']:
        assert reopened.get(field) == item.get(field)
    assert reopened['personal_status']['origin'] == 'explicit'
    assert reopened['personal_status']['state'] == 'pending'
    assert client.patch(path, json=payload, headers=headers).json() == reopened
    assert client.patch(path, json=payload, headers={**headers, 'Idempotency-Key': 'stale'}).status_code == 409
    later = core.ingest('moodle:fixture', [{**data, 'title': 'Updated by school'}])[0]
    assert later['status'] == 'pending' and later['personal_status'] == reopened['personal_status']
    assert later['evidence'][0]['submission_status'] == 'submitted'
    remote = TestClient(create_app(core, remote=True))
    remote.post('/api/v1/login', json={'username': 'owner', 'password': 'fixture-password-123'})
    assert remote.patch(path, json=payload, headers=headers).status_code in {404, 405}


def test_reopen_authorization_uses_transactional_state_not_earlier_read(tmp_path, monkeypatch):
    core, _, item, auth, client, _ = fixture(tmp_path, 'pending')
    token = auth.issue_token('racing-agent', ['read', 'draft'])
    original = core.update_item

    def interleave(item_id, changes, expected_version, *args, **kwargs):
        # Owner closes between route-level inspection and the update.
        original(item_id, {'status': 'completed'}, item['version'], actor='owner')
        return original(item_id, changes, expected_version, *args, **kwargs)

    monkeypatch.setattr(core, 'update_item', interleave)
    response = client.patch('/api/v1/items/' + item['id'], json={'expected_version': item['version'] + 1, 'changes': {'status': 'pending'}}, headers={'Authorization': 'Bearer ' + token, 'Idempotency-Key': 'racing-reopen'})
    assert response.status_code == 403
    assert core.get_item(item['id'])['status'] == 'completed'


def test_draft_agent_can_still_update_active_progress(tmp_path):
    core, _, item, auth, client, _ = fixture(tmp_path, 'pending')
    token = auth.issue_token('draft-agent', ['read', 'draft'])
    response = client.patch('/api/v1/items/' + item['id'], json={'expected_version': item['version'], 'changes': {'status': 'in_progress'}}, headers={'Authorization': 'Bearer ' + token, 'Idempotency-Key': 'active-progress'})
    assert response.status_code == 200
    assert core.get_item(item['id'])['status'] == 'in_progress'
