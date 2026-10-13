import importlib.util
import socket
import threading
import time

import httpx
import pytest

from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database


def module():
    assert importlib.util.find_spec('academic_os.runtime'), 'runtime implementation is missing'
    from academic_os import runtime
    return runtime


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def test_instance_lock_rejects_competing_process_and_releases(tmp_path):
    runtime = module()
    with runtime.InstanceLock(tmp_path), pytest.raises(RuntimeError, match='already'), runtime.InstanceLock(tmp_path):
        pass
    with runtime.InstanceLock(tmp_path):
        pass


def test_instance_lock_rejects_other_process_and_survives_crash(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    runtime = module()
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
    child = "from academic_os.runtime import InstanceLock\nimport sys,time\ntry:\n with InstanceLock(sys.argv[1]):\n  print('acquired',flush=True)\n  time.sleep(60)\nexcept RuntimeError:\n sys.exit(42)\n"
    with runtime.InstanceLock(tmp_path):
        result = subprocess.run([sys.executable, '-c', child, str(tmp_path)], env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 42
    process = subprocess.Popen([sys.executable, '-c', child, str(tmp_path)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'acquired'
    finally:
        process.terminate()
        process.communicate(timeout=10)
    with runtime.InstanceLock(tmp_path):
        pass


def test_both_real_servers_one_worker_and_graceful_stop(tmp_path):
    runtime = module()
    settings = Settings(data_dir=tmp_path, port=free_port(), dashboard_port=free_port())
    core = Core(Database(settings.database_path), settings)
    stop = threading.Event()
    errors = []
    service = runtime.Runtime(core)
    def run():
        try:
            service.run(stop)
        except Exception as exc:  # noqa: BLE001 - propagate background failures into the assertion
            errors.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        until = time.monotonic() + 15
        for port in (settings.port, settings.dashboard_port):
            while True:
                try:
                    result = httpx.get(f'http://127.0.0.1:{port}/api/v1/session', timeout=1)
                    assert result.status_code == 200
                    break
                except httpx.ConnectError:
                    assert time.monotonic() < until
                    time.sleep(.05)
        assert httpx.post(f'http://127.0.0.1:{settings.dashboard_port}/api/v1/items', json={'title':'x'}).status_code == 404
        until = time.monotonic() + 5
        while not core.health()['worker_online'] and time.monotonic() < until:
            time.sleep(.05)
        assert core.health()['worker_online']
        with pytest.raises(RuntimeError, match='already'), runtime.InstanceLock(tmp_path):
            pass
    finally:
        stop.set()
        thread.join(20)
    assert not thread.is_alive()
    assert not errors
    with core.db.connect() as db:
        assert db.execute('SELECT count(*) FROM locks').fetchone()[0] == 0


def test_port_collision_starts_no_worker(tmp_path):
    runtime = module()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        settings = Settings(data_dir=tmp_path, port=sock.getsockname()[1], dashboard_port=free_port())
        core = Core(Database(settings.database_path), settings)
        with pytest.raises(OSError):
            runtime.Runtime(core).run(threading.Event())
        assert core.db.get_meta('heartbeat') is None


def test_worker_crash_stops_servers_and_is_not_success(tmp_path, monkeypatch):
    runtime = module()
    settings = Settings(data_dir=tmp_path, port=free_port(), dashboard_port=free_port())
    core = Core(Database(settings.database_path), settings)
    def crash(self, stop):
        raise RuntimeError('worker deliberately crashed')
    monkeypatch.setattr(runtime.Worker, 'run', crash)
    with pytest.raises(RuntimeError, match='worker'):
        runtime.Runtime(core).run(threading.Event())


def test_cli_initialization_prompts_for_password_and_defaults_shadow(tmp_path, monkeypatch):
    assert importlib.util.find_spec('academic_os.cli'), 'CLI implementation is missing'
    from academic_os import cli
    monkeypatch.setattr(cli.getpass, 'getpass', lambda _: 'a-long-test-password')
    config = tmp_path / 'config.json'
    assert cli.main(['--config', str(config), 'init', '--username', 'owner']) == 0
    settings = Settings.load(config)
    assert settings.mode == 'shadow'
    from academic_os.security import Auth
    assert Auth(Database(settings.database_path), settings).login('owner', 'a-long-test-password', 'test', False)
    assert cli.main(['--config', str(config), 'init', '--username', 'owner']) != 0


def test_package_excludes_data_and_config_credentials(tmp_path):
    assert importlib.util.find_spec('academic_os.cli'), 'CLI implementation is missing'
    import zipfile

    from academic_os.cli import package_source
    root = tmp_path / 'project'
    (root / 'src' / 'academic_os').mkdir(parents=True)
    (root / 'src' / 'academic_os' / '__init__.py').write_text('')
    (root / 'pyproject.toml').write_text('[project]\nname="example"')
    (root / 'config.json').write_text('SECRET')
    (root / 'data').mkdir()
    (root / 'data' / 'state.sqlite3').write_text('SECRET')
    (root / 'src' / 'example.egg-info').mkdir()
    (root / 'src' / 'example.egg-info' / 'PKG-INFO').write_text('generated metadata')
    output = tmp_path / 'portable.zip'
    package_source(root, output)
    with zipfile.ZipFile(output) as archive:
        assert 'src/academic_os/__init__.py' in archive.namelist()
        assert not any('.egg-info/' in name for name in archive.namelist())
        assert not any('SECRET' in archive.read(name).decode() for name in archive.namelist())


def test_cli_backup_restore_invalidates_auth_and_rebases_data(tmp_path, monkeypatch):
    from academic_os import cli
    from academic_os.security import Auth
    monkeypatch.setattr(cli.getpass, 'getpass', lambda _: 'a-long-test-password')
    config = tmp_path / 'config.json'
    assert cli.main(['--config', str(config), 'init']) == 0
    settings = Settings.load(config)
    core = Core(Database(settings.database_path), settings)
    core.create_item({'title': 'before'})
    token = Auth(core.db, settings).issue_token('old-agent', ['read'])
    backup = tmp_path / 'backup'
    assert cli.main(['--config', str(config), 'backup', str(backup)]) == 0
    core.create_item({'title': 'after'})
    assert cli.main(['--config', str(config), 'restore', str(backup)]) == 0
    assert [item['title'] for item in core.list_items()] == ['before']
    assert not Auth(core.db, settings).token_scopes(token)
    assert Settings.load(config).mode == 'shadow'


def test_cutover_requires_legacy_attestation_and_requeues_only_current_state(tmp_path, monkeypatch):
    from academic_os import cli
    from academic_os.security import Auth
    from academic_os.notifications import Notifications
    monkeypatch.setattr(cli.getpass, 'getpass', lambda _: 'a-long-test-password')
    monkeypatch.setattr(cli.SecretStore, 'get', lambda *_: '{"client_id":"id","client_secret":"test","refresh_token":"test"}')
    config = tmp_path / 'config.json'
    assert cli.main(['--config', str(config), 'init']) == 0
    settings = Settings.load(config)
    settings.google_accounts = [{'id': 'personal', 'secret_ref': 'google', 'calendars': [{'id': 'primary', 'write': True}]}]
    settings.notifications = {'google_account': 'personal', 'email_recipients': ['owner@example.test']}
    settings.save(config)
    core = Core(Database(settings.database_path), settings)
    item = core.create_item({'title': 'current'})
    with core.db.transaction() as db:
        item.update(scheduled_start='2026-10-15T01:00:00+00:00', scheduled_end='2026-10-15T02:00:00+00:00')
        core._put(db, item)
        old_job = core._enqueue(db, 'calendar_push', {'item_id': item['id'], 'version': 0}, 'old-job')
        db.execute("UPDATE jobs SET status='preview' WHERE id=?", (old_job,))
    old_notice = Notifications(core).queue('email', {'title': 'stale', 'body': 'stale'}, 'old-notice')
    assert cli.main(['--config', str(config), 'cutover', '--backup-dir', str(tmp_path / 'denied')]) == 1
    assert Settings.load(config).mode == 'shadow'
    assert cli.main(['--config', str(config), 'cutover', '--legacy-stopped', '--backup-dir', str(tmp_path / 'cutover-backup')]) == 0
    assert Settings.load(config).mode == 'live'
    assert (tmp_path / 'cutover-backup' / 'manifest.json').exists()
    jobs = core.list_jobs()
    assert next(j for j in jobs if j['id'] == old_job)['status'] == 'superseded'
    current = [j for j in jobs if j['kind'] == 'calendar_push' and j['status'] == 'queued']
    assert len(current) == 1 and current[0]['payload']['version'] == item['version']
    assert next(n for n in Notifications(core).list() if n['id'] == old_notice)['state'] == 'superseded'
    token = Auth(core.db, settings).issue_token('revoked-name', ['read'])
    assert cli.main(['--config', str(config), 'token-revoke', '--name', 'revoked-name']) == 0
    assert not Auth(core.db, settings).token_scopes(token)
    assert cli.main(['--config', str(config), 'shadow']) == 0
    assert Settings.load(config).mode == 'shadow'
