from __future__ import annotations

import hashlib
import json
import re
import time
import threading
import uuid
from datetime import datetime, timezone

from .db import dumps
from .mail_triage import annotate, formal, invalidate_pending_due, linked_items, migrate, pure_mail, review_state


class Conflict(ValueError):
    pass


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def iso(value):
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Datetime must include a timezone")
    return dt.astimezone(timezone.utc).isoformat() if dt.year < 2090 else None


def identifier(prefix):
    return prefix + "_" + uuid.uuid4().hex


def canonical(text):
    return re.sub(r"\W+", "", text.casefold())


class Core:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings
        self.plan_lock = threading.Lock()
        self.configuration_lock = threading.RLock()
        with self.db.transaction() as connection:
            for row in connection.execute("SELECT document FROM items WHERE json_extract(document,'$.dedup_key') IS NULL AND json_extract(document,'$.course') != ''").fetchall():
                self._put(connection, json.loads(row[0]))
        migrate(self)

    @staticmethod
    def audit(db, actor, action, entity_id, detail):
        db.execute("INSERT INTO audit(actor,action,entity_id,document,created_at) VALUES(?,?,?,?,?)", (actor, action, entity_id, dumps(detail), utcnow()))

    @staticmethod
    def _get(db, item_id):
        row = db.execute("SELECT document FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise KeyError(item_id)
        item = json.loads(row[0])
        from .tags import item_tags
        item['tags'] = item_tags(db, item_id)
        return item

    @staticmethod
    def _put(db, item):
        item["dedup_key"] = canonical(item["course"]) + ":" + canonical(item["title"]) if item.get("course") else None
        document = {k: v for k, v in item.items() if k != 'tags'}
        db.execute("INSERT INTO items VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document,version=excluded.version,updated_at=excluded.updated_at", (item["id"], dumps(document), item["version"], item["updated_at"]))

    def _new_item(self, data):
        title = str(data.get("title", "")).strip()
        if not title or len(title) > 500:
            raise ValueError("Title must contain 1–500 characters")
        estimate = data.get("estimate_minutes")
        if estimate is not None and (not isinstance(estimate, int) or not 1 <= estimate <= 10080):
            raise ValueError("Estimate must be 1–10080 minutes")
        priority = data.get("priority", 2)
        if not isinstance(priority, int) or not 0 <= priority <= 5:
            raise ValueError("Priority must be 0–5")
        due = iso(data.get("source_due_at") or data.get("due_at"))
        if data.get("review_state", "ready") not in {"ready", "needs_date", "needs_analysis", "conflict"}:
            raise ValueError("Invalid review_state")
        return {"id": identifier("item"), "title": title, "course": str(data.get("course", "")),
                "kind": data.get("kind", "task"), "status": data.get("status", "pending"),
                "source_due_at": due, "deadline_revision": 1, "scheduled_start": None, "scheduled_end": None,
                "estimate_minutes": estimate, "priority": priority, "weight_pct": data.get("weight_pct"),
                "review_state": data.get("review_state", "ready" if due else "needs_date"),
                "evidence": [], "calendar_deleted": False, "version": 1, "created_at": utcnow(), "updated_at": utcnow()}

    def create_item(self, data, actor="user", key=None):
        item = self._new_item(data)
        item['manual_origin'] = True
        with self.db.transaction() as db:
            if key:
                fp, replay = self._operation(db, "create:" + key, data)
                if replay:
                    return replay
            self._put(db, item)
            self.audit(db, actor, "item.create", item["id"], {"version": 1})
            if key:
                db.execute("INSERT INTO operations VALUES(?,?,?)", ("create:" + key, fp, dumps(item)))
        return item

    def get_item(self, item_id):
        with self.db.connect() as db:
            return self._get(db, item_id)

    def list_items(self, view='all'):
        if view not in {'all', 'formal', 'mail_pending', 'mail_ignored'}:
            raise ValueError('Unknown item view')
        with self.db.connect() as db:
            items = [json.loads(r[0]) for r in db.execute("SELECT document FROM items ORDER BY updated_at DESC")]
            from .tags import attach_tags
            attach_tags(db, items)
        return [item for item in items if view == 'all' or view == 'formal' and formal(item)
                or view == 'mail_pending' and review_state(item) == 'pending'
                or view == 'mail_ignored' and review_state(item) == 'ignored']

    def review_mail_item(self, item_id, action, expected_version, key, changes=None, actor='user'):
        changes = changes or {}
        if action not in {'confirm', 'ignore', 'restore'} or not key or len(key) > 200:
            raise ValueError('Invalid mail review action or idempotency key')
        if set(changes) - {'title', 'source_due_at', 'estimate_minutes', 'priority'} or action != 'confirm' and changes:
            raise ValueError('Invalid mail review changes')
        with self.db.transaction() as db:
            payload = {'item_id': item_id, 'action': action, 'expected_version': expected_version, 'changes': changes}
            fp, replay = self._operation(db, 'mail-review:' + key, payload)
            if replay is not None:
                return replay
            item = self._get(db, item_id)
            if item['version'] != expected_version:
                raise Conflict('item_version_changed')
            state = review_state(item)
            if not pure_mail(item) or state != ('ignored' if action == 'restore' else 'pending'):
                raise Conflict('mail_review_state_changed')
            if action == 'confirm':
                if db.execute("SELECT 1 FROM conflicts WHERE item_id=? AND state='open'", (item_id,)).fetchone():
                    raise Conflict('resolve_conflict_first')
                old_due = item['source_due_at']
                item.update(changes)
                self._new_item(item)
                item['source_due_at'] = iso(item['source_due_at'])
                if item['source_due_at'] != old_due:
                    item['deadline_revision'] = item.get('deadline_revision', 1) + 1
                item['review_state'] = 'ready' if item['source_due_at'] else 'needs_date'
            item['mail_review'] = {'state': {'confirm': 'confirmed', 'ignore': 'ignored', 'restore': 'pending'}[action],
                                   'revision': item.get('mail_review', {}).get('revision', 0) + 1,
                                   'reviewed_by': actor, 'reviewed_at': utcnow()}
            item.update(version=item['version'] + 1, updated_at=utcnow())
            self._put(db, item)
            invalidate_pending_due(db, item_id)
            self.audit(db, actor, 'mail.' + action, item_id, {'version': item['version']})
            db.execute('INSERT INTO operations VALUES(?,?,?)', ('mail-review:' + key, fp, dumps(item)))
            return item

    def update_item(self, item_id, changes, expected_version, actor="user"):
        allowed = {"title", "estimate_minutes", "priority", "status", "review_state", "source_due_at"}
        if set(changes) - allowed:
            raise ValueError("Unsupported item fields; use a plan draft to change schedule")
        with self.db.transaction() as db:
            item = self._get(db, item_id)
            if item["version"] != expected_version:
                raise Conflict("item_version_changed")
            old_due = item["source_due_at"]
            if "review_state" in changes and changes["review_state"] != "conflict" and db.execute("SELECT 1 FROM conflicts WHERE item_id=? AND state='open'", (item_id,)).fetchone():
                raise Conflict("resolve_conflict_first")
            item.update(changes)
            self._new_item(item)  # Validate editable fields.
            if "source_due_at" in changes:
                item["source_due_at"] = iso(changes["source_due_at"])
            if item["status"] not in {"pending", "in_progress", "submitted", "completed", "cancelled", "unknown"}:
                raise ValueError("Invalid status")
            item.update(version=item["version"] + 1, updated_at=utcnow())
            self._put(db, item)
            if old_due != item["source_due_at"]:
                item["deadline_revision"] = item.get("deadline_revision", 1) + 1
                self._invalidate_notices(db, item_id)
                self._put(db, item)
            self.audit(db, actor, "item.update", item_id, {"fields": sorted(changes), "version": item["version"]})
            if item.get("scheduled_start") or item["status"] in {"completed", "submitted", "cancelled"}:
                self._enqueue(db, "calendar_push", {"item_id": item_id, "version": item["version"]}, f"calendar:{item_id}:{item['version']}")
            if item["status"] in {"completed", "submitted", "cancelled"}:
                db.execute("UPDATE notifications SET state='completed' WHERE item_id=? AND state IN ('queued','failed','snoozed')", (item_id,))
        return item

    @staticmethod
    def _invalidate_notices(db, item_id):
        db.execute("UPDATE notifications SET state='superseded',lease_token=NULL,lease_until=NULL WHERE item_id=? AND state IN ('queued','failed','snoozed','sending','preview')", (item_id,))

    def ingest(self, source, records):
        out = []
        with self.db.transaction() as db:
            for record in records:
                sid = str(record["source_id"])
                normalized = {k: record[k] for k in ("title", "course", "kind", "due_at", "submission_status", "url", "weight_pct", "estimate_minutes", "review_state", "evidence_text") if k in record}
                if "due_at" in normalized:
                    normalized["due_at"] = iso(normalized["due_at"])
                previous = db.execute("SELECT * FROM source_records WHERE source=? AND source_id=?", (source, sid)).fetchone()
                if previous and previous["document"] == dumps(normalized):
                    unchanged = self._get(db, previous['item_id'])
                    annotate(db, unchanged)
                    self._put(db, unchanged)
                    out.append(unchanged)
                    continue
                item = self._get(db, previous["item_id"]) if previous else None
                if item is None and normalized.get("course"):
                    # A title is not an identity for two activities from the same
                    # source. Only preserve an unambiguous cross-source match.
                    candidates = db.execute(
                        "SELECT document FROM items i WHERE json_extract(document,'$.dedup_key')=? "
                        "AND NOT EXISTS (SELECT 1 FROM source_records s WHERE s.item_id=i.id AND s.source=?)",
                        (canonical(normalized["course"]) + ":" + canonical(normalized["title"]), source),
                    ).fetchall()
                    item = json.loads(candidates[0][0]) if len(candidates) == 1 else None
                new = item is None
                if new:
                    item = self._new_item(normalized)
                elif not item.get('evidence'):
                    item['manual_origin'] = True
                old_due = item["source_due_at"]
                old_status = item["status"]
                protected = source.startswith('gmail:') and item.get('mail_review', {}).get('state') == 'confirmed'
                if previous:
                    for field in ("title", "course", "kind", "weight_pct"):
                        if field in normalized and not (protected and field == 'title'):
                            item[field] = normalized[field]
                due = normalized.get("due_at")
                existing_evidence = [e for e in item["evidence"] if not (e["source"] == source and e["source_id"] == sid)]
                if not new and "due_at" in normalized and due != item["source_due_at"]:
                    if protected:
                        self._conflict(db, 'source_date', item['id'], {'current': item['source_due_at'], 'incoming': due, 'source': source, 'source_id': sid})
                        item['review_state'] = 'conflict'
                    elif previous and len(existing_evidence) == 0:
                        item["source_due_at"] = due
                    elif due and item["source_due_at"]:
                        self._conflict(db, "source_date", item["id"], {"current": item["source_due_at"], "incoming": due, "source": source, "source_id": sid})
                        item["review_state"] = "conflict"
                    elif due:
                        item["source_due_at"] = due
                evidence = {"source": source, "source_id": sid, **normalized, "observed_at": utcnow()}
                item["evidence"] = existing_evidence + [evidence]
                status = normalized.get("submission_status")
                # A failed observation is not evidence that submitted work reopened.
                # Keep the unknown observation in evidence, not as a destructive status update.
                if status and (status != "unknown" or new or item["status"] == "unknown"):
                    item["status"] = {"not_started": "pending"}.get(status, status)
                if item["review_state"] != "conflict" and not protected:
                    item["review_state"] = normalized.get("review_state") or ("ready" if item["source_due_at"] else "needs_date")
                annotate(db, item)
                item.update(version=item["version"] if new else item["version"] + 1, updated_at=utcnow())
                self._put(db, item)
                if old_due != item["source_due_at"]:
                    item["deadline_revision"] = item.get("deadline_revision", 1) + 1
                    self._invalidate_notices(db, item["id"])
                    self._put(db, item)
                if item["status"] in {"submitted", "completed", "cancelled"}:
                    db.execute("UPDATE notifications SET state='completed',lease_token=NULL,lease_until=NULL WHERE item_id=? AND state IN ('queued','failed','snoozed','sending','preview')", (item["id"],))
                if not new and item.get("scheduled_start") and (old_due != item["source_due_at"] or old_status != item["status"] or previous):
                    self._enqueue(db, "calendar_push", {"item_id": item["id"], "version": item["version"]}, f"calendar:{item['id']}:{item['version']}")
                db.execute("INSERT OR REPLACE INTO source_records VALUES(?,?,?,?)", (source, sid, item["id"], dumps(normalized)))
                self.audit(db, "source:" + source, "item.ingest", item["id"], {"source_id": sid, "version": item["version"]})
                out.append(item)
        self.source_health(source, True, detail={"records": len(records)})
        return out

    def source_health(self, source, ok, error=None, detail=None):
        now = utcnow()
        with self.db.transaction() as db:
            old = db.execute("SELECT last_success FROM sources WHERE id=?", (source,)).fetchone()
            db.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?,?)", (source, "ok" if ok else "error", now if ok else (old[0] if old else None), now, error, dumps(detail or {})))

    def health(self):
        with self.db.connect() as db:
            sources = [dict(r) for r in db.execute("SELECT * FROM sources ORDER BY id")]
        for source in sources:
            last = source.get("last_success")
            source["stale"] = not last or (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() > max(900, self.settings.sync_interval_seconds * 3)
        heartbeat = self.db.get_meta("heartbeat")
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(heartbeat)).total_seconds() if heartbeat else None
        return {"mode": self.settings.mode, "model_enabled": False, "heartbeat": heartbeat,
                "worker_online": age is not None and age < 90, "sources": sources,
                "updated_at": utcnow(), "data_stale": age is None or age >= 90}

    def resolve_conflict(self, conflict_id, choice, expected_version, actor="user"):
        if choice not in {"local", "remote"}:
            raise ValueError("choice must be local or remote")
        from .calendars import event_hash, event_time
        with self.db.transaction() as db:
            row = db.execute("SELECT * FROM conflicts WHERE id=?", (conflict_id,)).fetchone()
            if not row:
                raise KeyError(conflict_id)
            item = self._get(db, row["item_id"])
            if row["state"] != "open" or item["version"] != expected_version:
                raise Conflict("conflict_or_item_version_changed")
            detail = json.loads(row["document"])
            old_due = item["source_due_at"]
            if row["kind"] == "source_date":
                if choice == "remote":
                    item["source_due_at"] = iso(detail["incoming"])
            elif row["kind"] in {"calendar_edit", "calendar_write"}:
                event = detail.get("remote")
                if not event:
                    cached = db.execute("SELECT document FROM calendar_events WHERE provider=? AND calendar_id=? AND event_id=?", (detail["provider"], detail["calendar_id"], detail["event_id"])).fetchone()
                    event = json.loads(cached[0]) if cached else None
                if not event:
                    raise Conflict("refresh_calendar_before_resolving")
                if row["kind"] == "calendar_write" and detail.get("status") == 412 and not detail.get("remote"):
                    # The current cache must be newer than the failed conditional request.
                    if event.get("etag") == detail.get("observed_etag"):
                        raise Conflict("refresh_calendar_before_resolving")
                if choice == "remote":
                    if event.get("status") == "cancelled":
                        item.update(calendar_deleted=True, scheduled_start=None, scheduled_end=None)
                    else:
                        a, b = event_time(event.get("start", {}), self.settings.timezone), event_time(event.get("end", {}), self.settings.timezone)
                        if not a or not b:
                            raise ValueError("Remote event has no valid schedule")
                        item.update(scheduled_start=a.isoformat(), scheduled_end=b.isoformat(), title=event.get("summary", item["title"]))
                elif event.get("status") == "cancelled":
                    # Recreating a deleted event requires a new, explicitly confirmed draft.
                    item.update(calendar_deleted=True, scheduled_start=None, scheduled_end=None)
                db.execute("UPDATE calendar_mappings SET document=? WHERE provider=? AND calendar_id=? AND event_id=?", (dumps({"etag": event.get("etag"), "hash": event_hash(event), "deleted": event.get("status") == "cancelled"}), detail["provider"], detail["calendar_id"], detail["event_id"]))
                db.execute("UPDATE jobs SET status='superseded' WHERE kind='calendar_push' AND json_extract(payload,'$.item_id')=? AND status IN ('queued','failed','preview')", (item["id"],))
            else:
                raise ValueError("Unsupported conflict kind")
            item.update(version=item["version"] + 1, updated_at=utcnow())
            db.execute("UPDATE conflicts SET state='resolved' WHERE id=?", (conflict_id,))
            remaining = db.execute("SELECT 1 FROM conflicts WHERE item_id=? AND state='open'", (item["id"],)).fetchone()
            item["review_state"] = "conflict" if remaining else ("ready" if item["source_due_at"] else "needs_date")
            self._put(db, item)
            if old_due != item["source_due_at"]:
                item["deadline_revision"] = item.get("deadline_revision", 1) + 1
                self._invalidate_notices(db, item["id"])
                self._put(db, item)
                if item.get("scheduled_start"):
                    self._enqueue(db, "calendar_push", {"item_id": item["id"], "version": item["version"]}, f"calendar:{item['id']}:{item['version']}")
            if choice == "local" and row["kind"] != "source_date" and not item["calendar_deleted"]:
                self._enqueue(db, "calendar_push", {"item_id": item["id"], "version": item["version"]}, f"calendar:{item['id']}:{item['version']}")
            self.audit(db, actor, "conflict.resolve", conflict_id, {"choice": choice, "version": item["version"]})
            return item

    def _conflict(self, db, kind, item_id, detail):
        # One open conflict per exact fact; subsequent polls do not flood the inbox.
        encoded = dumps(detail)
        row = db.execute("SELECT id FROM conflicts WHERE kind=? AND item_id=? AND document=? AND state='open'", (kind, item_id, encoded)).fetchone()
        if row:
            return row[0]
        cid = identifier("conflict")
        db.execute("INSERT INTO conflicts(id,kind,item_id,document,created_at) VALUES(?,?,?,?,?)", (cid, kind, item_id, encoded, utcnow()))
        return cid

    def list_conflicts(self):
        with self.db.connect() as db:
            return [{**dict(r), "document": json.loads(r["document"])} for r in db.execute("SELECT * FROM conflicts WHERE state='open' ORDER BY created_at DESC")]

    @staticmethod
    def _operation(db, key, payload):
        fingerprint = hashlib.sha256(dumps(payload).encode()).hexdigest()
        row = db.execute("SELECT * FROM operations WHERE key=?", (key,)).fetchone()
        if row and row["fingerprint"] != fingerprint:
            raise Conflict("idempotency_key_payload_mismatch")
        return fingerprint, json.loads(row["result"]) if row else None

    def create_draft(self, changes, key, actor="user", metadata=None):
        if not key or not changes or len(changes) > 500:
            raise ValueError("A key and 1–500 changes are required")
        clean = []
        seen = set()
        for change in changes:
            if not isinstance(change, dict) or set(change) != {"item_id", "expected_version", "start", "end"}:
                raise ValueError("Each change requires item_id, expected_version, start and end")
            if not isinstance(change["item_id"], str) or type(change["expected_version"]) is not int or change["expected_version"] < 1:
                raise ValueError("Invalid item identity or version")
            if change["item_id"] in seen:
                raise ValueError("Only one schedule change per item is allowed")
            seen.add(change["item_id"])
            start, end = iso(change.get("start")), iso(change.get("end"))
            if not start or not end or end <= start:
                raise ValueError("A positive, timezone-aware schedule is required")
            clean.append({"item_id": change["item_id"], "expected_version": change["expected_version"], "start": start, "end": end})
        with self.db.transaction() as db:
            fp, replay = self._operation(db, "draft:" + key, clean)
            if replay:
                return replay
            for change in clean:
                item = self._get(db, change["item_id"])
                if not formal(item):
                    raise Conflict('mail_candidate_requires_review')
                if item["version"] != change["expected_version"]:
                    raise Conflict("item_version_changed")
            result = {"id": identifier("draft"), "changes": clean, "state": "draft", "created_at": utcnow(), "metadata": metadata or {}}
            db.execute("INSERT INTO drafts VALUES(?,?,?,?)", (result["id"], dumps(result), "draft", result["created_at"]))
            db.execute("INSERT INTO operations VALUES(?,?,?)", ("draft:" + key, fp, dumps(result)))
            self.audit(db, actor, "draft.create", result["id"], {"changes": len(clean)})
        return result

    def list_drafts(self):
        with self.db.connect() as db:
            return [{**json.loads(r["document"]), "state": r["state"]} for r in db.execute("SELECT * FROM drafts ORDER BY created_at DESC")]

    def confirm_draft(self, draft_id, key, actor="user"):
        with self.db.transaction() as db:
            fp, replay = self._operation(db, "confirm:" + key, {"draft_id": draft_id})
            if replay:
                return replay
            row = db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if not row:
                raise KeyError(draft_id)
            if row["state"] != "draft":
                raise Conflict("draft_already_confirmed")
            draft = json.loads(row["document"])
            items = [self._get(db, c["item_id"]) for c in draft["changes"]]
            self._validate_schedule(db, draft["changes"])
            for item, change in zip(items, draft["changes"]):
                if not formal(item):
                    raise Conflict('mail_candidate_requires_review')
                if item["version"] != change["expected_version"]:
                    raise Conflict("item_version_changed")
                if item["status"] in {"completed", "submitted", "cancelled"}:
                    raise Conflict("item_already_closed")
                if item["source_due_at"] and change["end"] > item["source_due_at"]:
                    raise Conflict("schedule_after_deadline")
            jobs = []
            for item, change in zip(items, draft["changes"]):
                item.update(scheduled_start=change["start"], scheduled_end=change["end"], calendar_deleted=False, version=item["version"] + 1, updated_at=utcnow())
                self._put(db, item)
                jobs.append(self._enqueue(db, "calendar_push", {"item_id": item["id"], "version": item["version"]}, f"calendar:{item['id']}:{item['version']}"))
            db.execute("UPDATE drafts SET state='confirmed' WHERE id=?", (draft_id,))
            result = {"draft_id": draft_id, "state": "confirmed", "job_ids": jobs}
            db.execute("INSERT INTO operations VALUES(?,?,?)", ("confirm:" + key, fp, dumps(result)))
            self.audit(db, actor, "draft.confirm", draft_id, result)
            return result

    def _validate_schedule(self, db, changes):
        from .calendars import calendar_busy
        from .sources import local_busy
        if self.settings.mode == "live":
            for account in self.settings.google_accounts:
                for calendar in account.get("calendars", []):
                    if not calendar.get('read', True) and not calendar.get('write'):
                        continue
                    source = f"calendar:google:{account['id']}:{calendar['id']}"
                    health = db.execute("SELECT status,last_success FROM sources WHERE id=?", (source,)).fetchone()
                    if not health or health["status"] != "ok" or not health["last_success"] or (datetime.now(timezone.utc) - datetime.fromisoformat(health["last_success"])).total_seconds() > max(900, self.settings.sync_interval_seconds * 3):
                        raise Conflict("calendar_snapshot_stale; sync calendars before confirming")
        ids = {c["item_id"] for c in changes}
        earliest = min(datetime.fromisoformat(c["start"]) for c in changes)
        latest = max(datetime.fromisoformat(c["end"]) for c in changes)
        days = (latest - earliest).days + 2
        if days > 366:
            raise ValueError("A draft may span at most one year")
        busy = calendar_busy(self, earliest, days, exclude_items=ids) + local_busy(self, earliest, days)
        for row in db.execute("SELECT document FROM items"):
            item = json.loads(row[0])
            if item["id"] not in ids and item.get("scheduled_start") and item["status"] not in {"completed", "submitted", "cancelled"}:
                busy.append({"start": item["scheduled_start"], "end": item["scheduled_end"]})
        for change in changes:
            a, b = datetime.fromisoformat(change["start"]), datetime.fromisoformat(change["end"])
            if any(a < datetime.fromisoformat(slot["end"]) and b > datetime.fromisoformat(slot["start"]) for slot in busy):
                raise Conflict("schedule_overlap; refresh and adjust the draft")
            busy.append(change)

    @staticmethod
    def _enqueue(db, kind, payload, unique_key):
        existing = db.execute("SELECT id FROM jobs WHERE unique_key=?", (unique_key,)).fetchone()
        if existing:
            return existing[0]
        jid = identifier("job")
        db.execute("INSERT INTO jobs(id,kind,payload,status,due_at,unique_key,created_at) VALUES(?,?,?,?,?,?,?)", (jid, kind, dumps(payload), "queued", time.time(), unique_key, utcnow()))
        return jid

    def enqueue(self, kind, payload, key, actor="system"):
        with self.db.transaction() as db:
            # Prevent a key from silently targeting a different action.
            old = db.execute("SELECT kind,payload FROM jobs WHERE unique_key=?", (key,)).fetchone()
            if old and (old["kind"] != kind or old["payload"] != dumps(payload)):
                raise Conflict("idempotency_key_payload_mismatch")
            jid = self._enqueue(db, kind, payload, key)
            self.audit(db, actor, "job.enqueue", jid, {"kind": kind})
            return {"id": jid}

    def list_jobs(self):
        with self.db.connect() as db:
            return [{**dict(r), "payload": json.loads(r["payload"]), "result": json.loads(r["result"]) if r["result"] else None} for r in db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 200")]

    def list_mail(self, limit=100, offset=0, query=None):
        if not 1 <= limit <= 300 or offset < 0:
            raise ValueError("Mail pagination requires limit 1–300 and offset >= 0")
        with self.db.connect() as db:
            if query:
                if len(query) > 500:
                    raise ValueError("Search query is too long")
                escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                rows = db.execute("SELECT json_remove(document,'$.body') FROM mail WHERE document LIKE ? ESCAPE '\\' ORDER BY received_at DESC LIMIT ? OFFSET ?", ("%" + escaped + "%", limit, offset))
            else:
                rows = db.execute("SELECT json_remove(document,'$.body') FROM mail ORDER BY received_at DESC LIMIT ? OFFSET ?", (limit, offset))
            return [json.loads(r[0]) for r in rows]

    def get_mail(self, mail_key):
        with self.db.connect() as db:
            row = db.execute("SELECT document FROM mail WHERE id=?", (mail_key,)).fetchone()
            if not row:
                raise KeyError(mail_key)
            result = json.loads(row[0])
            result['linked_items'] = linked_items(db, mail_key)
            return result

    def list_artifacts(self):
        with self.db.connect() as db:
            return [{"id": r["id"], "kind": r["kind"], **json.loads(r["document"])} for r in db.execute("SELECT * FROM artifacts")]

    def list_calendars(self):
        with self.db.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT document FROM calendars")]
