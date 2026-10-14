from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr

from .core import Conflict, utcnow
from .db import dumps
from .providers import ProviderError


def event_time(value, default_timezone):
    if value.get("dateTime"):
        raw = value["dateTime"].replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(value.get("timeZone", default_timezone)))
        return dt.astimezone(timezone.utc)
    if value.get("date"):
        return datetime.fromisoformat(value["date"]).replace(tzinfo=ZoneInfo(value.get("timeZone", default_timezone))).astimezone(timezone.utc)
    return None


def event_hash(event):
    # Ignore transport fields and timestamps; compare fields managed by this application.
    fields = {k: event.get(k, "") for k in ("summary", "description")}
    fields.update(status=event.get("status", "confirmed"), transparency=event.get("transparency", "opaque"))
    for key in ("start", "end"):
        value = event.get(key, {})
        fields[key] = value.get("date") or (event_time(value, "UTC").isoformat() if value.get("dateTime") else None)
    return hashlib.sha256(dumps(fields).encode()).hexdigest()


def active_calendars(core, purpose='sync'):
    return {('google:' + a['id'], c['id']) for a in core.settings.google_accounts for c in a.get('calendars', [])
            if c.get('read', True) or (purpose == 'sync' and c.get('write'))}


class CalendarSync:
    def __init__(self, core, providers, feishu=None):
        self.core, self.providers, self.feishu = core, providers, feishu

    def pull(self):
        results = []
        for account in self.core.settings.google_accounts:
            provider = self.providers.get(account["id"])
            if provider is None:
                continue
            for calendar in account.get("calendars", []):
                if not calendar.get('read', True) and not calendar.get('write'):
                    continue
                cal_id = calendar["id"]
                provider_id = "google:" + account["id"]
                cursor_key = f"calendar:{provider_id}:{cal_id}"
                with self.core.db.connect() as db:
                    row = db.execute("SELECT value FROM cursors WHERE key=?", (cursor_key,)).fetchone()
                cursor = json.loads(row[0]) if row else None
                events, page, next_cursor = [], None, None
                started, seen_pages = time.monotonic(), set()
                try:
                    while True:
                        self._check_budget(started)
                        if page in seen_pages or len(seen_pages) >= 1000:
                            raise ValueError("calendar_pagination_cycle_or_limit")
                        seen_pages.add(page)
                        try:
                            payload = provider.list_events(cal_id, cursor, page)
                        except ProviderError as exc:
                            if exc.status == 410 and cursor:
                                cursor, page, events = None, None, []
                                seen_pages.clear()
                                continue
                            raise
                        for event in payload.get("items", []):
                            calendar_zone = payload.get("timeZone") or calendar.get("timezone") or self.core.settings.timezone
                            for boundary in ("start", "end", "originalStartTime"):
                                if event.get(boundary, {}).get("date"):
                                    event[boundary].setdefault("timeZone", calendar_zone)
                            events.append(event)
                        page = payload.get("nextPageToken")
                        if not page:
                            next_cursor = payload.get("nextSyncToken")
                            break
                    with self.core.configuration_lock, self.core.db.transaction() as db:
                        if (provider_id, cal_id) not in active_calendars(self.core):
                            continue
                        if not cursor:
                            incoming = {event["id"] for event in events}
                            old_events = db.execute("SELECT event_id FROM calendar_events WHERE provider=? AND calendar_id=?", (provider_id, cal_id)).fetchall()
                            for old_event in old_events:
                                if old_event[0] not in incoming:
                                    self._read_event(db, provider_id, cal_id, {"id": old_event[0], "status": "cancelled", "etag": "absent-in-full-snapshot"})
                        for event in events:
                            self._read_event(db, provider_id, cal_id, event)
                        if next_cursor:
                            db.execute("INSERT OR REPLACE INTO cursors VALUES(?,?)", (cursor_key, dumps(next_cursor)))
                        db.execute("INSERT OR REPLACE INTO calendars VALUES(?,?)", (cursor_key, dumps({"id": cursor_key, "provider": provider_id, "calendar_id": cal_id, "name": calendar.get("name", cal_id), "write": bool(calendar.get("write")), "last_success": utcnow(), "events_changed": len(events), "direction": "editable"})))
                    self.core.source_health(cursor_key, True, detail={"events_changed": len(events)})
                    results.append({"calendar_id": cal_id, "events": len(events)})
                except Exception as exc:
                    self.core.source_health(cursor_key, False, "calendar_pull_failed")
                    results.append({"calendar_id": cal_id, "error": type(exc).__name__})
        return results

    def _check_budget(self, started):
        if time.monotonic() - started > 300:
            raise TimeoutError("calendar_operation_budget_exceeded")
        stop = getattr(self.core, "stop_event", None)
        if stop is not None and stop.is_set():
            raise InterruptedError("runtime_stopping")

    def _read_event(self, db, provider_id, calendar_id, event):
        db.execute("INSERT OR REPLACE INTO calendar_events VALUES(?,?,?,?)", (provider_id, calendar_id, event["id"], dumps(event)))
        row = db.execute("SELECT * FROM calendar_mappings WHERE provider=? AND calendar_id=? AND event_id=?", (provider_id, calendar_id, event["id"])).fetchone()
        if not row:
            return
        mapping = json.loads(row["document"])
        if mapping.get("purpose") == "legacy_deadline":
            mapping.update(etag=event.get("etag"), hash=event_hash(event), deleted=event.get("status") == "cancelled")
            db.execute("UPDATE calendar_mappings SET document=? WHERE provider=? AND calendar_id=? AND event_id=?", (dumps(mapping), provider_id, calendar_id, event["id"]))
            return
        item = self.core._get(db, row["item_id"])
        if event.get("etag") == mapping.get("etag"):
            return
        if event.get("status") == "cancelled":
            item.update(calendar_deleted=True, scheduled_start=None, scheduled_end=None, version=item["version"] + 1, updated_at=utcnow())
            self.core._put(db, item)
            mapping.update(deleted=True, etag=event.get("etag"))
        else:
            start = event_time(event.get("start", {}), self.core.settings.timezone)
            end = event_time(event.get("end", {}), self.core.settings.timezone)
            if not start or not end:
                return
            changed = start.isoformat() != item["scheduled_start"] or end.isoformat() != item["scheduled_end"] or event.get("summary", item["title"]) != item["title"]
            pending = db.execute("SELECT 1 FROM jobs WHERE kind='calendar_push' AND status IN ('queued','running','failed') AND json_extract(payload,'$.item_id')=?", (item["id"],)).fetchone()
            if changed and pending:
                self.core._conflict(db, "calendar_edit", item["id"], {"provider": provider_id, "calendar_id": calendar_id, "event_id": event["id"], "remote": event, "local_version": item["version"]})
                item["review_state"] = "conflict"
                self.core._put(db, item)
                return
            if changed:
                item.update(scheduled_start=start.isoformat(), scheduled_end=end.isoformat(), title=event.get("summary", item["title"]), version=item["version"] + 1, updated_at=utcnow())
                self.core._put(db, item)
                self.core.audit(db, "google", "calendar.manual_edit", item["id"], {"event_id": event["id"]})
            mapping.update(etag=event.get("etag"), hash=event_hash(event), version=item["version"])
        db.execute("UPDATE calendar_mappings SET document=? WHERE provider=? AND calendar_id=? AND event_id=?", (dumps(mapping), provider_id, calendar_id, event["id"]))

    def push(self, item_id, expected_version=None):
        item = self.core.get_item(item_id)
        from .mail_triage import formal
        if not formal(item):
            return {'skipped': 'mail_candidate_requires_review'}
        if self.core.settings.mode == "shadow":
            return {"shadow": True, "item_id": item_id}
        if item["calendar_deleted"] or item["review_state"] == "conflict":
            return {"skipped": "deleted_or_conflicted"}
        if expected_version is not None and expected_version != item["version"]:
            return {"skipped": "superseded_version"}
        target = next(((a, c) for a in self.core.settings.google_accounts for c in a.get("calendars", []) if c.get("write")), None)
        if not target:
            raise RuntimeError("No writable Google calendar configured")
        account, calendar = target
        provider_id, cal_id = "google:" + account["id"], calendar["id"]
        provider = self.providers[account["id"]]
        with self.core.db.connect() as db:
            row = db.execute("SELECT * FROM calendar_mappings WHERE provider=? AND calendar_id=? AND item_id=? AND COALESCE(json_extract(document,'$.purpose'),'execution') != 'legacy_deadline'", (provider_id, cal_id, item_id)).fetchone()
        mapping = json.loads(row["document"]) if row else {}
        event_id = row["event_id"] if row else "aos" + hashlib.sha256(f"{provider_id}:{cal_id}:{item_id}".encode()).hexdigest()[:40]
        if item["status"] in {"completed", "submitted", "cancelled"}:
            if row and not mapping.get("deleted"):
                provider.delete_event(cal_id, event_id, mapping.get("etag"))
                mapping["deleted"] = True
                with self.core.db.transaction() as db:
                    db.execute("UPDATE calendar_mappings SET document=? WHERE provider=? AND calendar_id=? AND event_id=?", (dumps(mapping), provider_id, cal_id, event_id))
            return {"cancelled": bool(row)}
        if not item.get("scheduled_start"):
            return {"skipped": "not_scheduled"}
        body = {"summary": item["title"], "start": {"dateTime": item["scheduled_start"], "timeZone": self.core.settings.timezone}, "end": {"dateTime": item["scheduled_end"], "timeZone": self.core.settings.timezone}, "description": f"Academic OS item: {item_id}\n来源截止（不随安排移动）：{item['source_due_at'] or '待公布'}", "extendedProperties": {"private": {"academic_os_item": item_id}}, "transparency": "opaque", "status": "confirmed"}
        digest = event_hash(body)
        if mapping.get("hash") == digest and not mapping.get("deleted"):
            return {"unchanged": True, "event_id": event_id}
        try:
            remote = provider.put_event(cal_id, event_id, body, mapping.get("etag"), create=not row)
        except ProviderError as exc:
            if exc.status in {404, 410, 412}:
                with self.core.db.transaction() as db:
                    detail = {"event_id": event_id, "status": exc.status, "provider": provider_id, "calendar_id": cal_id, "observed_etag": mapping.get("etag")}
                    if exc.status in {404, 410}:
                        detail["remote"] = {"id": event_id, "status": "cancelled"}
                    self.core._conflict(db, "calendar_write", item_id, detail)
                    current = self.core._get(db, item_id)
                    current["review_state"] = "conflict"
                    self.core._put(db, current)
                raise Conflict("remote_event_changed_or_deleted") from exc
            raise
        mapping = {"etag": remote.get("etag"), "hash": event_hash(remote), "version": item["version"], "deleted": False, "last_success": utcnow()}
        divergent = mapping["hash"] != digest
        with self.core.db.transaction() as db:
            db.execute("INSERT OR REPLACE INTO calendar_mappings VALUES(?,?,?,?,?)", (provider_id, cal_id, event_id, item_id, dumps(mapping)))
            db.execute("INSERT OR REPLACE INTO calendar_events VALUES(?,?,?,?)", (provider_id, cal_id, event_id, dumps(remote)))
            if divergent:
                current = self.core._get(db, item_id)
                self.core._conflict(db, "calendar_edit", item_id, {"provider": provider_id, "calendar_id": cal_id, "event_id": event_id, "remote": remote, "local_version": current["version"]})
                current["review_state"] = "conflict"
                self.core._put(db, current)
        if divergent:
            raise Conflict("remote_event_changed_after_interrupted_creation")
        self.core.source_health("calendar:write", True)
        return {"event_id": event_id}

    def mirror(self):
        if not self.feishu or not self.core.settings.feishu.get("calendar_id"):
            return {"skipped": "feishu_not_configured"}
        if self.core.settings.mode == "shadow":
            return {"shadow": True}
        target = self.core.settings.feishu["calendar_id"]
        from .migration import adopt_legacy_feishu_mappings
        adoption = adopt_legacy_feishu_mappings(self.core)
        started = time.monotonic()
        with self.core.db.connect() as db:
            events = [dict(r) for r in db.execute("SELECT * FROM calendar_events WHERE provider LIKE 'google:%'")]
        active = active_calendars(self.core)
        events = [row for row in events if (row['provider'], row['calendar_id']) in active]
        events = mirror_instances(events, self.core.settings.timezone)
        desired_instances = {f"{r['provider']}:{r['calendar_id']}:{r['event_id']}" for r in events if r.get("managed_instance")}
        with self.core.db.connect() as db:
            old_instances = [dict(r) for r in db.execute("SELECT * FROM calendar_mappings WHERE provider='feishu' AND calendar_id=?", (target,)) if json.loads(r["document"]).get("managed_instance") and not json.loads(r["document"]).get("deleted")]
        for old in old_instances:
            if not any(old['item_id'].startswith(provider + ':' + calendar + ':') for provider, calendar in active):
                continue
            self._check_budget(started)
            if old["item_id"] not in desired_instances:
                with self.core.configuration_lock:
                    if not any(old['item_id'].startswith(provider + ':' + calendar + ':') for provider, calendar in active_calendars(self.core)):
                        continue
                    self.feishu.delete_event(target, old["event_id"])
                with self.core.db.transaction() as db:
                    document = json.loads(old["document"])
                    document["deleted"] = True
                    db.execute("UPDATE calendar_mappings SET document=? WHERE provider='feishu' AND calendar_id=? AND event_id=?", (dumps(document), target, old["event_id"]))
        changed = 0
        for row in events:
            self._check_budget(started)
            event = json.loads(row["document"])
            # Recurrences are bounded instances so moved/cancelled single occurrences stay correct.
            identity = f"{row['provider']}:{row['calendar_id']}:{row['event_id']}"
            with self.core.db.connect() as db:
                old = db.execute("SELECT * FROM calendar_mappings WHERE provider='feishu' AND calendar_id=? AND item_id=?", (target, identity)).fetchone()
            previous = json.loads(old["document"]) if old else {}
            digest = event_hash(event)
            if digest == previous.get("hash") and not previous.get("deleted"):
                continue
            if event.get("status") == "cancelled":
                if old:
                    with self.core.configuration_lock:
                        if (row['provider'], row['calendar_id']) not in active_calendars(self.core):
                            continue
                        self.feishu.delete_event(target, old["event_id"])
                    with self.core.db.transaction() as db:
                        db.execute("UPDATE calendar_mappings SET document=? WHERE provider='feishu' AND calendar_id=? AND event_id=?", (dumps({"hash": digest, "deleted": True}), target, old["event_id"]))
                continue
            start, end = event.get("start", {}), event.get("end", {})
            if start.get("date"):
                start_body, end_body = {"date": start["date"]}, {"date": end["date"]}
            else:
                a, b = event_time(start, self.core.settings.timezone), event_time(end, self.core.settings.timezone)
                if not a or not b:
                    continue
                start_body, end_body = {"timestamp": str(int(a.timestamp())), "timezone": self.core.settings.timezone}, {"timestamp": str(int(b.timestamp())), "timezone": self.core.settings.timezone}
            body = {"summary": event.get("summary", "(无标题)"), "description": "Google 单向镜像；请在 Google / Apple 日历或 Academic OS 调整。\n" + event.get("description", ""), "start_time": start_body, "end_time": end_body}
            body["free_busy_status"] = "free" if event.get("transparency") == "transparent" else "busy"
            body["need_notification"] = False
            with self.core.configuration_lock:
                if (row['provider'], row['calendar_id']) not in active_calendars(self.core):
                    continue
                result = self.feishu.mirror_event(target, body, old["event_id"] if old else None, hashlib.sha256(identity.encode()).hexdigest())
            event_id = result.get("event_id") or (old["event_id"] if old else None)
            if not event_id:
                raise RuntimeError("feishu_missing_event_id")
            with self.core.db.transaction() as db:
                db.execute("INSERT OR REPLACE INTO calendar_mappings VALUES('feishu',?,?,?,?)", (target, event_id, identity, dumps({"hash": digest, "last_success": utcnow(), "managed_instance": bool(row.get("managed_instance"))})))
            changed += 1
        self.core.source_health("calendar:feishu_mirror", True, detail={"changed": changed})
        return {"changed": changed, "legacy_adoption": adoption}


def mirror_instances(rows, default_timezone, now=None):
    """Rolling -7/+90 day recurrence view; keep each original occurrence identity stable."""
    now = now or datetime.now(timezone.utc)
    lower, upper = now - timedelta(days=7), now + timedelta(days=90)
    exceptions = {}
    for row in rows:
        event = json.loads(row["document"])
        if event.get("recurringEventId"):
            original = event_time(event.get("originalStartTime", {}), default_timezone)
            if original:
                exceptions[(row["provider"], row["calendar_id"], event["recurringEventId"], original.isoformat())] = event
    result, seen = [], set()
    masters = {(row["provider"], row["calendar_id"], row["event_id"]): json.loads(row["document"]) for row in rows if not json.loads(row["document"]).get("recurringEventId")}
    for row in rows:
        master = json.loads(row["document"])
        if master.get("recurringEventId"):
            continue
        if not master.get("recurrence"):
            result.append(row)
            continue
        if master.get("status") == "cancelled":
            continue
        a, b = event_time(master.get("start", {}), default_timezone), event_time(master.get("end", {}), default_timezone)
        if not a or not b:
            continue
        tz = ZoneInfo(master.get("start", {}).get("timeZone", default_timezone))
        rule = rrulestr("\n".join(master["recurrence"]), dtstart=a.astimezone(tz), forceset=True)
        calendar_days = (datetime.fromisoformat(master["end"]["date"]) - datetime.fromisoformat(master["start"]["date"])).days if master["start"].get("date") else None
        lookback = lower.astimezone(tz) - timedelta(days=calendar_days) if calendar_days is not None else (lower - (b - a)).astimezone(tz)
        for count, occurrence in enumerate(rule.xafter(lookback, inc=True)):
            if occurrence >= upper:
                break
            if count >= 10000:
                raise ValueError("Recurrence exceeds 10000 instances in the mirror window")
            start = occurrence.astimezone(timezone.utc)
            event = exceptions.get((row["provider"], row["calendar_id"], master["id"], start.isoformat()))
            if event and event.get("status") == "cancelled":
                continue
            if event is None:
                event = {**master, "start": {"dateTime": start.isoformat()}, "end": {"dateTime": (start + (b - a)).isoformat()}}
                if master["start"].get("date"):
                    start_date = occurrence.astimezone(tz).date()
                    event.update(start={"date": start_date.isoformat(), "timeZone": tz.key}, end={"date": (start_date + timedelta(days=calendar_days)).isoformat(), "timeZone": tz.key})
            event = {**master, **event}
            event.pop("recurrence", None)
            identity = master["id"] + "@" + start.isoformat()
            actual_start, actual_end = event_time(event["start"], default_timezone), event_time(event["end"], default_timezone)
            if actual_start >= upper or actual_end <= lower:
                continue
            seen.add((row["provider"], row["calendar_id"], identity))
            result.append({**row, "event_id": identity, "document": dumps(event), "managed_instance": True})
    for (provider, calendar, master_id, original), exception in exceptions.items():
        master = masters.get((provider, calendar, master_id))
        identity = master_id + "@" + original
        if not master or master.get("status") == "cancelled" or exception.get("status") == "cancelled" or (provider, calendar, identity) in seen:
            continue
        a, b = event_time(exception.get("start", {}), default_timezone), event_time(exception.get("end", {}), default_timezone)
        if a and b and a < upper and b > lower:
            event = {**master, **exception}
            event.pop("recurrence", None)
            result.append({"provider": provider, "calendar_id": calendar, "event_id": identity, "document": dumps(event), "managed_instance": True})
    return result


def calendar_busy(core, now=None, days=30, exclude_items=None):
    now = now or datetime.now(timezone.utc)
    until = now + timedelta(days=days)
    with core.db.connect() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM calendar_events WHERE provider LIKE 'google:%'")]
    active = active_calendars(core, 'busy')
    rows = [row for row in rows if (row['provider'], row['calendar_id']) in active]
    exceptions = {}
    for row in rows:
        event = json.loads(row["document"])
        if event.get("recurringEventId"):
            original = event_time(event.get("originalStartTime", {}), core.settings.timezone)
            if original:
                exceptions[(row["provider"], row["calendar_id"], event["recurringEventId"], original.isoformat())] = event
    result = []
    for row in rows:
        event = json.loads(row["document"])
        if event.get("extendedProperties", {}).get("private", {}).get("academic_os_item") in (exclude_items or set()):
            continue
        if event.get("status") == "cancelled" or event.get("transparency") == "transparent":
            continue
        start, end = event_time(event.get("start", {}), core.settings.timezone), event_time(event.get("end", {}), core.settings.timezone)
        if not start or not end or end <= start:
            continue
        recurrences = event.get("recurrence", [])
        if recurrences and not event.get("recurringEventId"):
            duration = end - start
            # dateutil handles RFC RRULE, RDATE and EXDATE; preserve the named zone for DST.
            local_tz = ZoneInfo(event.get("start", {}).get("timeZone", core.settings.timezone))
            recurrence = rrulestr("\n".join(recurrences), dtstart=start.astimezone(local_tz), forceset=True)
            calendar_days = (datetime.fromisoformat(event["end"]["date"]) - datetime.fromisoformat(event["start"]["date"])).days if event["start"].get("date") else None
            lookback = now.astimezone(local_tz) - timedelta(days=calendar_days) if calendar_days is not None else (now - duration).astimezone(local_tz)
            for count, occurrence in enumerate(recurrence.xafter(lookback, inc=True)):
                if occurrence > until:
                    break
                if count >= 10000:
                    raise ValueError("Recurrence exceeds 10000 instances in the planning window")
                a = occurrence.astimezone(timezone.utc)
                if (row["provider"], row["calendar_id"], event["id"], a.isoformat()) in exceptions:
                    continue
                if event["start"].get("date"):
                    occurrence_end = (occurrence + timedelta(days=calendar_days)).astimezone(timezone.utc)
                else:
                    occurrence_end = a + duration
                if a < until and occurrence_end > now:
                    result.append({"start": a.isoformat(), "end": occurrence_end.isoformat(), "title": event.get("summary", "")})
        elif end > now and start < until:
            result.append({"start": start.isoformat(), "end": end.isoformat(), "title": event.get("summary", "")})
    return result
