"""One shared classification for mail review, queries and background effects."""
from __future__ import annotations

import json
import uuid

from .db import dumps


def mail_evidence(item):
    return [e for e in item.get('evidence', []) if e.get('source', '').startswith('gmail:')]


def pure_mail(item):
    evidence = item.get('evidence', [])
    return not item.get('manual_origin') and bool(evidence) and len(mail_evidence(item)) == len(evidence)


def review_state(item):
    if not mail_evidence(item):
        return None
    if not pure_mail(item):
        return 'confirmed'
    saved = item.get('mail_review', {}).get('state')
    if saved in {'pending', 'confirmed', 'ignored'}:
        return saved
    if item.get('status') in {'completed', 'submitted', 'cancelled'} or item.get('scheduled_start') or item.get('review_state') == 'ready':
        return 'confirmed'
    return 'pending'


def formal(item):
    return not pure_mail(item) or review_state(item) == 'confirmed'


def review_revision(item):
    return item.get('mail_review', {}).get('revision', 0)


def annotate(db, item):
    evidence = mail_evidence(item)
    if not evidence:
        return
    if db.execute("SELECT 1 FROM audit WHERE entity_id=? AND action='item.create' LIMIT 1", (item['id'],)).fetchone():
        item['manual_origin'] = True
    if 'mail_review' not in item:
        item['mail_review'] = {'state': review_state(item), 'revision': 0}
    keys = set(item.get('mail_keys', []))
    for entry in evidence:
        account = entry['source'][len('gmail:'):]
        message_id = entry.get('source_id', '').split(':', 1)[0]
        rows = db.execute("SELECT id FROM mail WHERE account=? AND json_extract(document,'$.id')=?",
                          (account, message_id)).fetchall()
        if len(rows) == 1:
            keys.add(rows[0][0])
    item['mail_keys'] = sorted(keys)


def invalidate_pending_due(db, item_id):
    db.execute("UPDATE notifications SET state='superseded',lease_until=NULL,lease_token=NULL "
               "WHERE item_id=? AND json_type(document,'$.source_due_at') IS NOT NULL "
               "AND state IN ('queued','failed','sending','snoozed','preview')", (item_id,))


def migrate(core):
    with core.db.transaction() as db:
        changes = []
        for row in db.execute('SELECT document FROM items').fetchall():
            item = json.loads(row[0])
            before = dumps(item)
            annotate(db, item)
            if dumps(item) != before:
                changes.append(item)
        if changes:
            # The reserved write lock holds the snapshot stable; backup reads WAL
            # through a separate read connection before any migration writes.
            target = core.settings.data_dir / 'backups' / ('mail-triage-' + uuid.uuid4().hex + '.sqlite3')
            core.db.backup(target)
            for item in changes:
                core._put(db, item)
        for row in db.execute('SELECT document FROM items').fetchall():
            item = json.loads(row[0])
            if not formal(item):
                invalidate_pending_due(db, item['id'])


def linked_items(db, mail_key):
    rows = db.execute("SELECT DISTINCT i.document FROM items i,json_each(i.document,'$.mail_keys') k WHERE k.value=?",
                      (mail_key,)).fetchall()
    return [json.loads(row[0]) for row in rows]


def review_counts(items):
    return {state: sum(review_state(item) == state for item in items) for state in ('pending', 'confirmed', 'ignored')}
