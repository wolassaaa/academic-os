from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from academic_os.api import create_app
from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database
from academic_os.security import Auth


def fixture(tmp_path):
    core = Core(Database(tmp_path / "test.db"), Settings(data_dir=tmp_path, secure_cookies=False))
    auth = Auth(core.db, core.settings)
    auth.set_password("owner", "fixture-password-123")
    c = TestClient(create_app(core))
    session = c.post("/api/v1/login", json={"username": "owner", "password": "fixture-password-123"}).json()
    item = core.create_item({"title": "Fixture", "due_at": "2026-10-17T12:00:00+08:00"}, "test", "fixture")
    return core, auth, c, item, {"X-CSRF-Token": session["csrf_token"], "Idempotency-Key": "focus-once"}


def test_focus_persists_and_never_changes_business_state(tmp_path):
    core, _, c, item, h = fixture(tmp_path)
    assert c.get("/api/v1/board-focus").json() == {"revision": 0, "choices": {}}
    body = {"item_id": item["id"], "mode": "pool", "expected_revision": 0}
    a = c.put("/api/v1/board-focus", json=body, headers=h)
    assert a.status_code == 200
    assert a.json() == {"revision": 1, "choices": {item["id"]: "pool"}}
    assert c.put("/api/v1/board-focus", json=body, headers=h).json() == a.json()
    assert c.get("/api/v1/board-focus").json() == a.json()
    assert core.get_item(item["id"]) == item
    with core.db.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM audit WHERE action='board.focus'").fetchone()[0] == 1
    assert c.put("/api/v1/board-focus", json={**body, "mode": "focus"}, headers=h).status_code == 409
    assert (
        c.put("/api/v1/board-focus", json=body, headers={**h, "Idempotency-Key": "stale"}).status_code == 409
    )
    b = c.put(
        "/api/v1/board-focus",
        json={**body, "mode": "auto", "expected_revision": 1},
        headers={**h, "Idempotency-Key": "reset"},
    )
    assert b.json() == {"revision": 2, "choices": {}}
    # Source changes leave the independent preference intact.
    c.put(
        "/api/v1/board-focus",
        json={**body, "mode": "focus", "expected_revision": 2},
        headers={**h, "Idempotency-Key": "pin"},
    )
    core.update_item(item["id"], {"title": "Source changed"}, item["version"], "source")
    assert c.get("/api/v1/board-focus").json()["choices"] == {item["id"]: "focus"}


def test_focus_permissions_validation_and_user_isolation(tmp_path):
    core, auth, c, item, h = fixture(tmp_path)
    body = {"item_id": item["id"], "mode": "focus", "expected_revision": 0}
    assert TestClient(create_app(core)).get("/api/v1/board-focus").status_code == 401
    assert c.put("/api/v1/board-focus", json=body).status_code == 403
    token = auth.issue_token("agent", ["read", "draft", "confirm"])
    assert (
        c.put("/api/v1/board-focus", json=body, headers={"Authorization": "Bearer " + token, **h}).status_code
        == 403
    )
    assert c.put("/api/v1/board-focus", json={**body, "mode": "invalid"}, headers=h).status_code == 422
    assert c.put("/api/v1/board-focus", json={**body, "item_id": "missing"}, headers=h).status_code == 404
    auth.set_password("other", "fixture-password-123")
    candidate = core.create_item({"title": "Unconfirmed mail"}, "test", "candidate")
    with core.db.transaction() as db:
        candidate.update(
            manual_origin=False, evidence=[{"source": "gmail:fixture"}], mail_review={"state": "pending"}
        )
        core._put(db, candidate)
    assert (
        c.put("/api/v1/board-focus", json={**body, "item_id": candidate["id"]}, headers=h).status_code == 422
    )
    other = TestClient(create_app(core))
    other.post("/api/v1/login", json={"username": "other", "password": "fixture-password-123"})
    c.put("/api/v1/board-focus", json=body, headers=h)
    assert other.get("/api/v1/board-focus").json() == {"revision": 0, "choices": {}}
    core.update_item(item["id"], {"status": "completed"}, item["version"])
    assert (
        c.put(
            "/api/v1/board-focus",
            json={**body, "expected_revision": 1},
            headers={**h, "Idempotency-Key": "closed"},
        ).status_code
        == 422
    )
    remote = TestClient(create_app(core, remote=True))
    assert remote.get("/api/v1/board-focus").status_code == 404
    assert remote.put("/api/v1/board-focus", json=body, headers=h).status_code == 404


def test_focus_concurrency(tmp_path):
    _, _, c, item, h = fixture(tmp_path)
    body = {"item_id": item["id"], "mode": "focus", "expected_revision": 0}

    def write(index):
        return c.put(
            "/api/v1/board-focus", json=body, headers={**h, "Idempotency-Key": str(index)}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, range(2))) == [200, 409]
