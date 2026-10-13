from datetime import datetime, timezone

from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database
from academic_os.extraction import extract_date_nodes, email_records
from academic_os.exports import write_summary
from academic_os.model_gateway import DisabledModelGateway
from academic_os.planner import generate_plan


def build(tmp_path):
    settings = Settings(data_dir=tmp_path)
    return Core(Database(settings.database_path), settings)


def test_multiple_date_nodes_are_preserved_but_not_published_automatically():
    text = "报名截止 2026年10月15日 17:00。活动 2026-10-18 14:00。材料提交 2026/10/16。"
    nodes = extract_date_nodes(text, "Asia/Hong_Kong", datetime(2026, 10, 12, tzinfo=timezone.utc))
    assert len(nodes) == 3
    assert {n["role"] for n in nodes} == {"registration", "event", "submission"}
    records = email_records({"id": "mail1", "subject": "活动", "body": text}, nodes)
    assert len(records) == 3
    assert all(r["review_state"] == "needs_analysis" for r in records)


def test_ambiguous_and_invalid_dates_never_get_fabricated_year_or_time():
    nodes = extract_date_nodes("下周五提交；2026-02-30；11/12；2026-10-18", "Asia/Hong_Kong")
    assert len(nodes) == 1
    assert nodes[0]["all_day"] is True
    assert nodes[0]["due_at"] is None
    assert nodes[0]["date"] == "2026-10-18"


def test_planner_avoids_fixed_events_and_reports_missing_estimates(tmp_path):
    core = build(tmp_path)
    item = core.create_item({"title": "Essay", "estimate_minutes": 60, "due_at": "2026-10-15T10:00:00+08:00"})
    other = core.create_item({"title": "Unestimated"})
    busy = [{"start": "2026-10-14T09:00:00+08:00", "end": "2026-10-14T10:00:00+08:00"}]
    result = generate_plan(core.list_items(), busy, core.settings, datetime.fromisoformat("2026-10-14T09:00:00+08:00"), days=1)
    assert result["changes"][0]["item_id"] == item["id"]
    assert result["changes"][0]["start"] == "2026-10-14T02:00:00+00:00"
    assert result["unplaced"][0]["item_id"] == other["id"]
    assert result["unplaced"][0]["reason"] == "missing_estimate"


def test_planner_respects_committed_slots_and_insufficient_time(tmp_path):
    core = build(tmp_path)
    item = core.create_item({"title": "Impossible", "estimate_minutes": 120, "due_at": "2026-10-14T09:30:00+08:00"})
    result = generate_plan([item], [], core.settings, datetime.fromisoformat("2026-10-14T09:00:00+08:00"), 1)
    assert not result["changes"]
    assert result["unplaced"][0]["reason"] == "insufficient_time"


def test_summary_is_atomic_and_model_gateway_is_explicitly_disabled(tmp_path):
    core = build(tmp_path)
    core.create_item({"title": "Read paper", "estimate_minutes": 30})
    target = write_summary(core)
    text = target.read_text(encoding="utf-8")
    assert "Read paper" in text
    assert "影子模式" in text
    assert "模型分析：关闭" in text
    result = DisabledModelGateway().analyze({"request_id": "x", "text": "tomorrow"})
    assert result["status"] == "disabled"
    assert result["facts"] == []
