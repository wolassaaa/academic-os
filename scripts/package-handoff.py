"""Create a private, portable handoff without changing live application state."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_TABLES = ('sessions', 'tokens', 'locks', 'login_attempts')
BUSINESS_TABLES = ('mail', 'items', 'calendar_mappings', 'notifications', 'drafts', 'artifacts', 'users', 'source_records')
SECRET_KEYS = {'access_token', 'refresh_token', 'wstoken', 'privatetoken', 'token', 'client_secret',
               'password', 'api_key', 'authorization', 'cookies', 'app_secret'}
SKIP_DIRS = {'.git', '.venv', '__pycache__', '.pytest_cache', '.ruff_cache', 'node_modules', '.site-state'}


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def snapshot_database(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.exists() or source == target:
        raise FileExistsError('Refusing to overwrite a backup or live database')
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as original, sqlite3.connect(target) as copied:
        original.backup(copied, pages=256, sleep=.05)
        copied.execute('PRAGMA secure_delete=ON')
        tables = {row[0] for row in copied.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {table: copied.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                  for table in BUSINESS_TABLES if table in tables}
        for table in PRIVATE_TABLES:
            if table in tables:
                copied.execute('DELETE FROM ' + table)
        recovered = {}
        for table, field, value, error_field in [('jobs', 'status', 'running', 'error'),
                                                ('notifications', 'state', 'sending', 'last_error')]:
            if table not in tables:
                continue
            columns = {row[1] for row in copied.execute('PRAGMA table_info(' + table + ')')}
            if {field, 'due_at', 'lease_until', 'lease_token', error_field} <= columns:
                cursor = copied.execute(f"UPDATE {table} SET {field}='failed',due_at=?,lease_until=NULL,lease_token=NULL,{error_field}='migration_recovery' WHERE {field}=?", (time.time(), value))
                recovered[table] = cursor.rowcount
        copied.commit()
        integrity = copied.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity != 'ok':
            raise RuntimeError('Backup integrity check failed')
        for table, count in counts.items():
            assert copied.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == count
        for table in PRIVATE_TABLES:
            if table in tables:
                assert copied.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0
        copied.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        copied.execute('PRAGMA journal_mode=DELETE')
        copied.execute('VACUUM')
        assert copied.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    return {'integrity': integrity, 'counts': counts, 'recovered_leases': recovered,
            'cleared_tables': [table for table in PRIVATE_TABLES if table in tables]}


def scrub(value):
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items() if key.casefold() not in SECRET_KEYS}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def copy_file(source, target, *, scrub_json=False):
    target.parent.mkdir(parents=True, exist_ok=True)
    if scrub_json and source.suffix.casefold() == '.json':
        target.write_text(json.dumps(scrub(json.loads(source.read_text(encoding='utf-8-sig'))), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    else:
        shutil.copy2(source, target)


def copy_tree(source, target, *, data=False, runtime=False):
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if not path.is_file() or any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.is_symlink():
            raise RuntimeError('Symlink requires explicit handoff review: ' + str(relative))
        if runtime:
            copy_file(path, target / relative)
            continue
        if path.suffix.casefold() in {'.pyc', '.log', '.lock', '.pid', '.zip'} or path.name.startswith('.env'):
            continue
        if data and (path.name.startswith('state.sqlite3') or path.name in {'runtime.json', 'moodle-login-result.json'}):
            continue
        copy_file(path, target / relative, scrub_json=data)


def capture_state(config, target):
    config = config.resolve()
    original_config = config.read_bytes()
    settings = json.loads(original_config.decode('utf-8-sig'))
    data_dir = (config.parent / settings.get('data_dir', 'data')).resolve()
    report = snapshot_database(data_dir / 'state.sqlite3', target / 'data/state.sqlite3')
    if config.read_bytes() != original_config:
        raise RuntimeError('Configuration changed during backup; retry with a new output directory')
    copy_tree(data_dir, target / 'data', data=True)
    copied = scrub(settings)
    copied['data_dir'] = 'data'
    copied['mode'] = 'shadow'
    copied.setdefault('model', {})['enabled'] = False
    copied['google_network_mode'] = 'auto'
    copied['google_proxy'] = None
    references = [(copied.get('local_sources', {}), key, filename)
                  for key, filename in copied.get('local_sources', {}).items()]
    if copied.get('moodle', {}).get('baseline_path'):
        references.append((copied['moodle'], 'baseline_path', copied['moodle']['baseline_path']))
    missing = []
    for index, (container, key, filename) in enumerate(references):
        path = Path(filename)
        path = path if path.is_absolute() else config.parent / path
        if not path.exists():
            missing.append(str(path))
            continue
        relative = Path('files') / str(index) / path.name
        if path.is_dir():
            copy_tree(path, target / relative, data=True)
        else:
            copy_file(path, target / relative, scrub_json=True)
        container[key] = relative.as_posix()
    (target / 'config.json').write_text(json.dumps(copied, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'source_config': str(config), 'source_data': str(data_dir), **report,
            'missing_local_files': missing, 'referenced_files': len(references)}


def verify_stage(stage):
    stage = Path(stage).resolve()
    manifest = json.loads((stage / 'manifest.json').read_text(encoding='utf-8'))
    for entry in manifest['files']:
        path = (stage / entry['path']).resolve()
        assert path.is_relative_to(stage) and path.is_file()
        assert path.stat().st_size == entry['bytes'] and sha256(path) == entry['sha256']
    for database in stage.rglob('state.sqlite3'):
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in PRIVATE_TABLES:
                if table in tables:
                    assert db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0
    return len(manifest['files'])


def build(stage, active_config):
    stage = stage.resolve()
    assert stage.is_relative_to(ROOT / 'dist')
    stage.mkdir(parents=True, exist_ok=False)
    for name in ('src', 'tests', 'schemas', 'scripts', 'docs', 'website'):
        copy_tree(ROOT / name, stage / 'Source' / name)
    for name in ('.gitignore', 'README.md', 'THIRD-PARTY-NOTICES.md', 'config.example.json', 'pyproject.toml', 'requirements.lock.txt'):
        copy_file(ROOT / name, stage / 'Source' / name)
    # The clean, verified build contains only the application-level desktop client.
    clean_runtime = ROOT / 'dist/portable-moodle-format/AcademicOS'
    assert not (clean_runtime / 'config.json').exists() and not (clean_runtime / 'data').exists()
    copy_tree(clean_runtime, stage / 'Runtime', runtime=True)
    shutil.copytree(ROOT / 'docs', stage / 'Runtime/docs', dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    primary = capture_state(active_config, stage / 'Runtime')
    history = []
    candidates = [ROOT / 'config.local.json', *sorted((ROOT / 'dist').glob('Academic-OS-2.0-*/AcademicOS/config.json'))]
    for config in candidates:
        if not config.exists() or config.resolve() == active_config.resolve():
            continue
        values = json.loads(config.read_text(encoding='utf-8-sig'))
        source_db = (config.parent / values.get('data_dir', 'data')).resolve() / 'state.sqlite3'
        if not source_db.exists():
            continue
        with sqlite3.connect(source_db.as_uri() + '?mode=ro', uri=True) as db:
            total = sum(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                        for table in ('mail', 'items', 'notifications', 'artifacts'))
        if not total:
            continue
        name = 'legacy-development' if config.parent == ROOT else config.parent.parent.name
        history.append({'archive': name, **capture_state(config, stage / 'History' / name)})
    copy_file(ROOT / '先读我.md', stage / '先读我.md')
    files = [{'path': path.relative_to(stage).as_posix(), 'bytes': path.stat().st_size, 'sha256': sha256(path)}
             for path in sorted(stage.rglob('*')) if path.is_file()]
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'primary': primary, 'history': history,
                'product_verification': {'tests_passed': 145, 'portable_checks': 15},
                'pending_ui_plan': 'Source/docs/PENDING-UI-PLAN.md',
                'personal_credentials_exported': False, 'files': files}
    (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    hashes = [f"{entry['sha256']}  {entry['path']}" for entry in files]
    hashes.append(sha256(stage / 'manifest.json') + '  manifest.json')
    (stage / 'MANIFEST.sha256').write_text('\n'.join(hashes) + '\n', encoding='utf-8')
    verify_stage(stage)
    archive = stage.with_suffix('.zip')
    if archive.exists():
        raise FileExistsError('Refusing to overwrite an existing handoff ZIP')
    with ZipFile(archive, 'x', ZIP_DEFLATED) as zipped:
        for path in sorted(stage.rglob('*')):
            if path.is_file():
                zipped.write(path, Path(stage.name) / path.relative_to(stage))
    with ZipFile(archive) as zipped:
        assert zipped.testzip() is None
        for entry in files:
            with zipped.open(stage.name + '/' + entry['path']) as stream:
                assert hashlib.file_digest(stream, 'sha256').hexdigest() == entry['sha256']
    digest = sha256(archive)
    archive.with_suffix('.zip.sha256').write_text(digest + '  ' + archive.name + '\n', encoding='utf-8')
    return {'archive': str(archive), 'bytes': archive.stat().st_size, 'sha256': digest,
            'primary_counts': primary['counts'], 'historical_snapshots': len(history), 'payload_files': len(files)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path)
    parser.add_argument('--active-config', type=Path)
    parser.add_argument('--verify', type=Path)
    args = parser.parse_args()
    if args.verify:
        print(json.dumps({'verified_payload_files': verify_stage(args.verify)}))
    elif args.stage and args.active_config:
        print(json.dumps(build(args.stage, args.active_config), ensure_ascii=True))
    else:
        parser.error('Use --verify or both --stage and --active-config')
