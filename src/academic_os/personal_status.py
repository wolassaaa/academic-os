"""Personal workflow overrides do not rewrite source submission observations."""
from __future__ import annotations

import json
import uuid

from .db import dumps

STATES = {'pending', 'in_progress', 'submitted', 'completed', 'cancelled', 'unknown'}


def record(item, state, actor, at):
    if state not in STATES:
        raise ValueError('Invalid status')
    item['personal_status'] = {'state': state, 'origin': 'explicit', 'actor': actor, 'updated_at': at}


def initialize(item):
    if 'personal_status' in item:
        return False
    if item.get('status') == 'completed':
        # Preserve old closure without inventing user or submission provenance.
        item['personal_status'] = {'state': 'completed', 'origin': 'legacy_completed', 'actor': None, 'updated_at': None}
    else:
        # Also mark old active records as unmanaged before later source completion.
        item['personal_status'] = None
    return True


def observe(item, state, *, new=False):
    personal = item.get('personal_status')
    if isinstance(personal, dict) and personal.get('state') in STATES:
        item['status'] = personal['state']
    elif state and (state != 'unknown' or new or item['status'] == 'unknown'):
        # Keep the previous source-only unknown-observation behavior compatible.
        item['status'] = {'not_started': 'pending'}.get(state, state)


def migrate(core):
    with core.db.transaction() as db:
        changed = []
        for row in db.execute("SELECT document FROM items WHERE json_type(document,'$.personal_status') IS NULL"):
            item = json.loads(row[0])
            if initialize(item):
                changed.append(item)
        if not changed:
            return
        # Hold a reserved write lock; back up the committed pre-migration snapshot.
        target = core.settings.data_dir / 'backups' / ('personal-status-' + uuid.uuid4().hex + '.sqlite3')
        core.db.backup(target)
        for item in changed:
            # Do not reclassify, increment business versions, or rewrite history.
            db.execute('UPDATE items SET document=? WHERE id=?', (dumps(item), item['id']))
