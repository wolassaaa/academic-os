"""Weekly KPI report (#11): read-only, honest about what it cannot measure, reachable while the service runs."""
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from academic_os import cli
from academic_os.api import create_app
from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database, dumps
from academic_os.kpi import render_markdown, save_markdown, weekly_report
from academic_os.notifications import Notifications
from academic_os.security import Auth


def hub(tmp_path, **overrides):
    settings = Settings(data_dir=tmp_path, secure_cookies=False, **overrides)
    return Core(Database(settings.database_path), settings)


def iso(value):
    return value.isoformat().replace('+00:00', 'Z')


def snapshot(core):
    with core.db.connect() as db:
        return {table: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                for table in ('items', 'jobs', 'notifications', 'audit', 'calendar_mappings', 'meta')}


def test_empty_database_reports_not_measured_instead_of_zero(tmp_path):
    report = weekly_report(hub(tmp_path))
    kpis = report['kpis']
    for key in ('R1_missed_deadlines', 'R2_moodle_coverage', 'R3_push_latency_p95_seconds', 'R4_mail_candidate_precision',
                'R5_plan_coverage', 'R6_daily_use', 'R7_study_block_completion', 'H2_availability', 'H3_sync_success_rate'):
        assert kpis[key]['status'] == 'not_measured' and kpis[key]['reason'], key
    assert kpis['H1_reauthorizations']['value'] == 0 and kpis['H1_reauthorizations']['verdict'] == 'pass'
    assert kpis['H5_duplicate_calendar_events']['value'] == 0
    assert kpis['H6_stale_sources']['verdict'] == 'no_data'


def test_report_is_read_only(tmp_path):
    core = hub(tmp_path)
    now = datetime.now(timezone.utc)
    core.create_item({'title': 'Essay', 'estimate_minutes': 60, 'due_at': iso(now + timedelta(days=3))})
    before = snapshot(core)
    weekly_report(core)
    assert snapshot(core) == before


def test_plan_coverage_counts_only_open_formal_items_due_within_seven_days(tmp_path):
    core = hub(tmp_path)
    now = datetime.now(timezone.utc)
    planned = core.create_item({'title': 'Planned', 'estimate_minutes': 60, 'due_at': iso(now + timedelta(days=2))})
    planned.update(scheduled_start=iso(now + timedelta(days=1)), scheduled_end=iso(now + timedelta(days=1, hours=1)))
    with core.db.transaction() as db:  # schedule changes normally go through a confirmed plan draft
        core._put(db, planned)
    core.create_item({'title': 'No estimate', 'due_at': iso(now + timedelta(days=3))})
    core.create_item({'title': 'Later', 'estimate_minutes': 30, 'due_at': iso(now + timedelta(days=20))})
    core.create_item({'title': 'Done', 'status': 'completed', 'due_at': iso(now + timedelta(days=1))})
    entry = weekly_report(core, now=now)['kpis']['R5_plan_coverage']
    assert entry['due_next_7_days'] == 2 and entry['planned'] == 1
    assert entry['value'] == 0.5 and entry['verdict'] == 'fail'
    assert entry['unplaced_reasons'] == {'missing_estimate': 1}


def test_mail_precision_uses_reviews_inside_the_window_only(tmp_path):
    core = hub(tmp_path)
    now = datetime.now(timezone.utc)
    with core.db.transaction() as db:
        for action, when in (('mail.confirm', now - timedelta(days=1)), ('mail.confirm', now - timedelta(days=2)),
                             ('mail.ignore', now - timedelta(days=3)), ('mail.ignore', now - timedelta(days=30))):
            db.execute('INSERT INTO audit(actor,action,entity_id,document,created_at) VALUES(?,?,?,?,?)', ('owner', action, 'x', '{}', when.isoformat()))
    entry = weekly_report(core, now=now)['kpis']['R4_mail_candidate_precision']
    assert (entry['confirmed'], entry['ignored']) == (2, 1)
    assert entry['value'] == 0.667 and entry['verdict'] == 'pass'


def test_reauthorization_alerts_and_duplicate_calendar_events_are_counted(tmp_path):
    core = hub(tmp_path)
    notices = Notifications(core)
    notices.queue('workbench', {'title': 'reauth'}, 'auth-required:workbench:acct:incident-1')
    notices.queue('feishu', {'title': 'reauth'}, 'auth-required:feishu:acct:incident-1')  # same incident, other channel
    notices.queue('workbench', {'title': 'stale'}, 'agent-stale:workbench:hermes:x')
    with core.db.transaction() as db:
        for event in ('e1', 'e2'):
            db.execute('INSERT INTO calendar_mappings VALUES(?,?,?,?,?)', ('google:a', 'cal', event, 'item-1', '{}'))
        db.execute('INSERT INTO calendar_mappings VALUES(?,?,?,?,?)', ('google:a', 'cal', 'e3', 'item-2', '{}'))
    kpis = weekly_report(core)['kpis']
    assert kpis['H1_reauthorizations']['value'] == 1 and kpis['H1_reauthorizations']['verdict'] == 'fail'
    assert kpis['H4_agent_heartbeat']['alerts'] == {'stale': 1, 'stuck': 0}
    assert kpis['H5_duplicate_calendar_events']['value'] == 1


def test_latency_and_stale_sources_come_from_health(tmp_path):
    from academic_os.mail_digest import acknowledge, feed
    core = hub(tmp_path)
    received = iso(datetime.now(timezone.utc) - timedelta(minutes=5))
    doc = {'id': 'm1', 'key': 'school:m1', 'account': 'school', 'subject': 'Notice', 'from': 'school@example.test', 'body': 'Body', 'received_at': received, 'date_nodes': []}
    with core.db.transaction() as db:
        db.execute('INSERT INTO mail VALUES(?,?,?,?)', (doc['key'], 'school', dumps(doc), received))
    page = feed(core)
    acknowledge(core, 'hermes', page['next_cursor'], 0)
    core.source_health('moodle', True)
    with core.db.transaction() as db:
        db.execute("INSERT OR REPLACE INTO sources VALUES('gmail:old','ok',?,?,NULL,'{}')", ((datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),) * 2)
    kpis = weekly_report(core)['kpis']
    assert kpis['R3_push_latency_p95_seconds']['samples'] == 1 and kpis['R3_push_latency_p95_seconds']['verdict'] == 'pass'
    assert kpis['H6_stale_sources']['stale'] == ['gmail:old'] and kpis['H6_stale_sources']['verdict'] == 'fail'


def test_markdown_and_weekly_file(tmp_path):
    core = hub(tmp_path)
    report = weekly_report(core, now=datetime(2026, 11, 29, 12, tzinfo=timezone.utc))
    text = render_markdown(report)
    assert 'R2 Moodle 覆盖率 | 未测量' in text and 'H5 重复日历事件 | 0' in text
    path = save_markdown(core, report)
    assert path.name == 'kpi-2026-W48.md' and path.read_text(encoding='utf-8') == text


def test_api_requires_auth_and_bounds_window(tmp_path):
    settings = Settings(data_dir=tmp_path, secure_cookies=False)
    core = Core(Database(settings.database_path), settings)
    token = Auth(core.db, settings).issue_token('agent', ['read'])
    client = TestClient(create_app(core))
    assert client.get('/api/v1/kpi').status_code == 401
    agent = {'Authorization': 'Bearer ' + token}
    response = client.get('/api/v1/kpi?days=14', headers=agent)
    assert response.status_code == 200 and response.json()['window_days'] == 14
    assert client.get('/api/v1/kpi?days=90', headers=agent).status_code == 422


def test_cli_prints_json_and_saves_markdown(tmp_path, capsys):
    config = tmp_path / 'config.json'
    Settings(data_dir=tmp_path / 'data', secure_cookies=False).save(config)
    assert cli.main(['--config', str(config), 'kpi', '--save']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['window_days'] == 7 and report['saved_to'].endswith('.md')
    assert cli.main(['--config', str(config), 'kpi', '--markdown']) == 0
    assert capsys.readouterr().out.startswith('# Academic OS KPI 周报')
