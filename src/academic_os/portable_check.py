"""Offline packaged-runtime diagnostic. Uses only temporary data and loopback HTTP."""
from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time


def diagnose_mail_triage(directory):
    from datetime import datetime, timedelta, timezone
    from .config import Settings
    from .core import Core
    from .db import Database, dumps
    from .mail_query import search_mail
    from .notifications import Notifications

    settings = Settings(data_dir=Path(directory))
    core = Core(Database(settings.database_path), settings)
    due = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    with core.db.transaction() as db:
        db.execute('INSERT INTO mail VALUES(?,?,?,?)', ('diagnostic:m1', 'diagnostic', dumps(
            {'id': 'm1', 'key': 'diagnostic:m1', 'account': 'diagnostic', 'body': 'offline original', 'subject': 'Diagnostic', 'date_nodes': []}), due))
    first, second = core.ingest('gmail:diagnostic', [
        {'source_id': 'm1:deadline', 'title': 'Dated candidate', 'due_at': due, 'review_state': 'needs_analysis'},
        {'source_id': 'm1:unknown', 'title': 'Undated candidate', 'review_state': 'needs_analysis'}])
    assert not core.list_items(view='formal')
    notices = Notifications(core)
    assert notices.generate()['considered'] == 0 and not notices.list()
    assert search_mail(core, review='pending')['total'] == 1
    confirmed = core.review_mail_item(first['id'], 'confirm', first['version'], 'confirm-offline')
    assert confirmed == core.review_mail_item(first['id'], 'confirm', first['version'], 'confirm-offline')
    assert notices.generate()['considered'] == 1 and notices.list()
    ignored = core.review_mail_item(second['id'], 'ignore', second['version'], 'ignore-offline')
    restored = core.review_mail_item(second['id'], 'restore', ignored['version'], 'restore-offline')
    assert restored['source_due_at'] is None
    assert len(core.get_mail('diagnostic:m1')['linked_items']) == 2
    assert core.get_mail('diagnostic:m1')['body'] == 'offline original'
    assert len(Core(core.db, settings).list_items(view='formal')) == 1
    return ['mail_triage_independent_candidates', 'mail_triage_confirm_idempotency', 'mail_triage_reminder_gate', 'mail_triage_ignore_restore_unknown_date', 'mail_triage_links_restart']


def diagnose():
    import certifi
    import httpx
    import keyring
    import tkinter
    from mcp.server.fastmcp import FastMCP

    from .config import Settings
    from .core import Core
    from .db import Database
    from .exports import write_summary
    from .model_gateway import DisabledModelGateway
    from .runtime import Runtime
    from .sources import moodle_command
    from .connections import Connections

    gui = tkinter.Tk()
    gui.withdraw()
    gui.update()
    gui.destroy()
    assert Path(certifi.where()).is_file()
    assert keyring.get_keyring().priority > 0
    assert FastMCP("portable-diagnostic")
    checks = ["tk_gui", "tls_certificates", "credential_manager_backend", "mcp_sdk"]
    with tempfile.TemporaryDirectory(prefix="Academic OS 离线-") as directory:
        def free_port():
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                return sock.getsockname()[1]

        port, remote_port = free_port(), free_port()
        while remote_port == port:
            remote_port = free_port()
        settings = Settings(data_dir=Path(directory) / "data", port=port, dashboard_port=remote_port)
        config = Path(directory) / "config.json"
        settings.save(config)
        core = Core(Database(settings.database_path), settings)
        class EmptyCredentialStore:
            def __init__(self):
                self.values = {}

            def get(self, name):
                if name not in self.values:
                    raise RuntimeError('missing')
                return self.values[name]

            def set(self, name, value):
                self.values[name] = value

        isolated_secrets = EmptyCredentialStore()
        connection = Connections(core, config, isolated_secrets)
        assert connection.summary()['google_client_configured']
        assert not settings.google_accounts and not settings.moodle
        bundled = json.loads(isolated_secrets.get('google-oauth-client'))
        assert set(bundled) == {'client_id', 'client_secret'}
        assert settings.google_network_mode == 'auto'
        checks += ['fresh_credential_store_bundled_client', 'no_personal_tokens', 'automatic_network_default']
        failures = []
        for restart in range(2):
            stop = threading.Event()
            runtime = Runtime(core, config)

            def run():
                try:
                    runtime.run(stop)
                except BaseException as exc:
                    failures.append(type(exc).__name__)

            thread = threading.Thread(target=run)
            thread.start()
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10) as client:
                    deadline = time.monotonic() + 30
                    while not (len(runtime.servers) == 2 and all(s.started for s in runtime.servers)):
                        if failures or time.monotonic() > deadline:
                            raise RuntimeError("diagnostic_startup_failed")
                        time.sleep(.1)
                    assert client.get("/").status_code == 200
                    assert "reset-password" in client.get("/assets/app.js").text
                    if not restart:
                        assert client.post("/api/v1/setup", json={"username": "diagnostic", "password": "eight888"}).status_code == 200
                    login = client.post("/api/v1/login", json={"username": "diagnostic", "password": "eight888"})
                    assert login.status_code == 200
                    headers = {"X-CSRF-Token": login.json()["csrf_token"], "Idempotency-Key": "diagnostic-item"}
                    item = client.post("/api/v1/items", headers=headers, json={"title": "离线测试事项", "estimate_minutes": 30})
                    assert item.status_code == 200
                    assert len(client.get("/api/v1/items").json()) == 1
                    assert client.get("/api/v1/health").json()["model_enabled"] is False
                    assert httpx.get(f"http://127.0.0.1:{remote_port}/api/v1/dashboard", trust_env=False).status_code == 401
                    assert httpx.get(f"http://127.0.0.1:{remote_port}/api/v1/items", trust_env=False).status_code == 404
                    assert write_summary(core).is_file()
            finally:
                stop.set()
                thread.join(timeout=90)
                if thread.is_alive():
                    raise RuntimeError("diagnostic_shutdown_failed")
            assert not failures, failures
        checks += ["http_start_stop_restart", "eight_character_login", "persistent_items", "remote_readonly_boundary", "markdown_export"]
        checks += diagnose_mail_triage(Path(directory) / 'triage')
        # Exercise the packaged parser with the actual Windows trailing-slash shape.
        # The school API and credentials are isolated; no external login is used.
        import base64
        import hashlib
        fingerprint = hashlib.md5(b'https://school.invaliddiagnostic-passport').hexdigest()
        state, flow = connection.new_flow('moodle', client.cookies.get('aos_local'),
                                          site='https://school.invalid', fingerprint=fingerprint)
        connection.probe_moodle = lambda site, token: {'site_name': 'Diagnostic', 'username': 'diagnostic', 'courses': 1}
        callback = 'academicos://token=' + base64.b64encode((fingerprint + ':::diagnostic-token').encode()).decode() + '/'
        assert connection.finish_moodle(callback)['ok'] and flow['status'] == 'done'
        try:
            connection.finish_moodle(callback)
        except ValueError:
            pass
        else:
            raise AssertionError('callback_replay_accepted')
        checks += ['windows_moodle_callback_slash_and_replay']
        result = subprocess.run(moodle_command(), input="{}", capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert result.returncode == 0
        assert json.loads(result.stdout)["complete"] is False
        assert DisabledModelGateway().analyze({"request_id": "check"})["status"] == "disabled"
        checks += ["isolated_moodle_subprocess", "model_disabled"]
    print(json.dumps({"ok": True, "checks": checks}, ensure_ascii=False))
    return 0
