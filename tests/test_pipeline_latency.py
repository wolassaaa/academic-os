"""End-to-end timing: mail received → collected → consumed by Hermes, plus a consumer heartbeat and stale-agent alerts."""
import json
from datetime import datetime, timedelta, timezone

from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database, dumps


def hub(tmp_path, **overrides):
    settings = Settings(data_dir=tmp_path, secure_cookies=False, **overrides)
    return Core(Database(settings.database_path), settings)


def iso(moment):
    return moment.isoformat().replace('+00:00', 'Z')


def arrive(core, mid, minutes_ago):
    received = iso(datetime.now(timezone.utc) - timedelta(minutes=minutes_ago))
    doc = {'id': mid, 'key': 'school:' + mid, 'account': 'school', 'subject': 'Notice ' + mid, 'from': 'school@example.test', 'body': 'Body', 'received_at': received, 'date_nodes': []}
    with core.db.transaction() as db:
        db.execute('INSERT INTO mail VALUES(?,?,?,?)', (doc['key'], 'school', dumps(doc), received))


def consumer(core, name='hermes'):
    return next(c for c in core.health()['consumers'] if c['name'] == name)


def test_ack_stamps_consumption_and_reports_stage_latency(tmp_path):
    from academic_os.mail_digest import acknowledge, feed
    core = hub(tmp_path)
    arrive(core, 'm1', 10)
    arrive(core, 'm2', 20)
    page = feed(core)
    acknowledge(core, 'hermes', page['next_cursor'], 0)
    latency = core.health()['latency']
    assert latency['window_hours'] == 168
    assert latency['collection']['count'] == 2
    assert 590 <= latency['collection']['p50_seconds'] <= 1300
    assert 1190 <= latency['collection']['p95_seconds'] <= 1300
    consumed = latency['consumption']['hermes']
    assert consumed['count'] == 2 and 0 <= consumed['p95_seconds'] < 60
    # Re-acknowledging the same cursor neither duplicates nor moves timestamps.
    acknowledge(core, 'hermes', page['next_cursor'], 0)
    assert core.health()['latency']['consumption']['hermes']['count'] == 2


def test_old_backfilled_mail_does_not_skew_collection_latency(tmp_path):
    core = hub(tmp_path)
    arrive(core, 'old', 60 * 24 * 30)
    assert core.health()['latency']['collection']['count'] == 0


def test_consumer_heartbeat_backlog_and_browsing(tmp_path):
    from academic_os.mail_digest import acknowledge, feed
    core = hub(tmp_path)
    arrive(core, 'm1', 5)
    feed(core, after=0)  # browsing with an explicit cursor is not a consumer poll
    assert not any(c['name'] == 'hermes' for c in core.health()['consumers'])
    page = feed(core)
    state = consumer(core)
    assert state['last_polled_at'] and state['backlog'] == 1 and state['last_ack_at'] is None
    assert state['oldest_unacked_collected_at']
    acknowledge(core, 'hermes', page['next_cursor'], 0)
    state = consumer(core)
    assert state['backlog'] == 0 and state['last_ack_at'] and state['oldest_unacked_collected_at'] is None


def test_expected_agent_is_listed_before_first_poll(tmp_path):
    core = hub(tmp_path, agent_gateway_port=8767)
    assert consumer(core)['state'] == 'never'
    assert hub(tmp_path / 'off').health()['consumers'] == []


def queued(core, prefix):
    with core.db.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM notifications WHERE unique_key LIKE ? ORDER BY created_at", (prefix + '%',))]


def test_stale_agent_alert_once_then_recovery(tmp_path):
    from academic_os.mail_digest import feed
    from academic_os.notifications import Notifications
    core = hub(tmp_path, agent_gateway_port=8767, agent_stale_minutes=30)
    notices = Notifications(core)
    core.db.set_meta('consumer-seen:hermes', iso(datetime.now(timezone.utc) - timedelta(hours=2)))
    assert consumer(core)['state'] == 'stale'
    notices.health_alerts()
    notices.health_alerts()
    alerts = queued(core, 'agent-stale:')
    assert len(alerts) == 1
    assert 'Hermes' in json.loads(alerts[0]['document'])['title']
    feed(core)
    assert consumer(core)['state'] == 'ok'
    notices.health_alerts()
    notices.health_alerts()
    assert len(queued(core, 'agent-recovered:')) == 1


def test_polling_without_acknowledging_is_reported_as_stuck(tmp_path):
    from academic_os.mail_digest import feed
    from academic_os.notifications import Notifications
    core = hub(tmp_path, agent_gateway_port=8767, agent_stale_minutes=30)
    arrive(core, 'm1', 5)
    with core.db.transaction() as db:
        db.execute("UPDATE mail_feed SET collected_at=?", (iso(datetime.now(timezone.utc) - timedelta(hours=2)),))
    feed(core)
    # A first poll over an old backlog is not stuck yet.
    assert consumer(core)['state'] == 'ok'
    core.db.set_meta('consumer-online-since:hermes', iso(datetime.now(timezone.utc) - timedelta(hours=3)))
    assert consumer(core)['state'] == 'stuck'
    Notifications(core).health_alerts()
    assert len(queued(core, 'agent-stuck:')) == 1


def test_draining_backlog_with_recent_acks_is_not_stuck(tmp_path):
    from academic_os.mail_digest import acknowledge, feed
    core = hub(tmp_path, agent_gateway_port=8767, agent_stale_minutes=30)
    for index in range(3):
        arrive(core, f'm{index}', 5)
    with core.db.transaction() as db:
        db.execute("UPDATE mail_feed SET collected_at=?", (iso(datetime.now(timezone.utc) - timedelta(hours=5)),))
    core.db.set_meta('consumer-online-since:hermes', iso(datetime.now(timezone.utc) - timedelta(hours=3)))
    page = feed(core, limit=1)
    acknowledge(core, 'hermes', page['next_cursor'], 0)
    state = consumer(core)
    assert state['backlog'] == 2 and state['state'] == 'ok'


def test_stale_threshold_is_validated(tmp_path):
    import pytest
    assert Settings(data_dir=tmp_path).agent_stale_minutes == 30
    with pytest.raises(ValueError):
        Settings(data_dir=tmp_path, agent_stale_minutes=2)
