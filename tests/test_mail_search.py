from datetime import datetime, timezone

import pytest

from academic_os.db import dumps
from test_api import clients


def seed(core, key, received_at, account="school", subject="Notice", body="private body"):
    document = {"key": key, "id": key, "account": account, "subject": subject,
                "from": "school@example.test", "body": body, "received_at": received_at}
    with core.db.transaction() as db:
        db.execute("INSERT INTO mail VALUES(?,?,?,?)", (key, account, dumps(document), received_at))


def test_search_reaches_old_mail_and_preserves_legacy_contract(tmp_path):
    core, client, _, headers = clients(tmp_path)
    for i in range(120):
        seed(core, f"new-{i:03}", "2026-10-13T12:00:00+00:00")
    seed(core, "old", "2026-01-01T00:00:00+00:00", body="needle 深度阅读")
    result = client.get("/api/v1/mail/search", params={"q": "深度阅读", "limit": 20}, headers=headers)
    assert result.status_code == 200
    assert result.json()["total"] == 1
    assert result.json()["items"][0]["key"] == "old"
    assert "body" not in result.text
    assert isinstance(client.get("/api/v1/mail", headers=headers).json(), list)
    assert len(client.get("/api/v1/mail", headers=headers).json()) == 100


def test_account_keyword_and_local_date_intersect_with_inclusive_end(tmp_path):
    core, client, _, headers = clients(tmp_path)
    core.settings.timezone = "Asia/Taipei"
    seed(core, "start", "2026-10-12T16:00:00+00:00", subject="100%_阅读")
    seed(core, "end", "2026-10-13T15:59:59.999+00:00", subject="100%_阅读")
    seed(core, "before", "2026-10-12T15:59:59+00:00", subject="100%_阅读")
    seed(core, "after", "2026-10-13T16:00:00+00:00", subject="100%_阅读")
    seed(core, "other", "2026-10-13T10:00:00+00:00", account="personal", subject="100%_阅读")
    seed(core, "wildcard", "2026-10-13T10:00:00+00:00", subject="100xy阅读")
    result = client.get("/api/v1/mail/search", params={"account": "school", "q": "%_",
                        "date_mode": "custom", "start": "2026-10-13", "end": "2026-10-13"}, headers=headers).json()
    assert [m["key"] for m in result["items"]] == ["end", "start"]
    assert result["total"] == 2
    assert result["accounts"] == ["personal", "school"]
    assert result["timezone"] == "Asia/Taipei"


def test_paging_is_stable_for_equal_timestamps_and_counts_all_results(tmp_path):
    core, client, _, headers = clients(tmp_path)
    for key in ("a", "b", "c"):
        seed(core, key, "2026-10-13T00:00:00+00:00")
    params = {"limit": 2}
    first = client.get("/api/v1/mail/search", params=params, headers=headers).json()
    second = client.get("/api/v1/mail/search", params={**params, "offset": 2}, headers=headers).json()
    assert [m["key"] for m in first["items"] + second["items"]] == ["c", "b", "a"]
    assert first["total"] == second["total"] == 3
    assert second["offset"] == 2
    assert second["limit"] == 2


def test_undated_and_empty_results(tmp_path):
    core, client, _, headers = clients(tmp_path)
    seed(core, "unknown", "")
    seed(core, "known", "2026-10-13T00:00:00Z")
    result = client.get("/api/v1/mail/search", params={"date_mode": "undated"}, headers=headers).json()
    assert [m["key"] for m in result["items"]] == ["unknown"]
    result = client.get("/api/v1/mail/search", params={"q": "no match"}, headers=headers).json()
    assert result["total"] == 0 and result["items"] == []


def test_today_and_next_seven_use_settings_timezone(tmp_path):
    core, client, _, headers = clients(tmp_path)
    core.settings.timezone = "Pacific/Kiritimati"
    from zoneinfo import ZoneInfo
    from datetime import timedelta
    today = datetime.now(ZoneInfo(core.settings.timezone)).replace(hour=0, minute=0, second=0, microsecond=0)
    for n in (-1, 0, 6, 7):
        seed(core, str(n), (today + timedelta(days=n)).astimezone(timezone.utc).isoformat())
    result = client.get("/api/v1/mail/search", params={"date_mode": "today"}, headers=headers).json()
    assert [m["key"] for m in result["items"]] == ["0"]
    result = client.get("/api/v1/mail/search", params={"date_mode": "next7"}, headers=headers).json()
    assert [m["key"] for m in result["items"]] == ["6", "0"]


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 301}, {"offset": -1}, {"date_mode": "bad"},
    {"date_mode": "custom"}, {"date_mode": "custom", "start": "2026-10-14", "end": "2026-10-13"},
    {"date_mode": "custom", "start": "bad", "end": "2026-10-13"}, {"q": "x" * 501},
])
def test_invalid_filters_rejected(tmp_path, params):
    _, client, _, headers = clients(tmp_path)
    assert client.get("/api/v1/mail/search", params=params, headers=headers).status_code == 422


def test_search_requires_read_scope_and_stays_off_remote(tmp_path):
    core, client, remote, headers = clients(tmp_path)
    assert client.get("/api/v1/mail/search").status_code == 401
    assert remote.get("/api/v1/mail/search", headers=headers).status_code == 404
    from academic_os.security import Auth
    token = Auth(core.db, core.settings).issue_token("draft-only", ["draft"])
    assert client.get("/api/v1/mail/search", headers={"Authorization": "Bearer " + token}).status_code == 403


@pytest.mark.parametrize("subject,query", [('He said "hello"', '"hello"'),
                                         (r'C:\folder\file', r'C:\folder')])
def test_search_matches_decoded_text_not_json_serialization(tmp_path, subject, query):
    core, client, _, headers = clients(tmp_path)
    seed(core, "literal", "2026-10-13T00:00:00Z", subject=subject)
    result = client.get("/api/v1/mail/search", params={"q": query}, headers=headers).json()
    assert result["total"] == 1


def test_search_does_not_match_json_keys_or_account_metadata(tmp_path):
    core, client, _, headers = clients(tmp_path)
    seed(core, "literal", "2026-10-13T00:00:00Z", account="metadata-only")
    for query in ("received_at", "metadata-only"):
        result = client.get("/api/v1/mail/search", params={"q": query}, headers=headers).json()
        assert result["total"] == 0


def test_date_overflow_returns_validation_error(tmp_path):
    _, client, _, headers = clients(tmp_path)
    result = client.get("/api/v1/mail/search", params={"date_mode": "custom", "start": "9999-12-31", "end": "9999-12-31"}, headers=headers)
    assert result.status_code == 422
