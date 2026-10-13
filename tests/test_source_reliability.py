import json

from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database
from academic_os.notifications import Notifications
from academic_os.worker import Worker, has_failure


def make_core(tmp_path):
    settings = Settings(data_dir=tmp_path)
    return Core(Database(settings.database_path), settings)


def record(source_id, status="not_started"):
    return {"source_id": source_id, "title": "Essay", "course": "Course A", "kind": "assignment",
            "due_at": "2026-11-01T12:00:00Z", "submission_status": status}


def test_distinct_moodle_module_ids_do_not_merge_by_title(tmp_path):
    core = make_core(tmp_path)
    first = core.ingest("moodle", [record("moodle:activity:assign:100", "submitted")])[0]
    second = core.ingest("moodle", [record("moodle:activity:assign:200")])[0]
    assert first["id"] != second["id"]
    assert len(core.list_items()) == 2
    assert core.get_item(first["id"])["status"] == "submitted"
    assert core.get_item(second["id"])["status"] == "pending"
    assert core.ingest("moodle", [record("moodle:activity:assign:200")])[0]["version"] == second["version"]


def test_title_fallback_requires_an_unambiguous_cross_source_match(tmp_path):
    core = make_core(tmp_path)
    a, b = core.ingest("moodle", [record("module:1"), record("module:2")])
    assert a["id"] != b["id"]
    candidate = core.ingest("other", [record("other:1")])[0]
    assert candidate["id"] not in {a["id"], b["id"]}


def test_cross_source_unique_match_remains_compatible(tmp_path):
    core = make_core(tmp_path)
    first = core.ingest("moodle", [record("module:1")])[0]
    assert core.ingest("other", [record("other:1")])[0]["id"] == first["id"]


def test_unknown_submission_does_not_reopen_verified_submitted_item(tmp_path):
    core = make_core(tmp_path)
    first = core.ingest("moodle", [record("module:1", "submitted")])[0]
    result = core.ingest("moodle", [record("module:1", "unknown")])[0]
    assert result["id"] == first["id"]
    assert result["status"] == "submitted"
    assert result["evidence"][-1]["submission_status"] == "unknown"
    Notifications(core).generate()
    assert not Notifications(core).list()
    assert core.ingest("moodle", [record("module:1", "not_started")])[0]["status"] == "pending"


def test_new_item_with_unknown_submission_still_requires_review(tmp_path):
    core = make_core(tmp_path)
    assert core.ingest("moodle", [record("module:1", "unknown")])[0]["status"] == "unknown"


def test_broken_local_source_does_not_block_other_local_or_remote_collectors(tmp_path, monkeypatch):
    core = make_core(tmp_path)
    broken, valid = tmp_path / "broken.json", tmp_path / "valid.json"
    broken.write_text("{broken", encoding="utf-8")
    valid.write_text(json.dumps({"course": "A"}), encoding="utf-8")
    core.settings.local_sources = {"broken": str(broken), "grades": str(valid), "missing": str(tmp_path / "missing.json")}
    core.settings.moodle = {"enabled": True}
    core.settings.google_accounts = [{"id": "school", "gmail": True}]
    called = []
    monkeypatch.setattr("academic_os.worker.collect_moodle", lambda core: called.append("moodle") or {"complete": True})
    monkeypatch.setattr("academic_os.worker.collect_gmail", lambda *args: called.append("gmail") or {"messages": 0})
    result = Worker(core).collect({"school": object()})
    assert called == ["moodle", "gmail"]
    assert result["local"]["grades"] == "ok"
    assert result["local"]["broken"]["error"] == "JSONDecodeError"
    assert result["local"]["missing"]["error"] == "file_missing"
    assert has_failure(result)
    assert len(core.list_artifacts()) == 1
    health = {row["id"]: row for row in core.health()["sources"]}
    assert health["local:broken"]["status"] == "error"
    assert health["local:grades"]["status"] == "ok"
