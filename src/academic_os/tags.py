"""Small relational, user-owned tag catalog. Ingestion never rewrites relations."""
from __future__ import annotations

import sqlite3
import json
import unicodedata

from .core import Conflict, identifier, utcnow
from .db import dumps


def item_tags(db, item_id):
    manual = [{**dict(row), 'origin': 'manual'} for row in db.execute('SELECT t.id,t.name,t.kind,t.scope,t.version,t.archived FROM tags t JOIN item_tags x ON x.tag_id=t.id WHERE x.item_id=? ORDER BY t.kind,t.name,t.id', (item_id,))]
    row = db.execute("SELECT json_extract(document,'$.automatic_tags') FROM items WHERE id=?", (item_id,)).fetchone()
    return manual + (json.loads(row[0]) if row and row[0] else [])


def attach_tags(db, items):
    by_id = {item['id']: item for item in items}
    for item in items:
        item['tags'] = []
    for row in db.execute('SELECT x.item_id,t.id,t.name,t.kind,t.scope,t.version,t.archived FROM item_tags x JOIN tags t ON t.id=x.tag_id ORDER BY t.kind,t.name,t.id'):
        if row['item_id'] in by_id:
            by_id[row['item_id']]['tags'].append({**{k: row[k] for k in row.keys() if k != 'item_id'}, 'origin': 'manual'})
    for item in items:
        item['tags'] += item.get('automatic_tags', [])


def validated(name, kind, scope):
    if not all(isinstance(x, str) for x in (name, kind, scope)):
        raise ValueError('Tag fields must be strings')
    name, scope = name.strip(), scope.strip()
    if not name or len(name) > 80 or len(scope) > 200 or kind not in {'course', 'assignment', 'group', 'custom'}:
        raise ValueError('Invalid tag name, kind or scope')
    return name, kind, scope, unicodedata.normalize('NFKC', name).casefold()


class Tags:
    def __init__(self, core):
        self.core = core

    @staticmethod
    def _get(db, tag_id):
        row = db.execute('SELECT id,name,kind,scope,version,archived FROM tags WHERE id=?', (tag_id,)).fetchone()
        if not row:
            raise KeyError(tag_id)
        return dict(row)

    def list(self, include_archived=False):
        with self.core.db.connect() as db:
            return [dict(row) for row in db.execute('SELECT id,name,kind,scope,version,archived FROM tags' + ('' if include_archived else ' WHERE archived=0') + ' ORDER BY kind,scope,name,id')]

    def create(self, name, kind, scope, key, actor='user'):
        name, kind, scope, name_key = validated(name, kind, scope)
        if not key or len(key) > 200:
            raise ValueError('Idempotency key required')
        with self.core.db.transaction() as db:
            fp, replay = self.core._operation(db, 'tag-create:' + key, {'name': name, 'kind': kind, 'scope': scope})
            if replay is not None:
                return replay
            tag_id = identifier('tag')
            try:
                db.execute('INSERT INTO tags VALUES(?,?,?,?,?,?,0)', (tag_id, name, kind, scope, name_key, 1))
            except sqlite3.IntegrityError:
                raise Conflict('tag_already_exists') from None
            result = self._get(db, tag_id)
            self.core.audit(db, actor, 'tag.create', tag_id, {})
            db.execute('INSERT INTO operations VALUES(?,?,?)', ('tag-create:' + key, fp, dumps(result)))
            return result

    def update(self, tag_id, changes, expected_version, actor='user'):
        if not changes or set(changes) - {'name', 'archived'} or 'archived' in changes and not isinstance(changes['archived'], bool):
            raise ValueError('Only tag name and archived boolean are editable')
        with self.core.db.transaction() as db:
            tag = self._get(db, tag_id)
            if tag['version'] != expected_version:
                raise Conflict('tag_version_changed')
            name, _, _, name_key = validated(changes.get('name', tag['name']), tag['kind'], tag['scope'])
            try:
                db.execute('UPDATE tags SET name=?,name_key=?,archived=?,version=version+1 WHERE id=?', (name, name_key, changes.get('archived', tag['archived']), tag_id))
            except sqlite3.IntegrityError:
                raise Conflict('tag_already_exists') from None
            self.core.audit(db, actor, 'tag.update', tag_id, {'fields': sorted(changes)})
            return self._get(db, tag_id)

    def assign(self, item_id, tag_ids, expected_version, key, actor='user'):
        if not isinstance(tag_ids, list) or len(tag_ids) > 30 or any(not isinstance(x, str) for x in tag_ids) or len(tag_ids) != len(set(tag_ids)) or not key or len(key) > 200:
            raise ValueError('Choose at most 30 distinct tag IDs and an idempotency key')
        with self.core.db.transaction() as db:
            fp, replay = self.core._operation(db, 'item-tags:' + key, {'item_id': item_id, 'tag_ids': sorted(tag_ids), 'expected_version': expected_version})
            if replay is not None:
                return replay
            item = self.core._get(db, item_id)
            if item['version'] != expected_version:
                raise Conflict('item_version_changed')
            existing = {t['id'] for t in item['tags']}
            automatic = {t['id'] for t in item.get('automatic_tags', [])}
            for tag_id in tag_ids:
                if tag_id in automatic:
                    continue
                try:
                    tag = self._get(db, tag_id)
                except KeyError:
                    raise ValueError('Unknown tag') from None
                if tag['archived'] and tag_id not in existing:
                    raise ValueError('Archived tag cannot be newly assigned')
            db.execute('DELETE FROM item_tags WHERE item_id=?', (item_id,))
            db.executemany('INSERT INTO item_tags VALUES(?,?)', [(item_id, t) for t in tag_ids if t not in automatic])
            approved = db.execute("SELECT id FROM jobs WHERE kind='calendar_push' AND json_extract(payload,'$.item_id')=? AND json_extract(payload,'$.version')=? AND status IN ('queued','failed','running')", (item_id, item['version'])).fetchall()
            item.update(version=item['version'] + 1, updated_at=utcnow())
            self.core._put(db, item)
            if approved:
                # Carry only an existing authorized effect across this metadata-only
                # revision. Labeling an otherwise unscheduled item creates no job.
                db.executemany("UPDATE jobs SET status='superseded',lease_token=NULL,lease_until=NULL WHERE id=?", [(r['id'],) for r in approved])
                self.core._enqueue(db, 'calendar_push', {'item_id': item_id, 'version': item['version']}, f"calendar:{item_id}:{item['version']}")
            result = self.core._get(db, item_id)
            self.core.audit(db, actor, 'item.tags', item_id, {'tag_ids': sorted(tag_ids)})
            db.execute('INSERT INTO operations VALUES(?,?,?)', ('item-tags:' + key, fp, dumps(result)))
            return result

    @staticmethod
    def filter_items(items, tag_ids):
        selected = set(tag_ids)
        return [i for i in items if selected <= {t['id'] for t in i.get('tags', [])}]
