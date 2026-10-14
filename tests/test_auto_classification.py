import io
import json

from academic_os.db import Database
from academic_os.core import Core
from test_mail_triage import hub, candidate, mail


def test_conflicting_title_rules_do_not_choose_first_match(tmp_path):
    core = hub(tmp_path)
    item = core.ingest('gmail:school', [{'source_id': 'm1:event', 'title': 'COMP1000 project briefing lecture', 'review_state': 'needs_analysis'}])[0]
    c = item['classification']
    assert c['category'] is None
    assert c['fields']['category']['status'] == 'conflict'
    assert set(c['fields']['category']['candidates']) == {'assignment', 'event'}
    assert c['ai']['status'] == 'disabled'


def test_scoped_rules_do_not_spread_or_override_source_facts(tmp_path):
    from academic_os.classification import ClassificationRules
    core = hub(tmp_path)
    rules = [{'id': 'course-project', 'scope': {'source': 'gmail:school'}, 'field': 'category', 'terms': ['briefing'], 'value': 'event'}]
    ClassificationRules(core).replace(rules, 0)
    school = core.ingest('gmail:school', [{'source_id': 'a', 'title': 'briefing'}])[0]
    personal = core.ingest('gmail:personal', [{'source_id': 'a', 'title': 'briefing'}])[0]
    assert school['classification']['category'] == 'event'
    assert personal['classification']['category'] == 'task'
    source = core.ingest('moodle', [{'source_id': 'b', 'title': 'project lecture', 'kind': 'quiz', 'course': 'COMP1000'}])[0]
    assert source['classification']['category'] == 'quiz'
    assert source['classification']['fields']['category']['status'] == 'source_identified'
    assert source['classification']['trace']


def test_ai_port_is_authenticated_disabled_and_does_not_mutate(tmp_path):
    from test_api import clients
    core, local, remote, headers = clients(tmp_path)
    item = core.create_item({'title': 'project lecture'})
    path = '/api/v1/items/' + item['id'] + '/classification/ai-preview'
    assert local.get('/api/v1/classification/spec').status_code == 401
    assert local.get('/api/v1/classification/spec', headers=headers).json()['enabled'] is False
    before = core.get_item(item['id'])
    response = local.post(path, json={'expected_version': item['version']}, headers=headers)
    assert response.status_code == 200 and response.json()['status'] == 'disabled'
    assert response.json()['proposals'] == []
    assert core.get_item(item['id']) == before and core.list_jobs() == []
    assert remote.post(path, json={'expected_version': item['version']}, headers=headers).status_code == 404


def test_rule_changes_invalidate_derived_fingerprint_not_business_version(tmp_path):
    import pytest
    from academic_os.classification import ClassificationRules
    from academic_os.core import Conflict
    core = hub(tmp_path)
    item = core.ingest('gmail:school', [{'source_id': 'a', 'title': 'COMP1000 briefing project'}])[0]
    rules = ClassificationRules(core)
    rule = {'id': 'course-briefing', 'scope': {'course_key': 'course-name:comp1000'}, 'field': 'category', 'terms': ['briefing'], 'value': 'event'}
    rules.replace([rule], 0)
    current = core.get_item(item['id'])
    assert current['version'] == item['version'] and current['updated_at'] == item['updated_at']
    assert current['classification']['category'] == 'event'
    assert current['classification']['fingerprint'] != item['classification']['fingerprint']
    assert current['classification']['rules_revision'] == 1
    assert core.list_jobs() == []
    with pytest.raises(Conflict):
        rules.replace([], 0)
    with pytest.raises(ValueError):
        rules.replace([{**rule, 'scope': {}}], 1)


def test_rule_write_is_owner_only_and_browser_csrf_protected(tmp_path):
    from test_api import clients
    core, local, remote, headers = clients(tmp_path)
    body = {'expected_revision': 0, 'rules': []}
    assert local.put('/api/v1/classification/rules', json=body, headers=headers).status_code == 403
    login = local.post('/api/v1/login', json={'username': 'owner', 'password': 'a-strong-password-123'})
    assert local.put('/api/v1/classification/rules', json=body).status_code == 403
    assert local.put('/api/v1/classification/rules', json=body, headers={'X-CSRF-Token': login.json()['csrf_token']}).status_code == 200
    assert remote.get('/api/v1/classification/spec', headers=headers).status_code == 404


def test_collector_unicode_output_survives_windows_console_encoding(monkeypatch):
    from academic_os import moodle_process
    output = io.BytesIO()
    stream = io.TextIOWrapper(output, encoding='cp1252')
    monkeypatch.setattr('sys.stdin', io.StringIO('{}'))
    monkeypatch.setattr('sys.stdout', stream)
    monkeypatch.setattr(moodle_process, 'collect_snapshot', lambda config: {'complete': True, 'records': [{'title': '课程作业：报告一'}], 'errors': []})
    moodle_process.main()
    stream.flush()
    assert json.loads(output.getvalue())['records'][0]['title'] == '课程作业：报告一'


def test_ingest_automatically_classifies_course_assignment_and_group(tmp_path):
    core = hub(tmp_path)
    record = {'source_id': 'moodle:activity:assign:11', 'title': 'Assignment 1 - Group A', 'course': 'COMP1000 Programming',
              'course_id': 7, 'course_site': 'https://school.example.test', 'kind': 'assignment'}
    item = core.ingest('moodle', [record])[0]
    assert item['classification']['category'] == 'assignment'
    tags = core.get_item(item['id'])['tags']
    assert {t['kind'] for t in tags if t['origin'] == 'auto'} >= {'course', 'assignment', 'group'}
    assert any(t['name'] == 'Group A' for t in tags)
    assert core.list_items(view='formal')[0]['id'] == item['id']
    version = item['version']
    assert core.ingest('moodle', [record])[0]['version'] == version
    restarted = Core(Database(core.settings.database_path), core.settings)
    assert restarted.get_item(item['id'])['classification']['course'] == record['course']


def test_auto_course_scope_does_not_merge_same_name_and_unknown_is_explicit(tmp_path):
    core = hub(tmp_path)
    first = core.ingest('moodle', [{'source_id': 'a', 'title': 'Lab', 'course': 'Same course', 'course_id': 1, 'kind': 'assignment'}])[0]
    second = core.ingest('moodle', [{'source_id': 'b', 'title': 'Different Lab', 'course': 'Same course', 'course_id': 2, 'kind': 'assignment'}])[0]
    a = next(t for t in core.get_item(first['id'])['tags'] if t['kind'] == 'course')
    b = next(t for t in core.get_item(second['id'])['tags'] if t['kind'] == 'course')
    assert a['id'] != b['id']
    mail(core)
    pending, _ = candidate(core)
    assert pending['classification']['course'] is None
    assert pending['classification']['course_status'] == 'unidentified'
    assert pending['mail_review']['state'] == 'pending'
    assert pending['id'] not in {i['id'] for i in core.list_items(view='formal')}


def test_moodle_installation_paths_isolate_course_rules(tmp_path):
    from academic_os.classification import classify
    base = {'id': 'fixture', 'title': 'briefing Group A', 'course': 'COMP1000', 'kind': 'task'}
    def item(site):
        return {**base, 'evidence': [{'source': 'moodle', 'course_id': 7, 'course_site': site}]}
    first, tags = classify(item('https://school.test/moodle-2025/'))
    second, other = classify(item('https://school.test/moodle-2026'))
    assert first['course_key'] != second['course_key']
    assert {t['id'] for t in tags if t['kind'] in {'course', 'group'}}.isdisjoint({t['id'] for t in other if t['kind'] in {'course', 'group'}})
    rule = {'id': 'first', 'scope': {'course_key': first['course_key']}, 'field': 'category', 'terms': ['briefing'], 'value': 'event'}
    assert classify(item('https://school.test/moodle-2025'), [rule])[0]['category'] == 'event'
    assert classify(item('https://school.test/moodle-2026'), [rule])[0]['category'] == 'task'
