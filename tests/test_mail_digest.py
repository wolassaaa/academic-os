import pytest

from academic_os.mail_digest import digest, feed, acknowledge
from academic_os.core import Conflict
from academic_os.db import dumps
from test_mail_triage import hub, mail, candidate


def test_digest_has_original_content_truncation_candidates_and_pagination(tmp_path):
    core = hub(tmp_path)
    doc = mail(core)
    item, _ = candidate(core)
    with core.db.transaction() as db:
        doc['body'] = 'Original text\n' + 'x'*1200
        db.execute('UPDATE mail SET document=? WHERE id=?', (dumps(doc), doc['key']))
    page = digest(core, limit=1, body_chars=100)
    entry = page['items'][0]
    assert entry['body_excerpt'].startswith('Original text\n')
    assert len(entry['body_excerpt']) == 100 and entry['body_truncated']
    assert entry['excerpt_kind'] == 'original_text_excerpt'
    assert entry['candidates'][0]['id'] == item['id']
    assert entry['review_counts']['pending'] == 1
    assert page['total'] == 1
    assert digest(core, offset=1)['items'] == []
    assert core.get_mail(doc['key'])['body'] == doc['body']


def test_digest_retains_the_active_text_search(tmp_path):
    core = hub(tmp_path)
    mail(core, 'm1')
    other = mail(core, 'm2')
    with core.db.transaction() as db:
        other['body'] = 'UNIQUE_COURSE'
        db.execute('UPDATE mail SET document=? WHERE id=?', (dumps(other), other['key']))
    assert [m['key'] for m in digest(core, q='UNIQUE_COURSE')['items']] == [other['key']]


def test_feed_tracks_late_old_dated_messages_and_ack_is_separate(tmp_path):
    core = hub(tmp_path)
    first = mail(core, 'first')
    page = feed(core, consumer='hermes', limit=1)
    assert page['cursor'] == 0 and page['next_cursor'] > 0
    assert feed(core, consumer='hermes')['items'][0]['key'] == first['key']
    acknowledge(core, 'hermes', page['next_cursor'], expected_cursor=0)
    old = mail(core, 'late')
    with core.db.transaction() as db:
        db.execute("UPDATE mail SET received_at='2000-01-01' WHERE id=?", (old['key'],))
    next_page = feed(core, consumer='hermes')
    assert [x['key'] for x in next_page['items']] == [old['key']]
    assert next_page['items'][0]['body_excerpt'] == 'Original body'
    with pytest.raises(Conflict):
        acknowledge(core, 'hermes', next_page['next_cursor'], expected_cursor=0)
    with pytest.raises(ValueError):
        acknowledge(core, 'hermes', next_page['next_cursor']+100, expected_cursor=page['next_cursor'])
    assert feed(core, consumer='second')['items'][0]['key'] == first['key']
    # Replacing a cached message must not create a new arrival.
    mail(core, 'late')
    assert len(feed(core, consumer='hermes')['items']) == 1


@pytest.mark.parametrize('limit,chars', [(0, 100), (51, 100), (1, 0), (1, 4001)])
def test_digest_is_bounded(tmp_path, limit, chars):
    with pytest.raises(ValueError):
        digest(hub(tmp_path), limit=limit, body_chars=chars)


def test_summary_export_is_throttled_and_contains_mail_content(tmp_path, monkeypatch):
    from academic_os.worker import Worker
    core = hub(tmp_path)
    mail(core)
    worker = Worker(core, {})
    path = worker.export_periodic(now=0)
    assert 'Original body' in path.read_text(encoding='utf-8')
    stamp = path.stat().st_mtime_ns
    assert worker.export_periodic(now=59) is None
    assert path.stat().st_mtime_ns == stamp
    assert worker.export_periodic(now=60) == path


def test_automatic_mail_content_digest_is_local_and_daily_deduplicated(tmp_path, monkeypatch):
    from datetime import datetime
    from academic_os.worker import Worker
    core = hub(tmp_path)
    for n in range(11):
        mail(core, str(n))
    core.settings.feishu = {'chat_id': 'fixture-chat'}

    class Morning(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 14, 9, tzinfo=tz)

    monkeypatch.setattr('academic_os.worker.datetime', Morning)
    worker = Worker(core, {})
    worker.notification_periodic()
    core.db.set_meta('notice-minute', None)
    worker.notification_periodic()
    content_notices = [n for n in worker.notifications.list() if 'Original body' in n['document']['body']]
    assert len(content_notices) == 1
    assert content_notices[0]['channel'] == 'workbench'
    assert content_notices[0]['document']['local_only'] is True
    assert '最近 10 / 共 11 封' in content_notices[0]['document']['body']
