from __future__ import annotations

import hashlib
import hmac
import json
from html import escape
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import Body, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .calendars import calendar_busy
from .core import Conflict
from .db import dumps
from .exports import write_summary
from .model_gateway import DisabledModelGateway
from .notifications import Notifications
from .planner import generate_plan
from .security import Auth
from .school_browser import open_school_login
from .sources import local_busy


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Payload):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)


class ItemCreate(Payload):
    title: str = Field(min_length=1, max_length=500)
    course: str = Field(default="", max_length=300)
    kind: Literal["task", "event", "exam", "assignment", "quiz"] = "task"
    estimate_minutes: int | None = Field(default=None, ge=1, le=10080)
    priority: int = Field(default=2, ge=0, le=5)
    due_at: str | None = None


class ItemUpdate(Payload):
    expected_version: int = Field(ge=1)
    changes: dict


class Draft(Payload):
    changes: list[dict] = Field(min_length=1, max_length=500)


class Plan(Payload):
    days: int = Field(default=7, ge=1, le=30)


def create_app(core, remote=False, config_path=None, connections=None):
    app = FastAPI(title="Academic OS Remote Dashboard" if remote else "Academic OS 2.0 API", version="2.0.0", docs_url=None if remote else "/docs", redoc_url=None, openapi_url=None if remote else "/openapi.json")
    auth = Auth(core.db, core.settings)
    notifications = Notifications(core)
    cookie = "aos_remote" if remote else "aos_local"
    web = Path(__file__).parent / "web"

    @app.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def value_handler(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=422)

    @app.exception_handler(KeyError)
    async def missing_handler(request, exc):
        return JSONResponse({"error": "not_found"}, status_code=404)

    @app.middleware("http")
    async def security_headers(request, call_next):
        if not remote and request.url.hostname not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"error": "invalid_management_host"}, status_code=400)
        response = await call_next(request)
        response.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'", "Cache-Control": "no-store"})
        return response

    def principal(request, scope="read"):
        if not remote and request.headers.get("authorization", "").startswith("Bearer "):
            scopes = auth.token_scopes(request.headers["authorization"][7:])
            if scope not in scopes:
                raise HTTPException(403 if scopes else 401, "Insufficient token scope")
            return "agent"
        token = request.cookies.get(cookie)
        user = auth.session(token, remote)
        if not user:
            raise HTTPException(401, "Login required")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            expected = hashlib.sha256((token + ":csrf").encode()).hexdigest()
            if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), expected):
                raise HTTPException(403, "CSRF token required")
        return user

    def key(value):
        if not value or len(value) > 200:
            raise HTTPException(422, "Idempotency-Key is required (1–200 characters)")
        return value

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(web / "index.html")

    if web.exists():
        app.mount("/assets", StaticFiles(directory=web), name="assets")

    @app.get("/api/v1/session")
    def session(request: Request):
        user = auth.session(request.cookies.get(cookie), remote)
        with core.db.connect() as db:
            initialized = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
        token = request.cookies.get(cookie)
        return {"authenticated": bool(user), "username": user, "remote": remote, "initialized": initialized,
                "csrf_token": hashlib.sha256((token + ":csrf").encode()).hexdigest() if user else None}

    @app.post("/api/v1/login")
    def login(payload: Login, request: Request, response: Response):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "Cross-origin login denied")
        try:
            token = auth.login(payload.username, payload.password, request.client.host if request.client else "unknown", remote)
        except PermissionError as exc:
            raise HTTPException(429 if "Too many" in str(exc) else 401, str(exc)) from exc
        response.set_cookie(cookie, token, max_age=core.settings.session_hours * 3600, httponly=True, secure=core.settings.secure_cookies if remote else False, samesite="strict")
        return {"username": payload.username, "remote": remote, "csrf_token": hashlib.sha256((token + ":csrf").encode()).hexdigest()}

    @app.post("/api/v1/logout")
    def logout(request: Request, response: Response):
        principal(request)
        auth.logout(request.cookies.get(cookie))
        response.delete_cookie(cookie)
        return {"ok": True}

    @app.get("/api/v1/dashboard")
    def dashboard(request: Request):
        principal(request)
        items = core.list_items()
        visible = [{k: item[k] for k in ("id", "title", "course", "kind", "status", "source_due_at", "scheduled_start", "scheduled_end", "review_state", "priority", "version")} for item in items]
        notices = [{k: n[k] for k in ("id", "channel", "state", "attempts", "created_at", "last_error")} for n in notifications.list()]
        with core.db.connect() as db:
            mail_count = db.execute("SELECT COUNT(*) FROM mail").fetchone()[0]
        return {"health": core.health(), "items": visible, "calendars": core.list_calendars(), "notifications": notices,
                "conflicts": [{k: c[k] for k in ("id", "kind", "item_id", "state", "created_at")} for c in core.list_conflicts()],
                "counts": {"active": sum(i["status"] not in {"completed", "submitted", "cancelled"} for i in items), "mail": mail_count, "review": sum(i["review_state"] != "ready" for i in items), "failed": sum(n["state"] == "failed" for n in notices)}, "remote": remote}

    if remote:
        return app

    @app.get('/api/v1/instance', include_in_schema=False)
    def instance(request: Request):
        expected = getattr(core, '_instance_nonce', '')
        if not expected or not hmac.compare_digest(request.headers.get('x-aos-instance', ''), expected):
            raise HTTPException(404)
        return {'ok': True}

    from .connections import Connections
    from .providers import ProviderError
    from .vendor.moodle_client import MoodleApiError
    import httpx
    import webbrowser
    from urllib.error import URLError
    from .network import connection_error_message
    connections = connections or Connections(core, config_path)

    @app.exception_handler(ProviderError)
    @app.exception_handler(MoodleApiError)
    @app.exception_handler(httpx.HTTPError)
    @app.exception_handler(URLError)
    async def connection_error(request, exc):
        if isinstance(exc, MoodleApiError):
            from .moodle_callback import SCHOOL_CODES
            code = exc.errorcode if exc.errorcode in SCHOOL_CODES else 'moodle_api_error'
            return JSONResponse({'error': '学校授权已回传，但 Moodle 移动接口验证失败。', 'code': code}, status_code=502)
        return JSONResponse({'error': connection_error_message(exc)}, status_code=502)

    def owner(request):
        actor = principal(request)
        if actor == 'agent':
            raise HTTPException(403, 'Account management requires owner login')
        return actor

    @app.get('/api/v1/connections')
    def connection_summary(request: Request):
        owner(request)
        return connections.summary()

    @app.get('/api/v1/connections/flows')
    def connection_flows(request: Request):
        owner(request)
        return connections.flow_status(request.cookies.get(cookie))

    @app.post('/api/v1/connections/google/client')
    def google_client(request: Request, payload: dict = Body(...)):
        owner(request)
        raw = payload.get('client_json', '')
        if not isinstance(raw, str) or len(raw) > 20000:
            raise ValueError('客户端配置过大或格式错误。')
        return connections.configure_client(raw)

    @app.post('/api/v1/connections/proxy')
    def connection_proxy(request: Request, payload: dict = Body(...)):
        owner(request)
        proxy = payload.get('proxy') or None
        mode = payload.get('mode', 'manual' if proxy else 'auto')
        def update(settings):
            settings.google_proxy = proxy
            settings.google_network_mode = mode
        connections.save(update)
        return {'ok': True}

    @app.post('/api/v1/connections/google/authorize')
    def google_authorize(request: Request, payload: dict = Body(...)):
        owner(request)
        result = connections.begin_google(request.cookies.get(cookie), payload.get('account_id'), bool(payload.get('force_consent')))
        if payload.get('open_browser'):
            webbrowser.open(result['url'])
        return result

    @app.get('/api/v1/connections/google/callback')
    def google_callback(state: str = '', code: str | None = None, error: str | None = None):
        status = 200
        try:
            if error or not code or len(code) > 8192 or not state or len(state) > 200:
                connections.fail_flow(state, 'Google 登录已取消，请重新登录。')
                raise ValueError('Google 登录未完成，请返回后台重新登录。')
            connections.finish_google(state, code)
            message = 'Google 账号已连接，工作台会自动更新。可以关闭此页。'
        except (ValueError, KeyError, RuntimeError, httpx.HTTPError) as exc:
            status = 422 if isinstance(exc, (ValueError, KeyError)) else 502
            message = str(exc) if isinstance(exc, ValueError) else connection_error_message(exc)
        return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Google 登录结果</title><body><main><h1>Academic OS</h1><p>' + escape(message) + '</p><a href="/">返回工作台重新登录或查看状态</a></main></body></html>', status_code=status)

    @app.post('/api/v1/connections/google/{account_id}/check')
    def google_check(account_id: str, request: Request):
        owner(request)
        return connections.check_google(account_id)

    @app.put('/api/v1/connections/google/{account_id}/calendars')
    def google_calendars(account_id: str, request: Request, payload: dict = Body(...)):
        owner(request)
        return connections.select_calendars(account_id, payload.get('calendars', []))

    @app.delete('/api/v1/connections/google/{account_id}')
    def google_disconnect(account_id: str, request: Request):
        owner(request)
        return connections.disconnect('google', account_id)

    @app.post('/api/v1/connections/moodle/authorize')
    def moodle_authorize(request: Request, payload: dict = Body(...)):
        owner(request)
        result = connections.begin_moodle(request.cookies.get(cookie), payload.get('site_url', ''))
        if payload.get('open_browser'):
            open_school_login(result['url'], core.settings.data_dir)
        return result

    @app.post('/api/v1/connections/moodle/callback')
    def moodle_callback(payload: dict = Body(...)):
        return connections.finish_moodle(payload.get('uri', ''))

    @app.post('/api/v1/connections/moodle/token')
    def moodle_token(request: Request, payload: dict = Body(...)):
        owner(request)
        token = payload.get('token', '')
        if not isinstance(token, str) or not 1 <= len(token) <= 4096:
            raise ValueError('无效的 Moodle 授权。')
        return connections.connect_moodle(payload.get('site_url', ''), token)

    @app.post('/api/v1/connections/moodle/check')
    def moodle_check(request: Request):
        owner(request)
        credential = json.loads(connections.secrets.get(core.settings.moodle.get('secret_ref', 'moodle')))
        return connections.probe_moodle(credential['site_url'], credential['token'])

    @app.delete('/api/v1/connections/moodle')
    def moodle_disconnect(request: Request):
        owner(request)
        return connections.disconnect('moodle')

    @app.post('/api/v1/connections/import-existing')
    def connections_import(request: Request):
        owner(request)
        return connections.import_existing()

    @app.post("/api/v1/setup")
    def setup(payload: Login, request: Request):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "Cross-origin setup denied")
        from .security import hash_password
        encoded = hash_password(payload.password)
        with core.db.transaction() as db:
            if db.execute("SELECT 1 FROM users").fetchone():
                raise Conflict("already_initialized")
            db.execute("INSERT INTO users VALUES(?,?)", (payload.username, encoded))
        return {"ok": True}

    @app.get("/api/v1/health")
    def health(request: Request):
        principal(request)
        return core.health()

    @app.get("/api/v1/items")
    def items(request: Request):
        principal(request)
        return core.list_items()

    @app.get("/api/v1/items/{item_id}")
    def item(item_id: str, request: Request):
        principal(request)
        return core.get_item(item_id)

    @app.post("/api/v1/items")
    def add_item(payload: ItemCreate, request: Request, idempotency_key: str | None = Header(default=None)):
        actor = principal(request, "draft")
        return core.create_item(payload.model_dump(), actor, key(idempotency_key))

    @app.patch("/api/v1/items/{item_id}")
    def update_item(item_id: str, payload: ItemUpdate, request: Request):
        actor = principal(request, "draft")
        if "source_due_at" in payload.changes or payload.changes.get("status") in {"completed", "submitted", "cancelled"}:
            principal(request, "confirm")
        if "title" in payload.changes and core.get_item(item_id).get("scheduled_start"):
            principal(request, "confirm")
        return core.update_item(item_id, payload.changes, payload.expected_version, actor)

    @app.get("/api/v1/mail")
    def mail(request: Request, limit: int = 100, offset: int = 0, q: str | None = None):
        principal(request)
        return core.list_mail(limit, offset, q)

    @app.get("/api/v1/mail/{mail_key}")
    def mail_detail(mail_key: str, request: Request):
        principal(request)
        return core.get_mail(mail_key)

    @app.get("/api/v1/artifacts")
    def artifacts(request: Request):
        principal(request)
        return core.list_artifacts()

    @app.get("/api/v1/calendars")
    def calendars(request: Request):
        principal(request)
        return core.list_calendars()

    @app.get("/api/v1/calendar-events")
    def events(request: Request):
        principal(request)
        with core.db.connect() as db:
            return [{"provider": r["provider"], "calendar_id": r["calendar_id"], **json.loads(r["document"])} for r in db.execute("SELECT * FROM calendar_events LIMIT 5000")]

    @app.get("/api/v1/drafts")
    def drafts(request: Request):
        principal(request)
        return core.list_drafts()

    @app.post("/api/v1/drafts")
    def draft(payload: Draft, request: Request, idempotency_key: str | None = Header(default=None)):
        actor = principal(request, "draft")
        return core.create_draft(payload.changes, key(idempotency_key), actor)

    @app.post("/api/v1/plans/generate")
    def plan(payload: Plan, request: Request, idempotency_key: str | None = Header(default=None)):
        actor = principal(request, "draft")
        request_key = key(idempotency_key)
        with core.plan_lock:
            with core.db.connect() as db:
                fp, replay = core._operation(db, "plan:" + request_key, payload.model_dump())
            if replay is not None:
                return replay
            result = generate_plan(core.list_items(), calendar_busy(core) + local_busy(core), core.settings, days=payload.days)
            if result["changes"]:
                result["draft"] = core.create_draft(result["changes"], request_key, actor, {"unplaced": result["unplaced"]})
            with core.db.transaction() as db:
                db.execute("INSERT INTO operations VALUES(?,?,?)", ("plan:" + request_key, fp, dumps(result)))
            return result

    @app.post("/api/v1/drafts/{draft_id}/confirm")
    def confirm(draft_id: str, request: Request, idempotency_key: str | None = Header(default=None)):
        actor = principal(request, "confirm")
        return core.confirm_draft(draft_id, key(idempotency_key), actor)

    @app.get("/api/v1/jobs")
    def jobs(request: Request):
        principal(request)
        return core.list_jobs()

    @app.post("/api/v1/sync")
    def sync(request: Request, body: dict = Body(default={}), idempotency_key: str | None = Header(default=None)):
        actor = principal(request, "sync")
        kind = body.get("kind", "sources_sync")
        if kind not in {"sources_sync", "calendar_sync", "export"}:
            raise ValueError("Unsupported sync kind")
        return core.enqueue(kind, {}, key(idempotency_key), actor)

    @app.get("/api/v1/notifications")
    def notices(request: Request):
        principal(request)
        return notifications.list()

    @app.post("/api/v1/notifications/{notification_id}/{action}")
    def notice_action(notification_id: str, action: str, request: Request, body: dict = Body(default={})):
        actor = principal(request, "notify")
        if action == "complete":
            principal(request, "confirm")
        return notifications.action(notification_id, action, body.get("minutes", 60), actor)

    @app.get("/api/v1/conflicts")
    def conflicts(request: Request):
        principal(request)
        return core.list_conflicts()

    @app.post("/api/v1/conflicts/{conflict_id}/resolve")
    def resolve_conflict(conflict_id: str, request: Request, body: dict = Body()):
        actor = principal(request, "confirm")
        return core.resolve_conflict(conflict_id, body.get("choice"), body.get("expected_version"), actor)

    @app.get("/api/v1/summary", response_class=PlainTextResponse)
    def summary(request: Request):
        principal(request)
        return write_summary(core).read_text(encoding="utf-8")

    @app.get("/api/v1/model/spec")
    def model_spec(request: Request):
        principal(request)
        return {"schema_version": 1, "enabled": False, "capabilities": ["date_extraction", "task_classification", "plan_suggestions"], "sample_response": DisabledModelGateway().analyze({"request_id": "example"})}

    @app.get("/api/v1/config")
    def config(request: Request):
        actor = principal(request)
        if actor == "agent":
            raise HTTPException(403, "Configuration is owner-only")
        return {"mode": core.settings.mode, "timezone": core.settings.timezone, "availability": core.settings.availability,
                "google_accounts": core.settings.google_accounts, "feishu": core.settings.feishu, "notifications": core.settings.notifications,
                "model": core.settings.model, "google_proxy": core.settings.google_proxy}

    @app.get("/api/v1/audit")
    def audit(request: Request):
        principal(request)
        with core.db.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200")]

    return app
