"""Bound execution shape, not wall-clock speed, on fictional local data."""
from contextlib import contextmanager
import json
import sqlite3

import pytest

from academic_os.db import dumps
from academic_os.mail_query import search_mail
from academic_os.mail_triage import linked_items, review_state
from test_mail_triage import hub


def fixture(tmp_path, mail_count=240, item_count=100):
    core = hub(tmp_path)
    template = core.create_item({'title': 'Fixture task', 'estimate_minutes': 30})
    with core.db.transaction() as db:
        db.execute('DELETE FROM items')
        for i in range(mail_count):
            key = f'school:m{i:04}'
            account = 'school' if i % 2 == 0 else 'personal'
            day = '2026-10-17' if i % 3 else '2026-10-16'
            doc = {'key': key, 'id': f'm{i:04}', 'account': account, 'subject': 'needle task' if i % 2 == 0 else 'Other notice', 'from': 'teacher@example.test', 'body': 'Fictional original text', 'received_at': day + 'T00:00:00Z'}
            db.execute('INSERT INTO mail VALUES(?,?,?,?)', (key, account, dumps(doc), doc['received_at']))
        for i in range(item_count):
            key = f'school:m{i:04}'
            evidence = [{'source': 'gmail:fixture', 'source_id': f'm{i:04}:date'}]
            if i % 4 == 3:
                evidence.append({'source': 'moodle:fixture', 'source_id': str(i)})
            state = ['pending', 'ignored', 'confirmed', 'pending'][i % 4]
            keys = [key, key] if i % 7 == 0 else [key]
            if i == 0:
                keys.extend(['school:m0101', 'school:missing'])
            item = {**template, 'id': f'fixture-{i}', 'manual_origin': False, 'mail_keys': keys, 'evidence': evidence, 'mail_review': {'state': state, 'revision': 0}}
            db.execute('INSERT INTO items VALUES(?,?,?,?)', (item['id'], dumps(item), item['version'], item['updated_at']))
    return core


@pytest.mark.parametrize('state', ['pending', 'ignored'])
def test_review_filter_evaluates_item_state_once_per_statement(tmp_path, state):
    core = fixture(tmp_path)
    original = core.db.connect
    calls = []

    class Measured:
        def __init__(self, db):
            self.db = db

        def __getattr__(self, name):
            return getattr(self.db, name)

        def create_function(self, name, arguments, fn, **options):
            def count(value):
                calls.append(1)
                return fn(value)
            return self.db.create_function(name, arguments, count if name == 'mail_review_state' else fn, **options)

    @contextmanager
    def measured():
        with original() as db:
            yield Measured(db)
    core.db.connect = measured
    result = search_mail(core, review=state, limit=20, offset=2)
    assert result['total'] == (26 if state == 'pending' else 25)
    assert len(result['items']) == 20
    assert len(calls) <= 2 * 100, f'Correlated state evaluation: {len(calls)} calls for 100 items, count + page'


def test_review_filter_fits_fixed_sql_instruction_budget(tmp_path):
    core = fixture(tmp_path, mail_count=1000, item_count=200)
    original = core.db.connect
    callbacks = []

    @contextmanager
    def bounded():
        with original() as db:
            def progress():
                callbacks.append(1)
                return int(len(callbacks) > 500)  # 500,000 VM steps over the read.
            db.set_progress_handler(progress, 1000)
            yield db
    core.db.connect = bounded
    try:
        result = search_mail(core, review='ignored', limit=20)
    except sqlite3.OperationalError as error:
        pytest.fail(f'Review query exhausted fixed SQL instruction budget: {error}')
    assert result['total'] == 50 and len(result['items']) == 20


@pytest.mark.parametrize('state', ['pending', 'ignored'])
def test_review_filter_matches_authoritative_associations_and_combined_page(tmp_path, state):
    core = fixture(tmp_path)
    core.settings.timezone = 'Asia/Taipei'
    with core.db.connect() as db:
        before = [tuple(r) for r in db.execute('SELECT id,document,version FROM items ORDER BY id')]
        expected = []
        for row in db.execute('SELECT id,document FROM mail'):
            doc = json.loads(row['document'])
            if doc['account'] != 'school' or 'needle' not in doc['subject'] or not doc['received_at'].startswith('2026-10-17'):
                continue
            if any(review_state(item) == state for item in linked_items(db, row['id'])):
                expected.append((doc['received_at'], doc['key']))
    expected = [key for _, key in sorted(expected, reverse=True)]
    options = {'review': state, 'account': 'school', 'q': 'needle', 'date_mode': 'custom', 'start': '2026-10-17', 'end': '2026-10-17', 'limit': 3}
    pages = [search_mail(core, offset=offset, **options) for offset in range(0, len(expected) + 3, 3)]
    assert [m['key'] for page in pages for m in page['items']] == expected
    assert all(page['total'] == len(expected) for page in pages)
    assert all('body' not in m for page in pages for m in page['items'])
    assert all('school:m0003' != m['key'] for page in pages for m in page['items'])  # Mixed source stays formal.
    with core.db.connect() as db:
        assert before == [tuple(r) for r in db.execute('SELECT id,document,version FROM items ORDER BY id')]


def test_shared_mail_matches_each_state_and_updates_without_cached_membership(tmp_path):
    core = fixture(tmp_path)
    with core.db.transaction() as db:
        item = json.loads(db.execute("SELECT document FROM items WHERE id='fixture-1'").fetchone()[0])
        item['mail_keys'].extend(['school:m0000', 'school:m0000'])
        db.execute("UPDATE items SET document=? WHERE id='fixture-1'", (dumps(item),))
    pending = search_mail(core, review='pending', limit=100)
    ignored = search_mail(core, review='ignored', limit=100)
    assert pending['total'] == ignored['total'] == 26
    assert 'school:m0000' in {m['key'] for m in pending['items']}
    shared = next(m for m in ignored['items'] if m['key'] == 'school:m0000')
    assert shared['review_counts'] == {'pending': 1, 'ignored': 1, 'confirmed': 0}
    core.review_mail_item('fixture-0', 'confirm', 1, 'fixture-confirm')
    assert search_mail(core, review='pending')['total'] == 24
    assert search_mail(core, review='ignored')['total'] == 26
    core.review_mail_item('fixture-1', 'restore', 1, 'fixture-restore')
    assert search_mail(core, review='pending')['total'] == 26
    assert search_mail(core, review='ignored')['total'] == 24
