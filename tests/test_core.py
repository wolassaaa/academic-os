
import pytest

from academic_os.config import Settings
from academic_os.core import Core, Conflict
from academic_os.db import Database
from academic_os.security import hash_password, verify_password


@pytest.fixture
def core(tmp_path):
    settings = Settings(data_dir=tmp_path)
    return Core(Database(settings.database_path), settings)


def test_ingestion_is_idempotent_and_preserves_user_arrangement(core):
    record = {"source_id": "moodle:1", "title": "Essay", "due_at": "2026-10-20T15:59:00Z"}
    first = core.ingest("moodle", [record])[0]
    again = core.ingest("moodle", [record])[0]
    assert first["id"] == again["id"]
    assert first["version"] == again["version"]
    assert first["source_due_at"] == "2026-10-20T15:59:00+00:00"
    assert first["scheduled_start"] is None


def test_optimistic_update_and_idempotent_confirmation(core):
    item = core.create_item({"title": "Read", "estimate_minutes": 60})
    draft = core.create_draft([{ "item_id": item["id"], "expected_version": item["version"],
                                 "start": "2026-10-15T10:00:00+08:00", "end": "2026-10-15T11:00:00+08:00"}], "draft-key")
    result = core.confirm_draft(draft["id"], "confirm-key")
    replay = core.confirm_draft(draft["id"], "confirm-key")
    assert result == replay
    assert core.get_item(item["id"])["scheduled_start"] is not None
    assert len(core.list_jobs()) == 1
    with pytest.raises(Conflict):
        core.update_item(item["id"], {"title": "Wrong"}, item["version"])


def test_confirmation_checks_all_versions_before_any_change(core):
    a = core.create_item({"title": "A"})
    b = core.create_item({"title": "B"})
    changes = [{"item_id": i["id"], "expected_version": i["version"],
                "start": "2026-10-15T10:00:00+08:00", "end": "2026-10-15T11:00:00+08:00"} for i in (a,b)]
    draft = core.create_draft(changes, "draft")
    core.update_item(b["id"], {"title": "Changed"}, b["version"])
    with pytest.raises(Conflict):
        core.confirm_draft(draft["id"], "confirm")
    assert core.get_item(a["id"])["scheduled_start"] is None


def test_password_hashing_and_database_backup(core, tmp_path):
    hashed = hash_password("a-long-passphrase-123")
    assert verify_password("a-long-passphrase-123", hashed)
    assert not verify_password("wrong", hashed)
    core.create_item({"title": "Backup me"})
    target = tmp_path / "backup.sqlite3"
    core.db.backup(target)
    restored = Core(Database(target), core.settings)
    assert restored.list_items()[0]["title"] == "Backup me"


def test_draft_key_cannot_be_reused_with_different_payload(core):
    a = core.create_item({"title": "One"})
    change = {"item_id": a["id"], "expected_version": 1,
              "start": "2026-10-15T10:00:00+08:00", "end": "2026-10-15T11:00:00+08:00"}
    core.create_draft([change], "same")
    with pytest.raises(Conflict):
        core.create_draft([{**change, "end": "2026-10-15T12:00:00+08:00"}], "same")


def test_unknown_due_and_source_failure_do_not_erase_items(core):
    item = core.ingest("moodle", [{"source_id": "x", "title": "Undated", "due_at": "2099-01-01T00:00:00Z"}])[0]
    assert item["source_due_at"] is None
    assert item["review_state"] == "needs_date"
    core.source_health("moodle", False, "connection_failed")
    assert len(core.list_items()) == 1
    assert core.health()["sources"][0]["status"] == "error"


def test_source_date_conflict_keeps_both_evidence(core):
    a = core.ingest("moodle", [{"source_id": "a", "course": "CHEM1001", "title": "Essay",
                               "due_at": "2026-10-20T15:59:00Z"}])[0]
    b = core.ingest("gmail:school", [{"source_id": "b", "course": "CHEM1001", "title": "Essay",
                                     "due_at": "2026-10-21T15:59:00Z"}])[0]
    assert a["id"] == b["id"]
    assert b["source_due_at"] == a["source_due_at"]
    assert len(b["evidence"]) == 2
    assert len(core.list_conflicts()) == 1
