from datetime import datetime, timedelta, timezone

import pytest

from academic_os.calendars import CalendarSync, calendar_busy, mirror_instances
from academic_os.config import Settings
from academic_os.core import Conflict, Core
from academic_os.db import Database, dumps
from academic_os.notifications import Notifications
from academic_os.providers import ProviderError


class Calendar:
    def __init__(self):
        self.events = []
        self.writes = []
        self.calls = []
        self.invalid_token = False

    def list_events(self, calendar_id, sync_token=None, page_token=None):
        self.calls.append((sync_token, page_token))
        if sync_token and self.invalid_token:
            self.invalid_token = False
            raise ProviderError(410, "expired_cursor")
        if page_token is None and len(self.events) > 1:
            return {"items": self.events[:1], "nextPageToken": "page2"}
        return {"items": self.events[1:] if page_token else self.events, "nextSyncToken": "cursor"}

    def put_event(self, calendar_id, event_id, body, etag=None, create=False):
        self.writes.append((event_id, body, etag))
        return {"id": event_id, "etag": "v1", **body}

    def delete_event(self, calendar_id, event_id, etag=None):
        self.writes.append((event_id, "delete", etag))


def build(tmp_path, mode="live"):
    settings = Settings(data_dir=tmp_path, mode=mode, google_accounts=[{"id": "main", "secret_ref": "google-main", "calendars": [{"id": "primary", "write": True}]}])
    core = Core(Database(settings.database_path), settings)
    core.source_health("calendar:google:main:primary", True, detail={"test_fixture": "empty_calendar"})
    return core


def scheduled(core):
    item = core.create_item({"title": "Study", "estimate_minutes": 60})
    draft = core.create_draft([{"item_id": item["id"], "expected_version": 1, "start": "2026-10-15T10:00:00+08:00", "end": "2026-10-15T11:00:00+08:00"}], "d")
    core.confirm_draft(draft["id"], "c")
    return core.get_item(item["id"])


def test_calendar_shadow_prevents_writes_and_push_has_stable_id(tmp_path):
    core = build(tmp_path, "shadow")
    provider = Calendar()
    item = scheduled(core)
    sync = CalendarSync(core, {"main": provider})
    assert sync.push(item["id"])["shadow"]
    assert not provider.writes
    core.settings.mode = "live"
    sync.push(item["id"])
    sync.push(item["id"])
    assert len(provider.writes) == 1


def test_calendar_pages_remote_edit_delete_and_no_resurrection(tmp_path):
    core = build(tmp_path)
    provider = Calendar()
    item = scheduled(core)
    sync = CalendarSync(core, {"main": provider})
    sync.push(item["id"])
    event_id, body, _ = provider.writes[0]
    provider.events = [{"id": event_id, "etag": "v2", **body, "start": {"dateTime": "2026-10-16T02:00:00Z"}, "end": {"dateTime": "2026-10-16T03:00:00Z"}}, {"id": "other", "summary": "Meeting", "start": {"dateTime": "2026-10-16T04:00:00Z"}, "end": {"dateTime": "2026-10-16T05:00:00Z"}}]
    # Existing completed push jobs must not count as pending edits.
    with core.db.transaction() as db:
        db.execute("UPDATE jobs SET status='done'")
    sync.pull()
    assert len(provider.calls) == 2
    changed = core.get_item(item["id"])
    assert changed["scheduled_start"] == "2026-10-16T02:00:00+00:00"
    assert changed["source_due_at"] is None
    provider.events = [{"id": event_id, "etag": "v3", "status": "cancelled"}]
    sync.pull()
    assert core.get_item(item["id"])["calendar_deleted"] is True
    sync.push(item["id"])
    assert len(provider.writes) == 1


def test_external_edit_conflicts_with_pending_local_change_and_cursor_recovers(tmp_path):
    core = build(tmp_path)
    provider = Calendar()
    item = scheduled(core)
    sync = CalendarSync(core, {"main": provider})
    sync.push(item["id"])
    event_id, body, _ = provider.writes[0]
    provider.events = [{"id": event_id, "etag": "v2", **body, "start": {"dateTime": "2026-10-16T02:00:00Z"}, "end": {"dateTime": "2026-10-16T03:00:00Z"}}]
    sync.pull()
    assert core.list_conflicts()[0]["kind"] == "calendar_edit"
    assert core.get_item(item["id"])["scheduled_start"] == item["scheduled_start"]
    provider.invalid_token = True
    sync.pull()
    assert any(call[0] is None for call in provider.calls[-2:])


def test_calendar_all_day_and_recurring_exceptions(tmp_path):
    core = build(tmp_path)
    with core.db.transaction() as db:
        for event in [
            {"id": "all-day", "start": {"date": "2026-10-15"}, "end": {"date": "2026-10-16"}},
            {"id": "weekly", "start": {"dateTime": "2026-10-14T10:00:00+08:00"}, "end": {"dateTime": "2026-10-14T11:00:00+08:00"}, "recurrence": ["RRULE:FREQ=DAILY;COUNT=3"]},
            {"id": "exception", "recurringEventId": "weekly", "originalStartTime": {"dateTime": "2026-10-15T10:00:00+08:00"}, "status": "cancelled"},
        ]:
            db.execute("INSERT INTO calendar_events VALUES(?,?,?,?)", ("google:main", "primary", event["id"], dumps(event)))
    busy = calendar_busy(core, datetime.fromisoformat("2026-10-14T00:00:00+08:00"), 4)
    assert len(busy) == 3
    assert any(b["start"] == "2026-10-14T16:00:00+00:00" for b in busy)


def test_notification_failure_is_not_delivered_and_retries_same_record(tmp_path):
    core = build(tmp_path)
    calls = []
    def sender(document, key):
        calls.append(key)
        if len(calls) == 1:
            raise RuntimeError("offline")
        return "receipt-1"
    service = Notifications(core, {"feishu": sender})
    nid = service.queue("feishu", {"title": "Due", "body": "Submit"}, "unique")
    service.dispatch_one(now=10000000000)
    assert service.list()[0]["state"] == "failed"
    service.dispatch_one(now=10000000000 + 3600)
    row = service.list()[0]
    assert row["state"] == "delivered"
    assert row["receipt"] == "receipt-1"
    assert calls == [nid, nid]


def test_shadow_notification_is_preview_and_acknowledge_is_separate(tmp_path):
    core = build(tmp_path, "shadow")
    service = Notifications(core, {"feishu": lambda *_: pytest.fail("shadow must not send")})
    nid = service.queue("feishu", {"title": "Due", "body": "Submit"}, "unique")
    service.dispatch_one(now=10000000000)
    assert service.list()[0]["state"] == "preview"
    service.action(nid, "acknowledge")
    assert service.list()[0]["state"] == "acknowledged"


def test_feishu_mirror_materializes_recurrence_and_cancelled_exception(tmp_path):
    core = build(tmp_path)
    core.settings.feishu = {"calendar_id": "mirror"}
    class Feishu:
        def __init__(self):
            self.writes, self.deletes = [], []
        def mirror_event(self, calendar, body, event_id=None, unique_key=None):
            self.writes.append((body, unique_key))
            return {"event_id": event_id or unique_key}
        def delete_event(self, calendar, event_id):
            self.deletes.append(event_id)
    feishu = Feishu()
    now = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
    master = {"id": "daily", "summary": "Recurring", "start": {"dateTime": now.isoformat()}, "end": {"dateTime": (now + timedelta(hours=1)).isoformat()}, "recurrence": ["RRULE:FREQ=DAILY;COUNT=3"]}
    with core.db.transaction() as db:
        db.execute("INSERT INTO calendar_events VALUES(?,?,?,?)", ("google:main", "primary", "daily", dumps(master)))
    sync = CalendarSync(core, {}, feishu)
    sync.mirror()
    assert len(feishu.writes) == 3
    assert all("recurrence" not in body for body, _ in feishu.writes)
    sync.mirror()
    assert len(feishu.writes) == 3
    exception = {"id": "exception", "recurringEventId": "daily", "originalStartTime": {"dateTime": (now + timedelta(days=1)).isoformat()}, "status": "cancelled"}
    with core.db.transaction() as db:
        db.execute("INSERT INTO calendar_events VALUES(?,?,?,?)", ("google:main", "primary", "exception", dumps(exception)))
    sync.mirror()
    assert len(feishu.deletes) == 1
    assert len(feishu.writes) == 3


def test_crash_replayed_creation_does_not_hide_remote_edit(tmp_path):
    core = build(tmp_path)
    item = scheduled(core)
    class Replayed(Calendar):
        def put_event(self, calendar_id, event_id, body, etag=None, create=False):
            return {**body, "id": event_id, "etag": "edited-after-crash", "start": {"dateTime": "2026-10-16T10:00:00+08:00"}, "end": {"dateTime": "2026-10-16T11:00:00+08:00"}}
    sync = CalendarSync(core, {"main": Replayed()})
    with pytest.raises(Conflict):
        sync.push(item["id"])
    assert core.list_conflicts()[0]["kind"] == "calendar_edit"


def test_moved_recurring_exception_from_outside_window_still_mirrors(tmp_path):
    now = datetime.now(timezone.utc)
    original, moved = now - timedelta(days=30), now + timedelta(days=2)
    master = {"id": "master", "start": {"dateTime": original.isoformat()}, "end": {"dateTime": (original + timedelta(hours=1)).isoformat()}, "recurrence": ["RRULE:FREQ=YEARLY;COUNT=2"]}
    exception = {"id": "exception", "recurringEventId": "master", "originalStartTime": master["start"], "start": {"dateTime": moved.isoformat()}, "end": {"dateTime": (moved + timedelta(hours=1)).isoformat()}}
    rows = [{"provider": "google:a", "calendar_id": "c", "event_id": event["id"], "document": dumps(event)} for event in (master, exception)]
    result = mirror_instances(rows, "UTC", now)
    assert len(result) == 1
    assert result[0]["event_id"] == "master@" + original.isoformat()


def test_conditional_write_conflict_is_resolvable_after_fresh_pull(tmp_path):
    core = build(tmp_path)
    provider = Calendar()
    item = scheduled(core)
    sync = CalendarSync(core, {"main": provider})
    sync.push(item["id"])
    event_id, body, _ = provider.writes[0]
    item = core.update_item(item["id"], {"title": "New title"}, item["version"])
    provider.put_event = lambda *args, **kwargs: (_ for _ in ()).throw(ProviderError(412))
    with pytest.raises(Conflict):
        sync.push(item["id"])
    provider.events = [{**body, "id": event_id, "etag": "remote-v2", "summary": "Remote title"}]
    sync.pull()
    for conflict in core.list_conflicts():
        core.resolve_conflict(conflict["id"], "remote", core.get_item(item["id"])["version"])
    assert not core.list_conflicts()
    assert core.get_item(item["id"])["title"] == "Remote title"


def test_calendar_repeated_page_token_fails_without_infinite_loop(tmp_path):
    core = build(tmp_path)
    class Cycling(Calendar):
        def list_events(self, calendar_id, sync_token=None, page_token=None):
            self.calls.append(page_token)
            return {"items": [], "nextPageToken": "same"}
    provider = Cycling()
    result = CalendarSync(core, {"main": provider}).pull()
    assert result[0]["error"] == "ValueError"
    assert len(provider.calls) == 2
    assert core.health()["sources"][0]["status"] == "error"


def test_long_shutdown_overdue_reminder_is_not_silently_skipped(tmp_path):
    core = build(tmp_path)
    now = datetime.now(timezone.utc)
    core.create_item({"title": "Missed", "due_at": (now - timedelta(days=30)).isoformat()})
    service = Notifications(core)
    service.generate(now)
    assert service.list()[0]["document"]["level"].startswith("overdue:")


def test_acknowledgement_covers_channels_of_same_reminder(tmp_path):
    core = build(tmp_path)
    core.settings.feishu = {"chat_id": "configured"}
    now = datetime.now(timezone.utc)
    core.create_item({"title": "Due", "due_at": (now + timedelta(hours=2)).isoformat()})
    notices = Notifications(core)
    notices.generate(now)
    workbench = next(n for n in notices.list() if n["channel"] == "workbench")
    notices.action(workbench["id"], "acknowledge")
    assert all(n["state"] == "acknowledged" for n in notices.list())


def test_live_confirmation_requires_fresh_successful_calendar_snapshot(tmp_path):
    core = build(tmp_path)
    item = core.create_item({"title": "Study"})
    draft = core.create_draft([{"item_id": item["id"], "expected_version": 1, "start": "2026-10-15T10:00:00+08:00", "end": "2026-10-15T11:00:00+08:00"}], "freshness")
    with core.db.transaction() as db:
        db.execute("UPDATE sources SET last_success=?", ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),))
    with pytest.raises(Conflict, match="calendar_snapshot_stale"):
        core.confirm_draft(draft["id"], "confirm-freshness")
    core.source_health("calendar:google:main:primary", False, "offline")
    with pytest.raises(Conflict, match="calendar_snapshot_stale"):
        core.confirm_draft(draft["id"], "confirm-freshness")
    core.source_health("calendar:google:main:primary", True)
    assert core.confirm_draft(draft["id"], "confirm-freshness")["state"] == "confirmed"


def test_all_day_recurrence_keeps_calendar_dates_across_dst(tmp_path):
    import json
    core = build(tmp_path)
    event = {"id": "all-day-dst", "start": {"date": "2026-03-08", "timeZone": "America/New_York"}, "end": {"date": "2026-03-09", "timeZone": "America/New_York"}, "recurrence": ["RRULE:FREQ=DAILY;COUNT=3"]}
    row = {"provider": "google:main", "calendar_id": "primary", "event_id": event["id"], "document": dumps(event)}
    now = datetime.fromisoformat("2026-03-07T00:00:00+00:00")
    with core.db.transaction() as db:
        db.execute("INSERT INTO calendar_events VALUES(?,?,?,?)", tuple(row.values()))
    busy = calendar_busy(core, now, 5)
    assert busy[0]["start"] == "2026-03-08T05:00:00+00:00"
    assert busy[0]["end"] == "2026-03-09T04:00:00+00:00"
    assert busy[1]["end"] == "2026-03-10T04:00:00+00:00"
    mirrored = [json.loads(r["document"]) for r in mirror_instances([row], core.settings.timezone, now)]
    assert [(e["start"]["date"], e["end"]["date"]) for e in mirrored] == [("2026-03-08", "2026-03-09"), ("2026-03-09", "2026-03-10"), ("2026-03-10", "2026-03-11")]


def test_all_day_pull_preserves_source_calendar_timezone(tmp_path):
    import json
    core = build(tmp_path)
    class Zoned(Calendar):
        def list_events(self, *args):
            return {"timeZone": "America/New_York", "items": [{"id": "all-day", "start": {"date": "2026-03-08"}, "end": {"date": "2026-03-09"}}], "nextSyncToken": "cursor"}
    CalendarSync(core, {"main": Zoned()}).pull()
    with core.db.connect() as db:
        event = json.loads(db.execute("SELECT document FROM calendar_events").fetchone()[0])
    assert event["start"]["timeZone"] == "America/New_York"
    assert event["end"]["timeZone"] == "America/New_York"


def test_all_day_dst_recurrence_lookback_does_not_drop_active_occurrence(tmp_path):
    import json
    core = build(tmp_path)
    event = {"id": "dst-lookback", "start": {"date": "2026-03-08", "timeZone": "America/New_York"}, "end": {"date": "2026-03-09", "timeZone": "America/New_York"}, "recurrence": ["RRULE:FREQ=DAILY;COUNT=20"]}
    row = {"provider": "google:main", "calendar_id": "primary", "event_id": event["id"], "document": dumps(event)}
    now = datetime.fromisoformat("2026-03-10T23:30:00-04:00")
    with core.db.transaction() as db:
        db.execute("INSERT INTO calendar_events VALUES(?,?,?,?)", tuple(row.values()))
    assert any(b["start"] == "2026-03-10T04:00:00+00:00" for b in calendar_busy(core, now, 1))
    # The mirror uses a -7 day lower bound: its active boundary event must survive too.
    mirrored = [json.loads(r["document"]) for r in mirror_instances([row], core.settings.timezone, now + timedelta(days=7))]
    assert any(e["start"]["date"] == "2026-03-10" for e in mirrored)
