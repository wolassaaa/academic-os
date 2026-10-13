import json

from academic_os.moodle_process import collect_snapshot, main, safe_evidence
from academic_os.vendor.moodle_client import MoodleClient, UnsafeFunctionError


class Client:
    def __init__(self, fail=None, submission=None):
        self.fail = fail
        self.submission = submission or {"lastattempt": {"submission": {"status": "submitted", "timemodified": 1700000000}}}
        self.calls = []

    def call(self, name, params=None):
        self.calls.append((name, params))
        if name == self.fail:
            raise RuntimeError("secret-token-should-not-appear")
        return {
            "core_webservice_get_site_info": {"userid": 1},
            "core_enrol_get_users_courses": [{"id": 10, "fullname": "Course"}],
            "core_calendar_get_action_events_by_timesort": {"events": []},
            "mod_assign_get_assignments": {"courses": [{"id": 10, "assignments": [{"id": 20, "cmid": 200, "name": "Essay", "duedate": 1800000000}]}]},
            "mod_quiz_get_quizzes_by_courses": {"quizzes": []},
            "core_course_get_contents": [{"modules": [{"id": 200, "instance": 20, "modname": "assign", "name": "Essay"}, {"id": 300, "modname": "lesson", "name": "Reading"}]}],
            "mod_assign_get_submission_status": self.submission,
        }[name]


def test_complete_snapshot_keeps_instance_identity_and_missing_dates():
    client = Client()
    result = collect_snapshot({}, client=client)
    assert result["complete"]
    essay, reading = sorted(result["records"], key=lambda r: r["title"])
    assert essay["source_id"] == "moodle:activity:assign:200"
    assert essay["submission_status"] == "submitted"
    assert json.loads(essay["evidence_text"])["submission"]["lastattempt"]["submission"]["status"] == "submitted"
    assert ("mod_assign_get_submission_status", {"assignid": 20}) in client.calls
    assert reading["due_at"] is None
    assert reading["review_state"] == "needs_date"


def test_failed_status_is_unknown_and_incomplete_without_leaking_errors():
    result = collect_snapshot({}, client=Client(fail="mod_assign_get_submission_status"))
    assert not result["complete"]
    assert next(r for r in result["records"] if r["title"] == "Essay")["submission_status"] == "unknown"
    assert "secret-token" not in json.dumps(result)


def test_draft_files_do_not_count_as_submission():
    client = Client(submission={"lastattempt": {"submission": {"status": "draft", "plugins": [{"fileareas": [{"files": [{"filename": "draft.pdf"}]}]}]}}})
    result = collect_snapshot({}, client=client)
    assert next(r for r in result["records"] if r["title"] == "Essay")["submission_status"] == "not_started"


def test_course_failure_marks_partial_and_keeps_dedicated_records():
    result = collect_snapshot({}, client=Client(fail="core_course_get_contents"))
    assert not result["complete"]
    assert any(r["title"] == "Essay" for r in result["records"])


def test_baseline_does_not_fabricate_time(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"items": [{"course": "Other", "name": "Exam", "date": "2026-11-01", "weight": 30}]}))
    result = collect_snapshot({"baseline_path": str(path)}, client=Client())
    exam = next(r for r in result["records"] if r["title"] == "Exam")
    assert exam["due_at"] is None
    assert exam["review_state"] == "needs_date"
    assert "2026-11-01" in exam["evidence_text"]


def test_client_rejects_writes_and_parameter_override():
    import pytest
    client = MoodleClient("https://example.edu", "secret")
    with pytest.raises(UnsafeFunctionError):
        client.call("mod_assign_save_submission")
    with pytest.raises(UnsafeFunctionError):
        client.call("core_webservice_get_site_info", {"wsfunction": "mod_assign_save_submission"})


def test_main_returns_sanitized_json_for_bad_credentials(monkeypatch, capsys):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO('{"credentials":{"token":"private"}}'))
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["complete"] is False
    assert "private" not in json.dumps(result)


def test_missing_dedicated_feed_preserves_due_field_for_partial_core_merge():
    result = collect_snapshot({}, client=Client(fail="mod_assign_get_assignments"))
    essay = next(record for record in result["records"] if record["title"] == "Essay")
    assert "due_at" not in essay
    assert not result["complete"]


def test_feed_warnings_keep_accessible_assignments_and_submission_status():
    class Partial(Client):
        def call(self, name, params=None):
            result = super().call(name, params)
            if name == 'mod_assign_get_assignments':
                result['warnings'] = [{'warningcode': '1', 'item': 'module'}]
            return result
    result = collect_snapshot({}, client=Partial())
    essay = next(record for record in result['records'] if record['title'] == 'Essay')
    assert essay['due_at'] == '2027-01-15T08:00:00+00:00'
    assert essay['submission_status'] == 'submitted'
    assert not result['complete']


def test_malformed_course_modules_preserve_other_records():
    class Malformed(Client):
        def call(self, name, params=None):
            if name == "core_course_get_contents":
                return [None, {"modules": [None]}]
            return super().call(name, params)
    result = collect_snapshot({}, client=Malformed())
    assert not result["complete"]
    assert any(r["title"] == "Essay" for r in result["records"])


def test_transport_ignores_environment_proxies(monkeypatch):
    import httpx

    from academic_os.vendor.moodle_client import UrllibTransport
    seen = {}
    class Response:
        def raise_for_status(self):
            seen['status_checked'] = True
        def json(self):
            return {}
    def post(url, **kwargs):
        seen.update(kwargs)
        return Response()
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.example")
    monkeypatch.setattr(httpx, 'post', post)
    assert UrllibTransport().post_form("https://school.example", {"wstoken": "private"}, 30) == {}
    assert seen['trust_env'] is False
    assert seen['headers']['Connection'] == 'close'
    assert seen['timeout'] == 30 and seen['status_checked']
    assert seen['data'] == {'wstoken': 'private'}


def test_safe_urls_keep_numeric_moodle_identity_and_discard_credentials():
    url = "https://school.example/mod/assign/view.php?id=200&courseid=10&cmid=300&wstoken=SECRET&auth=SECRET&x=SECRET#SECRET"
    assert safe_evidence(url) == "https://school.example/mod/assign/view.php?id=200&courseid=10&cmid=300"
    assert safe_evidence("https://school.example/view.php?id=SECRET&courseid=10x") == "https://school.example/view.php"
    assert safe_evidence("https://user:SECRET@school.example/view.php?id=200") is None


def test_explicit_module_due_dates_are_used_without_treating_open_date_as_due():
    class ModuleDates(Client):
        def call(self, name, params=None):
            if name == "core_course_get_contents":
                return [{"modules": [
                    {"id": 300, "modname": "turnitintooltwo", "name": "Paper", "dates": [{"label": "Opened:", "timestamp": 1700000000}, {"label": "Due date:", "timestamp": 1800000000}]},
                    {"id": 301, "modname": "lesson", "name": "Lesson", "dates": [{"label": "Closes:", "timestamp": 1800000010}]},
                    {"id": 302, "modname": "feedback", "name": "Survey", "dates": [{"label": "Opened:", "timestamp": 1700000000}]},
                ]}]
            return super().call(name, params)
    records = {record["title"]: record for record in collect_snapshot({}, client=ModuleDates())["records"]}
    assert records["Paper"]["due_at"] == "2027-01-15T08:00:00+00:00"
    assert records["Paper"]["review_state"] == "ready"
    assert records["Lesson"]["due_at"] == "2027-01-15T08:00:10+00:00"
    assert records["Survey"]["due_at"] is None
    assert json.loads(records["Paper"]["evidence_text"])["module"]["dates"][1]["timestamp"] == 1800000000
