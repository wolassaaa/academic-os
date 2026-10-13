import importlib.util
from pathlib import Path
import sqlite3

import pytest


def helper():
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'package-handoff.py'
    spec = importlib.util.spec_from_file_location('package_handoff', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backup_preserves_business_data_and_password_without_changing_source(tmp_path):
    source, target = tmp_path / 'source.sqlite3', tmp_path / 'copy.sqlite3'
    with sqlite3.connect(source) as db:
        db.execute('PRAGMA journal_mode=WAL')
        for table in ('mail', 'items', 'calendar_mappings', 'notifications', 'users', 'sessions', 'tokens', 'locks', 'login_attempts'):
            db.execute(f'CREATE TABLE {table}(value TEXT)')
            db.execute(f'INSERT INTO {table} VALUES(?)', ('preserve-' + table,))
        db.commit()
        report = helper().snapshot_database(source, target)
        assert report['integrity'] == 'ok'
        for table in ('mail', 'items', 'calendar_mappings', 'notifications', 'users'):
            with sqlite3.connect(target) as copied:
                assert copied.execute(f'SELECT value FROM {table}').fetchone()[0] == 'preserve-' + table
        for table in ('sessions', 'tokens', 'locks', 'login_attempts'):
            with sqlite3.connect(target) as copied:
                assert copied.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 1


def test_backup_refuses_overwrite(tmp_path):
    source, target = tmp_path / 'source.sqlite3', tmp_path / 'keep.sqlite3'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE items(id TEXT)')
    target.write_bytes(b'keep')
    with pytest.raises(FileExistsError):
        helper().snapshot_database(source, target)
    assert target.read_bytes() == b'keep'


def test_runtime_keeps_embedded_python_archives_and_bytecode(tmp_path):
    source, target = tmp_path / 'runtime', tmp_path / 'packed'
    (source / '_internal').mkdir(parents=True)
    (source / '_internal/base_library.zip').write_bytes(b'runtime-library')
    (source / '_internal/dependency.pyc').write_bytes(b'runtime-bytecode')
    helper().copy_tree(source, target, runtime=True)
    assert (target / '_internal/base_library.zip').read_bytes() == b'runtime-library'
    assert (target / '_internal/dependency.pyc').read_bytes() == b'runtime-bytecode'


def test_inflight_leases_recover_only_in_copy_and_keep_dedup_keys(tmp_path):
    source, target = tmp_path / 'live.sqlite3', tmp_path / 'copy.sqlite3'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE jobs(status TEXT,due_at REAL,lease_until REAL,lease_token TEXT,error TEXT,unique_key TEXT)')
        db.execute("INSERT INTO jobs VALUES('running',0,9999999999,'old-lease',NULL,'keep-job-key')")
        db.execute('CREATE TABLE notifications(state TEXT,due_at REAL,lease_until REAL,lease_token TEXT,last_error TEXT,unique_key TEXT)')
        db.execute("INSERT INTO notifications VALUES('sending',0,9999999999,'old-lease',NULL,'keep-notice-key')")
        db.commit()
        helper().snapshot_database(source, target)
        with sqlite3.connect(target) as copied:
            assert copied.execute('SELECT status,lease_token,unique_key FROM jobs').fetchone() == ('failed', None, 'keep-job-key')
            assert copied.execute('SELECT state,lease_token,unique_key FROM notifications').fetchone() == ('failed', None, 'keep-notice-key')
        assert db.execute('SELECT status,lease_token FROM jobs').fetchone() == ('running', 'old-lease')
        assert db.execute('SELECT state,lease_token FROM notifications').fetchone() == ('sending', 'old-lease')


def test_cleared_credentials_leave_no_binary_residue_in_backup(tmp_path):
    source, target = tmp_path / 'live.sqlite3', tmp_path / 'copy.sqlite3'
    marker = 'PRIVATE_SESSION_SENTINEL_' + 'z' * 200
    with sqlite3.connect(source) as db:
        db.execute('PRAGMA secure_delete=OFF')
        db.execute('CREATE TABLE sessions(token_hash TEXT)')
        db.execute('INSERT INTO sessions VALUES(?)', (marker,))
        db.commit()
    helper().snapshot_database(source, target)
    assert marker.encode() not in target.read_bytes()
    with sqlite3.connect(source) as db:
        assert db.execute('SELECT token_hash FROM sessions').fetchone()[0] == marker
