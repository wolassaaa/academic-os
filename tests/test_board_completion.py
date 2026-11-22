from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from academic_os.api import create_app
from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database
from academic_os.security import Auth


def setup(tmp_path):
    settings = Settings(data_dir=tmp_path, secure_cookies=False)
    core = Core(Database(settings.database_path), settings)
    auth = Auth(core.db, settings)
    auth.set_password("owner", "a-strong-password-123")
    client = TestClient(create_app(core))
    session = client.post(
        "/api/v1/login", json={"username": "owner", "password": "a-strong-password-123"}
    ).json()
    item = core.create_item(
        {"title": "Fixture assignment", "course": "TEST101", "due_at": "2026-10-20T12:00:00+08:00"},
        "test",
        "create",
    )
    return core, client, item, {"X-CSRF-Token": session["csrf_token"], "Idempotency-Key": "complete-once"}


def test_completion_replay_is_exact_and_preserves_business_identity(tmp_path):
    core, client, item, headers = setup(tmp_path)
    payload = {"expected_version": item["version"], "changes": {"status": "completed"}}
    first = client.patch("/api/v1/items/" + item["id"], json=payload, headers=headers)
    second = client.patch("/api/v1/items/" + item["id"], json=payload, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    result = core.get_item(item["id"])
    assert result["status"] == "completed" and result["version"] == item["version"] + 1
    for key in [
        "id",
        "title",
        "source_due_at",
        "scheduled_start",
        "scheduled_end",
        "evidence",
        "classification",
    ]:
        assert result.get(key) == item.get(key)
    with core.db.connect() as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM audit WHERE entity_id=? AND action='item.update'", (item["id"],)
            ).fetchone()[0]
            == 1
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM jobs WHERE json_extract(payload,'$.item_id')=?", (item["id"],)
            ).fetchone()[0]
            == 1
        )
    # Key reuse with different intent and stale versions are not blind retries.
    assert (
        client.patch(
            "/api/v1/items/" + item["id"],
            json={"expected_version": result["version"], "changes": {"status": "pending"}},
            headers=headers,
        ).status_code
        == 409
    )
    assert (
        client.patch(
            "/api/v1/items/" + item["id"], json=payload, headers={**headers, "Idempotency-Key": "different"}
        ).status_code
        == 409
    )


def test_permissions_are_checked_even_for_replayed_completion(tmp_path):
    core, client, item, headers = setup(tmp_path)
    payload = {"expected_version": item["version"], "changes": {"status": "completed"}}
    assert (
        client.patch(
            "/api/v1/items/" + item["id"], json=payload, headers={"Idempotency-Key": "no-csrf"}
        ).status_code
        == 403
    )
    token = Auth(core.db, core.settings).issue_token("agent", ["read", "draft", "sync"])
    assert (
        client.patch(
            "/api/v1/items/" + item["id"],
            json=payload,
            headers={"Authorization": "Bearer " + token, "Idempotency-Key": "agent"},
        ).status_code
        == 403
    )
    assert client.patch("/api/v1/items/" + item["id"], json=payload, headers=headers).status_code == 200
    assert (
        client.patch(
            "/api/v1/items/" + item["id"],
            json=payload,
            headers={"Authorization": "Bearer " + token, "Idempotency-Key": "complete-once"},
        ).status_code
        == 403
    )
    remote = TestClient(create_app(core, remote=True))
    assert remote.patch("/api/v1/items/" + item["id"], json=payload, headers=headers).status_code == 404


def test_parallel_identical_completion_has_one_effect(tmp_path):
    core, _, item, _ = setup(tmp_path)

    def complete(_):
        return core.update_item(
            item["id"], {"status": "completed"}, item["version"], "test", request_key="parallel"
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(complete, range(2)))
    assert results[0] == results[1] and results[0]["version"] == item["version"] + 1
