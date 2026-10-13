import subprocess
import threading

import pytest

from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database
from academic_os.sources import collect_gmail, collect_moodle


def make_core(tmp_path):
    settings = Settings(data_dir=tmp_path)
    return Core(Database(settings.database_path), settings)


def test_gmail_repeated_page_token_fails_and_keeps_cursor(tmp_path):
    core = make_core(tmp_path)
    class Repeating:
        calls = 0
        def request(self, *_args, **_kwargs):
            self.calls += 1
            assert self.calls <= 3, 'Gmail repeatedly requested the same page'
            return {'messages': [], 'nextPageToken': 'same-token'}
    client = Repeating()
    with pytest.raises(RuntimeError, match='pagination'):
        collect_gmail(core, {'id': 'school'}, client)
    assert client.calls == 2
    with core.db.connect() as db:
        assert db.execute("SELECT count(*) FROM cursors WHERE key='gmail:school'").fetchone()[0] == 0


def test_gmail_stop_event_stops_between_messages(tmp_path):
    core = make_core(tmp_path)
    core.stop_event = threading.Event()
    class StopAfterListing:
        def request(self, *_args, **_kwargs):
            core.stop_event.set()
            return {'messages': [{'id': 'not-fetched'}]}
    with pytest.raises(RuntimeError, match='cancel'):
        collect_gmail(core, {'id': 'school'}, StopAfterListing())
    assert core.list_mail() == []


def test_gmail_page_budget_stops_without_advancing_cursor(tmp_path):
    core = make_core(tmp_path)
    class ManyPages:
        calls = 0
        def request(self, *_args, **_kwargs):
            self.calls += 1
            return {'messages': [], 'nextPageToken': str(self.calls)}
    client = ManyPages()
    with pytest.raises(RuntimeError, match='pagination_limit'):
        collect_gmail(core, {'id': 'school'}, client, max_pages=2)
    assert client.calls == 2
    with core.db.connect() as db:
        assert db.execute('SELECT count(*) FROM cursors').fetchone()[0] == 0


def test_gmail_time_budget_stops_without_sending_request(tmp_path):
    core = make_core(tmp_path)
    with pytest.raises(RuntimeError, match='budget'):
        collect_gmail(core, {'id': 'school'}, None, budget_seconds=0)


def test_moodle_shutdown_interrupts_process_without_credential_arguments(tmp_path, monkeypatch):
    core = make_core(tmp_path)
    core.stop_event = threading.Event()
    monkeypatch.setattr('academic_os.security.SecretStore.get', lambda *_: '{"token":"PRIVATE"}')
    seen = {}
    class Process:
        returncode = None
        terminated = False
        def __init__(self, args, **kwargs):
            seen['args'], seen['kwargs'] = args, kwargs
        def communicate(self, input=None, timeout=None):
            if self.terminated:
                self.returncode = -1
                return '', ''
            seen['input'] = input
            assert timeout <= 2
            core.stop_event.set()
            raise subprocess.TimeoutExpired('sanitized-child', timeout)
        def terminate(self):
            self.terminated = True
            seen['terminated'] = True
        def kill(self):
            seen['killed'] = True
    monkeypatch.setattr('academic_os.sources.subprocess.Popen', Process)
    with pytest.raises(RuntimeError, match='cancel') as error:
        collect_moodle(core)
    assert 'PRIVATE' not in str(error.value)
    assert 'PRIVATE' not in str(seen['args'])
    assert 'PRIVATE' in seen['input']
    assert seen['terminated']


def test_moodle_timeout_poll_sends_credentials_only_once_then_ingests(tmp_path, monkeypatch):
    core = make_core(tmp_path)
    monkeypatch.setattr('academic_os.security.SecretStore.get', lambda *_: '{"token":"PRIVATE"}')
    inputs = []
    class Process:
        returncode = None
        def __init__(self, *_args, **_kwargs):
            pass
        def communicate(self, input=None, timeout=None):
            inputs.append(input)
            if len(inputs) <= 2:
                raise subprocess.TimeoutExpired('sanitized-child', timeout)
            self.returncode = 0
            return '{"records":[{"source_id":"one","title":"Essay"}],"complete":true}', ''
    monkeypatch.setattr('academic_os.sources.subprocess.Popen', Process)
    assert collect_moodle(core) == {'records': 1, 'complete': True}
    assert 'PRIVATE' in inputs[0]
    assert inputs[1:] == [None, None]
    assert core.list_items()[0]['title'] == 'Essay'


def test_moodle_stop_reaps_real_child_process(tmp_path, monkeypatch):
    import sys
    import time

    core = make_core(tmp_path)
    core.stop_event = threading.Event()
    monkeypatch.setattr('academic_os.security.SecretStore.get', lambda *_: '{"token":"test-only"}')
    original_popen = subprocess.Popen
    children = []
    def child(_args, **kwargs):
        process = original_popen([sys.executable, '-c', 'import sys,time;sys.stdin.read();time.sleep(30)'], **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr('academic_os.sources.subprocess.Popen', child)
    timer = threading.Timer(.1, core.stop_event.set)
    started = time.monotonic()
    timer.start()
    try:
        with pytest.raises(RuntimeError, match='cancel'):
            collect_moodle(core)
    finally:
        timer.cancel()
        for process in children:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
    assert time.monotonic() - started < 6
    assert children and children[0].poll() is not None
