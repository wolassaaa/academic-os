from __future__ import annotations

import urllib.parse
from collections.abc import Mapping

import httpx

READ_ONLY_FUNCTIONS = frozenset({
    "core_webservice_get_site_info", "core_enrol_get_users_courses",
    "core_course_get_contents", "core_calendar_get_action_events_by_timesort",
    "mod_assign_get_assignments", "mod_quiz_get_quizzes_by_courses",
    # 提交状态（只读）：作业提交状态 / 测验作答记录
    "mod_assign_get_submission_status", "mod_quiz_get_user_attempts",
    # 成绩册（只读）：课程总成绩 / 分项成绩
    "gradereport_overview_get_course_grades",
    "gradereport_user_get_grade_items",
    "core_grades_get_grades",
})


class UnsafeFunctionError(ValueError):
    pass


class MoodleApiError(RuntimeError):
    def __init__(self, function: str, errorcode: str = "unknown_error"):
        super().__init__(f"{function} failed: {errorcode}")
        self.function, self.errorcode = function, errorcode


class DirectTransport:
    def post_form(self, url: str, data: Mapping[str, object], timeout_seconds: int) -> object:
        # Match the verified school route without process/user proxy inheritance.
        # Use the HTTPX reader verified against HKBU after urllib read timeouts.
        response = httpx.post(url, data=data, trust_env=False, timeout=timeout_seconds,
                              headers={"Connection": "close"})
        response.raise_for_status()
        return response.json()


# Retain the original transport import for existing callers.
UrllibTransport = DirectTransport


class MoodleClient:
    def __init__(self, site_url: str, token: str, transport: object | None = None):
        parsed = urllib.parse.urlparse(site_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("site_url must be an HTTPS site URL")
        if not token:
            raise ValueError("A Moodle token is required")
        self._endpoint = site_url.rstrip("/") + "/webservice/rest/server.php"
        self._token, self._transport = token, transport or DirectTransport()

    def call(self, function: str, params: Mapping[str, object] | None = None) -> object:
        if function not in READ_ONLY_FUNCTIONS:
            raise UnsafeFunctionError(f"Function is not permitted: {function}")
        if {"wstoken", "wsfunction", "moodlewsrestformat"} & set(params or {}):
            raise UnsafeFunctionError("Reserved Moodle parameters cannot be overridden")
        body: dict[str, object] = {"wstoken": self._token, "wsfunction": function, "moodlewsrestformat": "json"}
        body.update(params or {})
        result = self._transport.post_form(self._endpoint, body, timeout_seconds=30)
        if isinstance(result, Mapping) and "exception" in result:
            raise MoodleApiError(function, str(result.get("errorcode", "moodle_exception")))
        return result
