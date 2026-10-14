"""Isolated, read-only Moodle collector. Credentials arrive only on stdin."""
from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .vendor.deadlines import _timestamp, normalize_assignments, normalize_calendar_events, normalize_quizzes
from .vendor.moodle_client import MoodleClient

ASSESS_MODULES = {
    "assign": "assignment", "quiz": "quiz", "turnitintooltwo": "task", "lesson": "task",
    "workshop": "task", "choice": "task", "feedback": "task", "h5pactivity": "task",
}
SECRET_FIELDS = {"token", "wstoken", "access_token", "refresh_token", "password", "secret", "client_secret", "api_key"}


def safe_evidence(value):
    """Keep source evidence while excluding credentials and token-bearing URLs."""
    if isinstance(value, dict):
        return {k: safe_evidence(v) for k, v in value.items() if str(k).casefold() not in SECRET_FIELDS}
    if isinstance(value, list):
        return [safe_evidence(v) for v in value]
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        try:
            parsed = urlsplit(value)
        except ValueError:
            return None
        if parsed.username or parsed.password:
            return None
        query = [(key, val) for key, val in parse_qsl(parsed.query) if key in {"id", "courseid", "cmid"} and val.isascii() and val.isdecimal()]
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
    return value


def _evidence(value):
    return json.dumps(safe_evidence(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _submission_status(kind, value):
    if kind == "assign":
        attempt = value.get("lastattempt")
        if attempt is None:
            return "not_started"
        if not isinstance(attempt, dict):
            return "unknown"
        submission = attempt.get("submission")
        if submission is None:
            return "not_started"
        if not isinstance(submission, dict):
            return "unknown"
        status = str(submission.get("status", "")).lower()
        return {"submitted": "submitted", "draft": "not_started", "new": "not_started", "reopened": "not_started"}.get(status, "unknown")
    attempts = value.get("attempts")
    if not isinstance(attempts, list):
        return "unknown"
    if not attempts:
        return "not_started"
    states = {a.get("state") for a in attempts if isinstance(a, dict)}
    if "finished" in states:
        return "submitted"
    if states & {"inprogress", "overdue"}:
        return "in_progress"
    return "unknown"


def _module_due(module):
    """Use only explicit Moodle due/close labels with actual Unix timestamps."""
    dates = module.get("dates")
    if not isinstance(dates, list):
        return None
    matches = set()
    for entry in dates:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label", "")).strip().rstrip(":：").strip().casefold()
        if label not in {"due", "due date", "closes", "close", "closing date", "截止日期", "截止时间", "到期日"}:
            continue
        timestamp = _timestamp(entry.get("timestamp"))
        if timestamp:
            matches.add(timestamp.isoformat())
    # Conflicting labels do not establish one authoritative instant.
    return next(iter(matches)) if len(matches) == 1 else None


def baseline_records(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise TypeError("invalid_baseline")
    result = []
    for entry in data["items"]:
        if not isinstance(entry, dict) or not entry.get("course") or not (entry.get("name") or entry.get("title")):
            continue
        title = str(entry.get("name") or entry["title"])
        course = str(entry["course"])
        sid = "assess:" + (course + ":" + title).replace(" ", "_")[:80]
        # A date in a syllabus is not an exact deadline. Never invent 23:59.
        due = entry.get("due_at")
        try:
            dt = datetime.fromisoformat(str(due))
            due = dt.astimezone(UTC).isoformat() if dt.tzinfo and dt.year < 2090 else None
        except (ValueError, TypeError):
            due = None
        result.append({"source_id": sid, "title": title, "course": course, "kind": entry.get("kind", "task"),
                       "due_at": due, "weight_pct": entry.get("weight"), "review_state": "ready" if due else "needs_date",
                       "evidence_text": _evidence({"baseline": entry})})
    return result


def collect_snapshot(config, *, client=None, budget_seconds=840):
    errors, records = [], {}
    partial_functions = set()
    started = time.monotonic()
    if client is None:
        credentials = config.get("credentials", {})
        client = MoodleClient(credentials["site_url"], credentials["token"])

    def read(function, params=None, expected=dict):
        try:
            if time.monotonic() - started >= budget_seconds:
                raise TimeoutError()
            value = client.call(function, params)
            if not isinstance(value, expected) or (isinstance(value, dict) and "exception" in value):
                raise ValueError()
            if isinstance(value, dict) and value.get('warnings'):
                partial_functions.add(function)
                errors.append(function + ':partial_access')
            return value
        except Exception:  # noqa: BLE001 - never expose token-bearing transport diagnostics
            errors.append(function + ":read_failed")
            return None

    site = read("core_webservice_get_site_info")
    if not site or type(site.get("userid")) is not int:
        errors.append("missing_userid")
        courses = []
    else:
        courses = read("core_enrol_get_users_courses", {"userid": site["userid"]}, list) or []
    assigns = read("mod_assign_get_assignments")
    quizzes = read("mod_quiz_get_quizzes_by_courses")
    if assigns is not None and not isinstance(assigns.get("courses"), list):
        errors.append("assignments:invalid_shape")
        assigns = None
    if quizzes is not None and not isinstance(quizzes.get("quizzes"), list):
        errors.append("quizzes:invalid_shape")
        quizzes = None
    index, raw_activities, instances = {}, {}, {}
    for course in (assigns or {}).get("courses", []):
        if not isinstance(course, dict) or not isinstance(course.get("assignments"), list):
            errors.append("assignments:invalid_course")
            continue
        for entry in course.get("assignments", []):
            if not isinstance(entry, dict):
                errors.append("assignments:invalid_activity")
                continue
            if type(entry.get("cmid")) is int and type(entry.get("id")) is int:
                index[("assign", entry["cmid"])] = entry["id"]
                instances[("assign", entry["id"])] = entry["cmid"]
                raw_activities[("assign", entry["cmid"])] = entry
    for entry in (quizzes or {}).get("quizzes", []):
        if not isinstance(entry, dict):
            errors.append("quizzes:invalid_activity")
            continue
        if type(entry.get("coursemodule")) is int and type(entry.get("id")) is int:
            index[("quiz", entry["coursemodule"])] = entry["id"]
            instances[("quiz", entry["id"])] = entry["coursemodule"]
            raw_activities[("quiz", entry["coursemodule"])] = entry

    events, cursor, visited = [], None, set()
    for _ in range(1000):
        params = {"timesortfrom": 0, "limitnum": 50, "limittononsuspendedevents": 1}
        if cursor is not None:
            params["aftereventid"] = cursor
        page = read("core_calendar_get_action_events_by_timesort", params)
        if page is None or not isinstance(page.get("events"), list):
            errors.append("events:invalid_page")
            break
        events.extend(page["events"])
        if len(page["events"]) < 50:
            break
        cursor = page.get("lastid")
        if type(cursor) is not int or cursor in visited:
            errors.append("events:invalid_pagination")
            break
        visited.add(cursor)
    else:
        errors.append("events:page_limit")

    for item in normalize_calendar_events({"events": events}, courses):
        # Action-event instance is an activity instance ID, not always a module ID.
        parts = item.source_id.split(":")
        if len(parts) == 4 and parts[2] in {"assign", "quiz"}:
            cmid = instances.get((parts[2], int(parts[3])))
            if cmid is None:
                continue  # Dedicated lists/contents supply unambiguous module identities.
            sid = f"moodle:activity:{parts[2]}:{cmid}"
        else:
            sid = item.source_id
        records[sid] = {"source_id": sid, "title": item.title, "course": item.course, "kind": item.kind,
                        "due_at": item.due_at.isoformat(), "url": safe_evidence(item.url),
                        "evidence_text": _evidence({"source": "action_events", "aliases": item.aliases})}
    for item in normalize_assignments(assigns, courses) + normalize_quizzes(quizzes, courses):
        module, cmid = item.source_id.split(":")[-2:]
        records[item.source_id] = {"source_id": item.source_id, "title": item.title, "course": item.course,
                                   "kind": item.kind, "due_at": item.due_at.isoformat(),
                                   "evidence_text": _evidence({"activity": raw_activities.get((module, int(cmid)))})}
    for course in courses:
        if not isinstance(course, dict) or type(course.get("id")) is not int:
            errors.append("course:invalid_shape")
            continue
        sections = read("core_course_get_contents", {"courseid": course["id"]}, list)
        for section in sections or []:
            if not isinstance(section, dict) or not isinstance(section.get("modules"), list):
                errors.append("contents:invalid_section")
                continue
            for module in section.get("modules", []):
                if not isinstance(module, dict):
                    errors.append("contents:invalid_module")
                    continue
                kind, cmid = module.get("modname"), module.get("id")
                if kind not in ASSESS_MODULES or type(cmid) is not int or not isinstance(module.get("name"), str):
                    continue
                sid = f"moodle:activity:{kind}:{cmid}"
                if kind in {"assign", "quiz"} and type(module.get("instance")) is int:
                    index[(kind, cmid)] = module["instance"]
                if sid not in records:
                    record = {"source_id": sid, "title": module["name"], "course": course.get("fullname", ""), "kind": ASSESS_MODULES[kind],
                              "url": safe_evidence(module.get("url")), "evidence_text": _evidence({"module": module})}
                    # Failed dedicated API does not mean the existing deadline disappeared.
                    if (kind == "assign" and assigns is not None and 'mod_assign_get_assignments' not in partial_functions) or (kind == "quiz" and quizzes is not None and 'mod_quiz_get_quizzes_by_courses' not in partial_functions) or kind not in {"assign", "quiz"}:
                        record["due_at"] = None
                        record["review_state"] = "needs_date"
                    records[sid] = record
                record = records[sid]
                record['course_id'] = course['id']
                record['course_site'] = config.get('credentials', {}).get('site_url', '')
                evidence = json.loads(record.get("evidence_text", "{}"))
                evidence["module"] = module
                record["evidence_text"] = _evidence(evidence)
                module_due = _module_due(module)
                if module_due and not record.get("due_at"):
                    record.update(due_at=module_due, review_state="ready")

    for sid, record in records.items():
        parts = sid.split(":")
        if len(parts) != 4 or parts[2] not in {"assign", "quiz"}:
            record["submission_status"] = "unknown"
            continue
        kind, cmid = parts[2], int(parts[3])
        instance = index.get((kind, cmid))
        if instance is None:
            errors.append("submission:no_instance")
            value = None
        else:
            function = "mod_assign_get_submission_status" if kind == "assign" else "mod_quiz_get_user_attempts"
            params = {"assignid": instance} if kind == "assign" else {"quizid": instance, "status": "all"}
            value = read(function, params)
        status = _submission_status(kind, value) if value is not None else "unknown"
        record["submission_status"] = status
        if status == "unknown" and value is not None:
            errors.append("submission:unknown")
        evidence = json.loads(record.get("evidence_text", "{}"))
        evidence["submission"] = value if value is not None else {"status": "unknown", "error": "read_failed"}
        record["evidence_text"] = _evidence(evidence)
    if config.get("baseline_path"):
        try:
            for record in baseline_records(config["baseline_path"]):
                records[record["source_id"]] = record
        except Exception:  # noqa: BLE001 - report baseline failure without private file contents
            errors.append("baseline:read_failed")
    return {"complete": not errors, "records": list(records.values()), "errors": sorted(set(errors))}


def main():
    try:
        config = json.load(sys.stdin)
        result = collect_snapshot(config)
    except Exception:  # noqa: BLE001 - subprocess boundary must emit sanitized JSON
        result = {"complete": False, "records": [], "errors": ["moodle_collection_failed"]}
    # Never log exceptions: transport diagnostics can embed tokens or credential URLs.
    # Frozen Windows pipe encodings can ignore PYTHONIOENCODING. ASCII JSON
    # transports every Unicode title losslessly without a console codec crash.
    sys.stdout.write(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
