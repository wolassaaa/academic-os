from fastapi.testclient import TestClient

from academic_os.api import create_app
from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database
from academic_os.security import Auth


def clients(tmp_path):
    settings = Settings(data_dir=tmp_path, secure_cookies=False)
    core = Core(Database(settings.database_path), settings)
    auth = Auth(core.db, settings)
    auth.set_password("owner", "a-strong-password-123")
    token = auth.issue_token("agent", ["read", "draft", "sync"])
    return core, TestClient(create_app(core)), TestClient(create_app(core, remote=True)), {"Authorization": "Bearer " + token}


def test_private_resources_require_auth_and_remote_has_no_mutations(tmp_path):
    core, local, remote, agent = clients(tmp_path)
    assert local.get("/api/v1/items").status_code == 401
    assert remote.get("/api/v1/dashboard").status_code == 401
    login = remote.post("/api/v1/login", json={"username": "owner", "password": "a-strong-password-123"})
    assert login.status_code == 200
    assert remote.get("/api/v1/dashboard").status_code == 200
    assert remote.post("/api/v1/items", json={"title": "No"}, headers=agent).status_code == 404
    assert remote.get("/api/v1/mail", headers=agent).status_code == 404
    assert remote.post("/api/v1/drafts/x/confirm", json={}, headers=agent).status_code == 404


def test_agent_can_prepare_but_cannot_confirm_and_create_is_idempotent(tmp_path):
    core, client, _, headers = clients(tmp_path)
    payload = {"title": "Work", "estimate_minutes": 60}
    first = client.post("/api/v1/items", json=payload, headers={**headers, "Idempotency-Key": "create-1"})
    replay = client.post("/api/v1/items", json=payload, headers={**headers, "Idempotency-Key": "create-1"})
    assert first.status_code == 200
    assert first.json() == replay.json()
    assert len(core.list_items()) == 1
    item = first.json()
    changes = [{"item_id": item["id"], "expected_version": 1, "start": "2026-11-01T09:00:00+08:00", "end": "2026-11-01T10:00:00+08:00"}]
    draft = client.post("/api/v1/drafts", json={"changes": changes}, headers={**headers, "Idempotency-Key": "draft-1"}).json()
    assert client.post(f"/api/v1/drafts/{draft['id']}/confirm", json={}, headers={**headers, "Idempotency-Key": "confirm-1"}).status_code == 403


def test_browser_mutation_requires_csrf_and_login_rate_limits(tmp_path):
    core, client, _, _ = clients(tmp_path)
    login = client.post("/api/v1/login", json={"username": "owner", "password": "a-strong-password-123"})
    assert login.status_code == 200
    assert client.post("/api/v1/items", json={"title": "No"}, headers={"Idempotency-Key": "x"}).status_code == 403
    assert client.post("/api/v1/items", json={"title": "Yes"}, headers={"Idempotency-Key": "y", "X-CSRF-Token": login.json()["csrf_token"]}).status_code == 200
    for _ in range(5):
        assert client.post("/api/v1/login", json={"username": "owner", "password": "wrong"}).status_code == 401
    assert client.post("/api/v1/login", json={"username": "owner", "password": "wrong"}).status_code == 429


def test_no_secret_or_mail_body_in_remote_dashboard(tmp_path):
    core, _, remote, _ = clients(tmp_path)
    item = core.ingest("gmail:x", [{"source_id": "x", "title": "Private task", "evidence_text": "SECRET_BODY"}])[0]
    remote.post("/api/v1/login", json={"username": "owner", "password": "a-strong-password-123"})
    result = remote.get("/api/v1/dashboard")
    assert "SECRET_BODY" not in result.text
    assert item["title"] in result.text


def test_setup_is_local_only_and_can_only_happen_once(tmp_path):
    settings = Settings(data_dir=tmp_path, secure_cookies=False)
    core = Core(Database(settings.database_path), settings)
    local = TestClient(create_app(core))
    remote = TestClient(create_app(core, remote=True))
    assert remote.post("/api/v1/setup", json={}).status_code == 404
    payload = {"username": "owner", "password": "a-strong-password-123"}
    assert local.post("/api/v1/setup", json=payload).status_code == 200
    assert local.post("/api/v1/setup", json=payload).status_code == 409
