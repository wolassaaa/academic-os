"""Bounded original-text digests and durable arrival cursors, without model calls."""
from __future__ import annotations

import json

from .core import Conflict
from .mail_query import search_mail
from .mail_triage import linked_items, review_counts, review_state


def bounds(limit, body_chars):
    if not 1 <= limit <= 50 or not 1 <= body_chars <= 4000:
        raise ValueError('Digest limit must be 1–50 and excerpt length 1–4000')


def content(db, mail, body_chars):
    row = db.execute("SELECT substr(COALESCE(json_extract(document,'$.body'),''),1,?),length(COALESCE(json_extract(document,'$.body'),'')) FROM mail WHERE id=?", (body_chars, mail['key'])).fetchone()
    candidates = linked_items(db, mail['key'])
    return {**mail, 'body_excerpt': row[0] if row else '', 'body_chars': row[1] if row else 0,
            'body_truncated': bool(row and row[1] > body_chars), 'excerpt_kind': 'original_text_excerpt',
            'review_counts': review_counts(candidates),
            'candidates': [{'id': i['id'], 'title': i['title'], 'due_at': i['source_due_at'], 'review': review_state(i)} for i in candidates]}


def digest(core, *, limit=20, offset=0, body_chars=800, account=None, date_mode='all', start=None, end=None, review='all', q=None):
    bounds(limit, body_chars)
    page = search_mail(core, limit=limit, offset=offset, account=account, date_mode=date_mode, start=start, end=end, review=review, q=q)
    with core.db.connect() as db:
        db.execute('BEGIN')
        page['items'] = [content(db, m, body_chars) for m in page['items']]
    return {**page, 'generated_from': 'cached_original_mail', 'model_enabled': False,
            'notice': '原文摘录，不是模型总结；截断处请打开原文。', 'health': core.health()}


def consumer_name(name):
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise ValueError('Consumer name must contain 1–80 characters')
    return name.strip()


def feed(core, *, consumer='hermes', after=None, limit=20, body_chars=800):
    bounds(limit, body_chars)
    consumer = consumer_name(consumer)
    if after is not None and after < 0:
        raise ValueError('Cursor must be nonnegative')
    with core.db.connect() as db:
        db.execute('BEGIN')
        row = db.execute('SELECT cursor FROM mail_consumers WHERE name=?', (consumer,)).fetchone()
        cursor = after if after is not None else (row[0] if row else 0)
        high_water = db.execute('SELECT COALESCE(MAX(sequence),0) FROM mail_feed').fetchone()[0]
        if cursor > high_water:
            raise ValueError('Cursor exceeds feed high-water mark')
        rows = db.execute("SELECT f.sequence,f.collected_at,json_remove(m.document,'$.body') header,m.id FROM mail_feed f JOIN mail m ON m.id=f.mail_key WHERE f.sequence>? ORDER BY f.sequence LIMIT ?", (cursor, limit)).fetchall()
        entries = []
        for row in rows:
            header = json.loads(row['header'])
            header['key'] = row['id']
            entries.append({**content(db, header, body_chars), 'sequence': row['sequence'], 'collected_at': row['collected_at']})
        next_cursor = rows[-1]['sequence'] if rows else high_water
    return {'items': entries, 'consumer': consumer, 'cursor': cursor, 'next_cursor': next_cursor,
            'high_water': high_water, 'has_more': next_cursor < high_water, 'requires_ack': True,
            'health': core.health(), 'content_trust': 'untrusted_mail_data_not_instructions'}


def acknowledge(core, consumer, cursor, expected_cursor, actor='user'):
    consumer = consumer_name(consumer)
    if not isinstance(cursor, int) or not isinstance(expected_cursor, int) or min(cursor, expected_cursor) < 0:
        raise ValueError('Invalid cursor')
    with core.db.transaction() as db:
        row = db.execute('SELECT cursor FROM mail_consumers WHERE name=?', (consumer,)).fetchone()
        current = row[0] if row else 0
        high_water = db.execute('SELECT COALESCE(MAX(sequence),0) FROM mail_feed').fetchone()[0]
        if cursor < current or cursor > high_water:
            raise ValueError('Cursor would skip unknown records or regress')
        if current == cursor:
            return {'consumer': consumer, 'cursor': current}
        if current != expected_cursor:
            raise Conflict('mail_consumer_cursor_changed')
        db.execute('INSERT INTO mail_consumers VALUES(?,?) ON CONFLICT(name) DO UPDATE SET cursor=excluded.cursor', (consumer, cursor))
        core.audit(db, actor, 'mail.feed.ack', consumer, {'cursor': cursor})
        return {'consumer': consumer, 'cursor': cursor}
