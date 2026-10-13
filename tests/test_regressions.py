
import pytest
from fastapi.testclient import TestClient

from academic_os.api import create_app
from academic_os.config import Settings
from academic_os.core import Conflict, Core
from academic_os.db import Database, dumps
from academic_os.notifications import Notifications
from academic_os.security import Auth
from academic_os.sources import collect_gmail


def core_at(path):
    settings = Settings(data_dir=path, mode="live")
    return Core(Database(settings.database_path), settings)


def change(item, start="2026-11-01T09:00:00+08:00", end="2026-11-01T10:00:00+08:00"):
    return {"item_id": item["id"], "expected_version": item["version"], "start": start, "end": end}


def test_duplicate_item_and_overlapping_confirmation_are_rejected(tmp_path):
    core = core_at(tmp_path)
    a, b = core.create_item({"title": "A"}), core.create_item({"title": "B"})
    with pytest.raises(ValueError):
        core.create_draft([change(a), change(a)], "duplicate")
    draft = core.create_draft([change(a), change(b)], "overlap")
    with pytest.raises(Conflict, match="overlap"):
        core.confirm_draft(draft["id"], "confirm")
    assert core.get_item(a["id"])["scheduled_start"] is None


def test_new_calendar_conflict_blocks_existing_draft_confirmation(tmp_path):
    core = core_at(tmp_path)
    core.settings.google_accounts = [{'id': 'main', 'secret_ref': 'main', 'calendars': [{'id': 'primary', 'read': True}]}]
    core.source_health('calendar:google:main:primary', True)
    a = core.create_item({"title": "A"})
    draft = core.create_draft([change(a)], "draft")
    with core.db.transaction() as db:
        db.execute("INSERT INTO calendar_events VALUES(?,?,?,?)", ("google:main", "primary", "busy", dumps({"id": "busy", "start": {"dateTime": change(a)["start"]}, "end": {"dateTime": change(a)["end"]}})))
    with pytest.raises(Conflict, match="overlap"):
        core.confirm_draft(draft["id"], "confirm")


def test_generated_plan_idempotency_is_independent_of_clock_and_item_changes(tmp_path):
    core = core_at(tmp_path)
    token = Auth(core.db, core.settings).issue_token("test", ["read", "draft"])
    core.create_item({"title": "A", "estimate_minutes": 60})
    client = TestClient(create_app(core))
    headers = {"Authorization": "Bearer " + token, "Idempotency-Key": "plan"}
    first = client.post("/api/v1/plans/generate", json={"days": 7}, headers=headers)
    core.create_item({"title": "B", "estimate_minutes": 60})
    second = client.post("/api/v1/plans/generate", json={"days": 7}, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert client.post("/api/v1/plans/generate", json={"days": 8}, headers=headers).status_code == 409


def test_gmail_retry_after_ingest_crash_does_not_lose_candidates(tmp_path, monkeypatch):
    core = core_at(tmp_path)
    class Gmail:
        def request(self, method, url, params=None):
            if url.endswith("/messages"):
                return {"messages": [{"id": "m1"}]}
            return {"internalDate": "1790000000000", "payload": {"headers": [{"name": "From", "value": "school@hkbu.edu.hk"}, {"name": "Subject", "value": "Submit 2026-11-01 12:00"}]}}
    original = core.ingest
    monkeypatch.setattr(core, "ingest", lambda *_: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        collect_gmail(core, {"id": "main"}, Gmail())
    monkeypatch.setattr(core, "ingest", original)
    collect_gmail(core, {"id": "main"}, Gmail())
    assert len(core.list_items()) == 1


def test_snoozed_workbench_notice_is_delivered_locally(tmp_path):
    core = core_at(tmp_path)
    notices = Notifications(core)
    nid = notices.queue("workbench", {"title": "A"}, "a")
    notices.action(nid, "snooze", 1)
    notices.dispatch_one(now=10000000000)
    assert notices.list()[0]["state"] == "delivered"


def test_source_date_conflict_can_be_resolved_with_version_guard(tmp_path):
    core = core_at(tmp_path)
    core.ingest("a", [{"source_id": "a", "course": "C", "title": "A", "due_at": "2026-11-01T12:00:00Z"}])
    item = core.ingest("b", [{"source_id": "b", "course": "C", "title": "A", "due_at": "2026-11-02T12:00:00Z"}])[0]
    conflict = core.list_conflicts()[0]
    core.resolve_conflict(conflict["id"], "remote", item["version"])
    assert core.get_item(item["id"])["source_due_at"] == "2026-11-02T12:00:00+00:00"
    assert not core.list_conflicts()


def test_source_completion_cancels_queued_notice_and_calendar_slot(tmp_path):
    core = core_at(tmp_path)
    record = {"source_id": "a", "title": "A", "due_at": "2026-11-01T12:00:00Z", "submission_status": "pending"}
    item = core.ingest("moodle", [record])[0]
    draft = core.create_draft([change(item)], "draft")
    core.confirm_draft(draft["id"], "confirm")
    notices = Notifications(core)
    notices.queue("feishu", {"title": "A"}, "a", item["id"])
    updated = core.ingest("moodle", [{**record, "submission_status": "submitted"}])[0]
    assert notices.list()[0]["state"] == "completed"
    assert any(j["payload"].get("version") == updated["version"] for j in core.list_jobs())


def test_source_renaming_and_weight_updates_preserve_user_schedule(tmp_path):
    core = core_at(tmp_path)
    item = core.ingest("moodle", [{"source_id": "a", "title": "A", "weight_pct": 10}])[0]
    updated = core.ingest("moodle", [{"source_id": "a", "title": "New A", "weight_pct": 20}])[0]
    assert updated["id"] == item["id"]
    assert updated["title"] == "New A"
    assert updated["weight_pct"] == 20


def test_manual_deadline_edit_invalidates_old_notice(tmp_path):
    core = core_at(tmp_path)
    item = core.create_item({"title": "A", "due_at": "2026-11-01T12:00:00Z"})
    notices = Notifications(core)
    notices.queue("feishu", {"title": "Old due"}, "old", item["id"])
    core.update_item(item["id"], {"source_due_at": "2026-12-01T12:00:00Z"}, item["version"])
    assert notices.list()[0]["state"] == "superseded"


def test_draft_only_agent_cannot_rename_scheduled_item(tmp_path):
    core = core_at(tmp_path)
    item = core.create_item({"title": "A"})
    draft = core.create_draft([change(item)], "draft")
    core.confirm_draft(draft["id"], "confirm")
    token = Auth(core.db, core.settings).issue_token("draft-only", ["read", "draft", "sync"])
    client = TestClient(create_app(core))
    response = client.patch("/api/v1/items/" + item["id"], json={"expected_version": 2, "changes": {"title": "B"}}, headers={"Authorization": "Bearer " + token})
    assert response.status_code == 403


def test_explicit_null_cannot_clear_open_conflict(tmp_path):
    core = core_at(tmp_path)
    core.ingest("a", [{"source_id": "a", "course": "C", "title": "A", "due_at": "2026-11-01T12:00:00Z"}])
    item = core.ingest("b", [{"source_id": "b", "course": "C", "title": "A", "due_at": "2026-11-02T12:00:00Z"}])[0]
    with pytest.raises(ValueError):
        core.update_item(item["id"], {"review_state": None}, item["version"])
    assert core.get_item(item["id"])["review_state"] == "conflict"


def test_notification_generation_snapshot_cannot_outlive_deadline_edit(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    core = core_at(tmp_path)
    core.settings.feishu = {"chat_id": "configured"}
    now = datetime.now(timezone.utc)
    item = core.create_item({"title": "A", "due_at": (now + timedelta(hours=1)).isoformat()})
    snapshot = core.list_items()
    core.update_item(item["id"], {"source_due_at": (now + timedelta(days=30)).isoformat()}, 1)
    monkeypatch.setattr(core, "list_items", lambda: snapshot)
    notices = Notifications(core)
    notices.generate(now)
    assert not notices.list()


def test_source_date_resolution_refreshes_scheduled_calendar_description(tmp_path):
    core = core_at(tmp_path)
    item = core.ingest("a", [{"source_id": "a", "course": "C", "title": "A", "due_at": "2026-11-01T12:00:00Z"}])[0]
    draft = core.create_draft([change(item)], "draft")
    core.confirm_draft(draft["id"], "confirm")
    item = core.ingest("b", [{"source_id": "b", "course": "C", "title": "A", "due_at": "2026-11-02T12:00:00Z"}])[0]
    updated = core.resolve_conflict(core.list_conflicts()[0]["id"], "remote", item["version"])
    assert any(job["payload"].get("version") == updated["version"] for job in core.list_jobs())

