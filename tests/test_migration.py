import json
import time
from datetime import UTC

import pytest

from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database
from academic_os.migration import adopt_legacy_feishu_mappings, backup_data, import_legacy, restore_data


@pytest.fixture
def core(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    return Core(Database(settings.database_path), settings)


def put(root, filename, value):
    path = root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_import_is_read_only_idempotent_and_portable(core, tmp_path):
    root = tmp_path / "legacy"
    baseline = put(root, "academic_os/grade/assessment_baseline.json", {"items": [{"course": "C", "name": "Exam", "date": "2026-11-01"}]})
    put(root, "academic_os/logs/reminder_state.json", {"sent": ["old"]})
    put(root, "google_token.json", {"refresh_token": "DO-NOT-COPY"})
    original = baseline.read_bytes()
    first = import_legacy(core, root)
    second = import_legacy(core, root)
    assert first["artifacts"] == second["artifacts"] == 2
    assert baseline.read_bytes() == original
    assert core.settings.local_sources["assessment_baseline"].startswith(str(core.settings.data_dir))
    assert "DO-NOT-COPY" not in str(first)
    assert len(core.list_artifacts()) == 2
    assert core.list_items() == []
    assert not list((core.settings.data_dir / "imports").rglob("google_token*"))


def test_timetable_ast_never_executes_and_copies_literal(core, tmp_path):
    root = tmp_path / "legacy"
    path = root / "scripts/hkbu_timetable.py"
    path.parent.mkdir(parents=True)
    marker = tmp_path / "executed"
    path.write_text("raise RuntimeError('must not execute')\nTIMETABLE = {0: [('09:00','10:00','C','x','Lecture','Room')]}\n", encoding="utf-8")
    result = import_legacy(core, root)
    assert not marker.exists()
    assert result["artifacts"] == 1
    data = json.loads((core.settings.data_dir / "imports/timetable.json").read_text())
    assert data["entries"][0]["start"] == "09:00"
    assert data["needs_term_review"]


def test_event_mapping_requires_exact_source_and_explicit_calendar(core, tmp_path):
    item = core.ingest("moodle", [{"source_id": "moodle:activity:assign:200", "title": "Essay"}])[0]
    root = tmp_path / "legacy"
    put(root, "academic_os/logs/calendar_map.json", {"moodle:activity:assign:200": {"event_id": "e1", "calendar_id": "c1", "provider": "google:school"}, "wrong": {"event_id": "e2", "calendar_id": "c1", "provider": "google:school"}})
    assert import_legacy(core, root)["mappings"] == 1
    with core.db.connect() as db:
        rows = db.execute("SELECT * FROM calendar_mappings").fetchall()
    assert len(rows) == 1
    assert rows[0]["item_id"] == item["id"]
    assert json.loads(rows[0]["document"])["purpose"] == "legacy_deadline"


def test_symlinks_and_secret_fields_are_not_copied(core, tmp_path):
    root = tmp_path / "legacy"
    put(root, "grade_data.json", {"grade": 80, "refresh_token": "SECRET", "nested": {"api_key": "SECRET"}})
    result = import_legacy(core, root)
    assert result["artifacts"] == 1
    assert "SECRET" not in json.dumps(core.list_artifacts())


def test_backup_restore_invalidates_auth_and_requires_stopped_worker(core, tmp_path):
    core.create_item({"title": "Original"})
    with core.db.transaction() as db:
        db.execute("INSERT INTO sessions VALUES('x','u',9999999999,0)")
        db.execute("INSERT INTO tokens VALUES('x','u','[]','now')")
    backup = backup_data(core, tmp_path / "backup")
    core.create_item({"title": "Later"})
    with core.db.transaction() as db:
        db.execute("INSERT INTO locks VALUES('active-worker','other',?)", (time.time() + 300,))
    with pytest.raises(RuntimeError, match="worker"):
        restore_data(core, backup)
    with core.db.transaction() as db:
        db.execute("DELETE FROM locks")
    restore_data(core, backup)
    assert [i["title"] for i in core.list_items()] == ["Original"]
    assert core.settings.mode == "shadow"
    with core.db.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM tokens").fetchone()[0] == 0


def test_restore_rejects_traversal_and_modified_hash(core, tmp_path):
    manifest = backup_data(core, tmp_path / "backup")
    payload = json.loads(manifest.read_text())
    payload["files"][0]["path"] = "../outside.sqlite3"
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        restore_data(core, manifest)


def test_restore_rejects_changed_import_before_database_mutation(core, tmp_path):
    put(core.settings.data_dir, "imports/grades.json", {"grade": 70})
    core.create_item({"title": "Original"})
    manifest = backup_data(core, tmp_path / "backup")
    core.create_item({"title": "Later"})
    (manifest.parent / "imports/grades.json").write_text('{}')
    with pytest.raises(ValueError, match="integrity"):
        restore_data(core, manifest)
    assert len(core.list_items()) == 2


def test_credentials_only_import_when_opted_in(core, tmp_path, monkeypatch):
    import keyring
    root = tmp_path / "legacy"
    put(root, "google_token.json", {"client_id": "id", "client_secret": "SECRET", "refresh_token": "REFRESH"})
    written = []
    monkeypatch.setattr(keyring, "get_password", lambda service, name: {"moodle-site-url": "https://school.example", "moodle-token": "TOKEN"}.get(name))
    monkeypatch.setattr(keyring, "set_password", lambda service, name, value: written.append((service, name, value)))
    assert import_legacy(core, root)["credentials"] == []
    assert written == []
    result = import_legacy(core, root, import_credentials=True)
    assert len(written) == 2
    assert all(service == "academic-os-v2" for service, _, _ in written)
    assert core.settings.moodle["secret_ref"] == "moodle"
    assert all(secret not in json.dumps(result) for secret in ("SECRET", "REFRESH", "TOKEN"))
    assert all(secret not in (core.settings.data_dir / "imports/manifest.json").read_text() for secret in ("SECRET", "REFRESH", "TOKEN"))


def test_restore_roundtrips_account_calendar_metadata_and_rebases_sources(core, tmp_path, monkeypatch):
    import keyring
    core.settings.google_accounts = [{"id": "school", "secret_ref": "google-school", "calendars": [{"id": "academic", "write": True}]}]
    core.settings.moodle = {"secret_ref": "moodle"}
    core.settings.port = 9876
    path = put(core.settings.data_dir, "imports/baseline.json", {"items": []})
    core.settings.local_sources["assessment_baseline"] = str(path)
    manifest = backup_data(core, tmp_path / "backup")
    destination = Settings(data_dir=tmp_path / "other")
    target = Core(Database(destination.database_path), destination)
    monkeypatch.setattr(keyring, "get_password", lambda service, name: None)
    result = restore_data(target, manifest)
    assert target.settings.google_accounts == core.settings.google_accounts
    assert target.settings.port == 9876
    assert target.settings.data_dir == destination.data_dir
    assert target.settings.local_sources["assessment_baseline"] == str(destination.data_dir / "imports/baseline.json")
    assert target.settings.mode == "shadow"
    assert target.settings.model["enabled"] is False
    assert result["missing_credential_refs"] == ["google-school", "moodle"]


def feishu_legacy(core, tmp_path, records, target="feishu-calendar"):
    root = tmp_path / "legacy"
    put(root, "gcal_feishu_state.json", records)
    script = root / "scripts/gcal_to_feishu.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(f"raise RuntimeError('do not execute')\nFEISHU_CAL_ID = {target!r}\n", encoding="utf-8")
    core.settings.feishu = {"calendar_id": "feishu-calendar"}
    core.settings.google_accounts = [{"id": "school", "secret_ref": "google-school", "calendars": [{"id": "google-calendar", "write": False}]}]
    import_legacy(core, root)


def cache_event(core, event, calendar="google-calendar"):
    with core.db.transaction() as db:
        db.execute("INSERT INTO calendar_events VALUES(?,?,?,?)", ("google:school", calendar, event["id"], json.dumps(event)))


def test_adopt_feishu_maps_requires_verified_target_and_exact_cached_google_event(core, tmp_path):
    feishu_legacy(core, tmp_path, {"google-calendar|event-one": {"feishu_event_id": "old-feishu", "gid": "event-one"}, "google-calendar|missing": {"feishu_event_id": "unknown"}})
    cache_event(core, {"id": "event-one", "status": "confirmed"})
    result = adopt_legacy_feishu_mappings(core)
    assert result == {"adopted": 1, "unmatched": 1, "verified": True}
    with core.db.connect() as db:
        row = db.execute("SELECT * FROM calendar_mappings").fetchone()
    assert row["event_id"] == "old-feishu"
    assert row["item_id"] == "google:school:google-calendar:event-one"
    assert json.loads(row["document"])["hash"] is None
    assert adopt_legacy_feishu_mappings(core)["adopted"] == 0
    assert any(a["kind"] == "legacy_feishu_mappings" for a in core.list_artifacts())


def test_adopt_feishu_maps_refuses_unverified_or_changed_target(core, tmp_path):
    feishu_legacy(core, tmp_path, {"google-calendar|event-one": {"feishu_event_id": "old-feishu"}}, target="other-feishu-calendar")
    cache_event(core, {"id": "event-one"})
    assert adopt_legacy_feishu_mappings(core) == {"adopted": 0, "unmatched": 1, "verified": False}
    with core.db.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM calendar_mappings").fetchone()[0] == 0


def test_adopt_recurring_feishu_instances_uses_original_time_and_preserves_cancelled_intent(core, tmp_path):
    from datetime import datetime, timedelta
    start = datetime.now(UTC).replace(microsecond=0, minute=0, second=0)
    cancelled = start + timedelta(days=1)
    legacy_start = "master_" + start.strftime("%Y%m%dT%H%M%SZ")
    legacy_cancelled = "master_" + cancelled.strftime("%Y%m%dT%H%M%SZ")
    feishu_legacy(core, tmp_path, {f"google-calendar|{legacy_start}": {"feishu_event_id": "old-active", "gid": legacy_start}, f"google-calendar|{legacy_cancelled}": {"feishu_event_id": "old-cancelled", "gid": legacy_cancelled}})
    cache_event(core, {"id": "master", "recurrence": ["RRULE:FREQ=DAILY;COUNT=3"], "start": {"dateTime": start.isoformat()}, "end": {"dateTime": (start + timedelta(hours=1)).isoformat()}})
    cache_event(core, {"id": legacy_cancelled, "recurringEventId": "master", "status": "cancelled", "originalStartTime": {"dateTime": cancelled.isoformat()}})
    assert adopt_legacy_feishu_mappings(core)["adopted"] == 2
    with core.db.connect() as db:
        rows = {r["event_id"]: r for r in db.execute("SELECT * FROM calendar_mappings")}
    assert rows["old-active"]["item_id"].endswith("master@" + start.isoformat())
    assert rows["old-cancelled"]["item_id"].endswith("master@" + cancelled.isoformat())
    assert json.loads(rows["old-cancelled"]["document"])["managed_instance"] is True
    assert json.loads(rows["old-cancelled"]["document"])["deleted"] is False


def test_adopt_feishu_maps_never_overwrites_existing_or_ambiguous_mapping(core, tmp_path):
    feishu_legacy(core, tmp_path, {"google-calendar|event-one": {"feishu_event_id": "old-feishu"}})
    cache_event(core, {"id": "event-one"})
    with core.db.transaction() as db:
        db.execute("INSERT INTO calendar_mappings VALUES('feishu','feishu-calendar','old-feishu','unrelated','{}')")
    assert adopt_legacy_feishu_mappings(core)["adopted"] == 0


def test_adopt_all_day_recurring_feishu_ids_uses_original_date(core, tmp_path):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    day = datetime.now(ZoneInfo("Asia/Hong_Kong")).date()
    cancelled = day + timedelta(days=1)
    first_id, cancelled_id = "master_" + day.strftime("%Y%m%d"), "master_" + cancelled.strftime("%Y%m%d")
    feishu_legacy(core, tmp_path, {f"google-calendar|{first_id}": {"feishu_event_id": "old-active"}, f"google-calendar|{cancelled_id}": {"feishu_event_id": "old-cancelled"}})
    cache_event(core, {"id": "master", "recurrence": ["RRULE:FREQ=DAILY;COUNT=3"], "start": {"date": day.isoformat(), "timeZone": "Asia/Hong_Kong"}, "end": {"date": cancelled.isoformat(), "timeZone": "Asia/Hong_Kong"}})
    cache_event(core, {"id": cancelled_id, "recurringEventId": "master", "status": "cancelled", "originalStartTime": {"date": cancelled.isoformat(), "timeZone": "Asia/Hong_Kong"}})
    assert adopt_legacy_feishu_mappings(core)["adopted"] == 2
