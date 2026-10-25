from pathlib import Path

from fastapi.testclient import TestClient

from academic_os.api import create_app
from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database
from academic_os.security import Auth


def fixture(tmp_path, remote=False):
    settings = Settings(data_dir=tmp_path, secure_cookies=False, timezone="America/New_York")
    core = Core(Database(settings.database_path), settings)
    Auth(core.db, settings).set_password("owner", "a-strong-password-123")
    client = TestClient(create_app(core, remote=remote))
    return core, client


def login(client):
    assert (
        client.post(
            "/api/v1/login", json={"username": "owner", "password": "a-strong-password-123"}
        ).status_code
        == 200
    )


def test_context_is_minimal_authenticated_and_readonly(tmp_path):
    for remote in (False, True):
        core, client = fixture(tmp_path / str(remote), remote)
        assert client.get("/api/v1/board-context").status_code == 401
        login(client)
        result = client.get("/api/v1/board-context")
        assert result.status_code == 200
        data = result.json()
        assert set(data) == {"timezone", "now", "semester"}
        assert data["timezone"] == "America/New_York" and data["semester"] is None
        assert data["now"].endswith("+00:00")
        assert client.post("/api/v1/board-context", json={}).status_code == 405
        if remote:
            assert client.get("/api/v1/items").status_code == 404
            assert client.get("/api/v1/health").status_code == 404
            assert client.post("/api/v1/items/x/mail-review", json={}).status_code == 404


def test_new_entry_assets_no_fictional_records_or_inline_styles(tmp_path):
    _, client = fixture(tmp_path)
    assert "/assets/app.js" in client.get("/").text
    for page in ["index.html", "tasks.html", "task.html", "settings.html", "sources.html"]:
        result = client.get("/board/" + page)
        assert result.status_code == 200
        assert "script-src 'self'" in result.headers["content-security-policy"]
        assert "'unsafe-inline'" not in result.headers["content-security-policy"]
        assert "style=" not in result.text
        assert "preview-adapter" not in result.text and "preview-state" not in result.text
        for sample in ["student@example.test", "COMP1000", "2026 秋季学期", "完成课程报告"]:
            assert sample not in result.text
        assert "service-adapter.js" in result.text
    assert client.get("/board/vendor/gsap-3.15.0.min.js").status_code == 200
    assert client.get("/board/preview-adapter.js").status_code == 404
    assert client.get("/board/../../config.json").status_code == 404
    root = Path(__file__).parents[1]
    assert "web/board/vendor/*" in (root / "pyproject.toml").read_text(encoding="utf-8")
