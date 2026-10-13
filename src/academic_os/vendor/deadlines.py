from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from .models import Deadline

_MODULE_KINDS = {"assign": "assignment", "quiz": "quiz"}


def normalize_calendar_events(events: object, courses: object) -> list[Deadline]:
    """Return only Moodle deadline shapes the MVP can identify without inference."""
    course_names = {
        item["id"]: item.get("fullname", "Unknown course")
        for item in courses
        if isinstance(item, Mapping) and isinstance(item.get("id"), int)
    } if _is_list(courses) else {}
    raw_events = events.get("events", []) if isinstance(events, Mapping) else []
    if not _is_list(raw_events):
        return []
    normalized: list[Deadline] = []
    for event in raw_events:
        if not isinstance(event, Mapping):
            continue
        kind = _event_kind(event)
        due_at = _deadline_time(event)
        event_id = event.get("id")
        title = event.get("name")
        if kind is None or due_at is None or not isinstance(event_id, int) or not isinstance(title, str):
            continue
        course = _course_name(event, course_names)
        source_id, aliases = _event_identity(event, event_id)
        normalized.append(
            Deadline(
                source_id=source_id,
                course=course,
                title=title,
                kind=kind,
                due_at=due_at,
                url=event.get("url") if isinstance(event.get("url"), str) else None,
                source="Moodle action event",
                aliases=aliases,
            )
        )
    return sorted(normalized, key=lambda item: (item.due_at, item.source_id))


def normalize_assignments(assignments: object, courses: object) -> list[Deadline]:
    if not isinstance(assignments, Mapping) or not _is_list(assignments.get("courses")):
        return []
    enrolled = _course_names(courses)
    normalized: list[Deadline] = []
    for course in assignments["courses"]:
        if not isinstance(course, Mapping) or not _is_list(course.get("assignments")):
            continue
        course_id = course.get("id")
        course_name = course.get("fullname") or enrolled.get(course_id) or "Unknown course"
        for assignment in course["assignments"]:
            if not isinstance(assignment, Mapping):
                continue
            activity_id, title = assignment.get("id"), assignment.get("name")
            module_id = assignment.get("cmid", activity_id)
            due_at = _timestamp(assignment.get("duedate"))
            if not isinstance(activity_id, int) or not isinstance(module_id, int) or not isinstance(title, str) or due_at is None:
                continue
            normalized.append(Deadline(
                source_id=f"moodle:activity:assign:{module_id}", course=str(course_name), title=title,
                kind="assignment", due_at=due_at, source="Moodle assignment",
            ))
    return sorted(normalized, key=lambda item: (item.due_at, item.source_id))


def normalize_quizzes(quizzes: object, courses: object) -> list[Deadline]:
    if not isinstance(quizzes, Mapping) or not _is_list(quizzes.get("quizzes")):
        return []
    course_names = _course_names(courses)
    normalized: list[Deadline] = []
    for quiz in quizzes["quizzes"]:
        if not isinstance(quiz, Mapping):
            continue
        activity_id, title = quiz.get("id"), quiz.get("name")
        module_id = quiz.get("coursemodule", activity_id)
        due_at = _timestamp(quiz.get("timeclose"))
        if not isinstance(activity_id, int) or not isinstance(module_id, int) or not isinstance(title, str) or due_at is None:
            continue
        course = course_names.get(quiz.get("course"), "Unknown course")
        normalized.append(Deadline(
            source_id=f"moodle:activity:quiz:{module_id}", course=course, title=title,
            kind="quiz", due_at=due_at, source="Moodle quiz",
        ))
    return sorted(normalized, key=lambda item: (item.due_at, item.source_id))


def _event_kind(event: Mapping[str, object]) -> str | None:
    module = event.get("modulename")
    if isinstance(module, str) and module in _MODULE_KINDS:
        return _MODULE_KINDS[module]
    if event.get("isactionevent") is True:
        return "task"
    return "calendar" if event.get("eventtype") == "course" else None


def _event_identity(event: Mapping[str, object], event_id: int) -> tuple[str, tuple[str, ...]]:
    module, instance = event.get("modulename"), event.get("instance")
    legacy = f"moodle:event:{event_id}"
    if isinstance(module, str) and module and isinstance(instance, int) and instance > 0:
        return f"moodle:activity:{module}:{instance}", (legacy,)
    return legacy, ()


def _course_name(event: Mapping[str, object], course_names: Mapping[object, str]) -> str:
    enrolled_name = course_names.get(event.get("courseid"))
    if isinstance(enrolled_name, str) and enrolled_name:
        return enrolled_name
    nested = event.get("course")
    if isinstance(nested, Mapping):
        for field in ("fullname", "fullnamedisplay", "shortname"):
            value = nested.get(field)
            if isinstance(value, str) and value:
                return value
    direct = event.get("coursefullname")
    return direct if isinstance(direct, str) and direct else "Unknown course"


def _course_names(courses: object) -> dict[object, str]:
    return {
        item["id"]: item.get("fullname", "Unknown course")
        for item in courses
        if isinstance(item, Mapping) and isinstance(item.get("id"), int)
    } if _is_list(courses) else {}


def _deadline_time(event: Mapping[str, object]) -> datetime | None:
    timestamp = event.get("timesort", event.get("timestart"))
    return _timestamp(timestamp)


def _timestamp(value: object) -> datetime | None:
    try:
        result = datetime.fromtimestamp(value, tz=UTC) if type(value) is int and value > 0 else None
        return result if result and result.year < 2090 else None
    except (OverflowError, OSError, ValueError):
        return None


def _is_list(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))
