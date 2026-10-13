from __future__ import annotations

import argparse
import getpass
import json
import logging
import signal
import sys
import threading
import zipfile
from pathlib import Path

from .config import Settings
from .core import Core, identifier
from .db import Database
from .runtime import InstanceLock, Runtime
from .security import Auth, SecretStore


def package_source(root, output):
    """Allowlisted source package. Data, actual configs and credentials never enter it."""
    root, output = Path(root).resolve(), Path(output).resolve()
    allowed_files = ('pyproject.toml', 'README.md', 'config.example.json', 'THIRD-PARTY-NOTICES.md', 'requirements.lock.txt')
    paths = [root / name for name in allowed_files if (root / name).is_file()]
    for name in ('src', 'scripts', 'docs', 'schemas', 'tests'):
        if (root / name).is_dir():
            paths.extend(path for path in (root / name).rglob('*') if path.is_file()
                         and '__pycache__' not in path.parts and path.suffix not in {'.pyc', '.pyo'}
                         and not any(part.endswith('.egg-info') for part in path.relative_to(root).parts)
                         and not any(part.startswith('.') for part in path.relative_to(root).parts))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            if path != output:
                archive.write(path, path.relative_to(root).as_posix())
    return output


def parser():
    result = argparse.ArgumentParser(description='Academic OS local administration')
    result.add_argument('--config', type=Path, default=Path('config.json'))
    commands = result.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('--username', default='owner')
    init.add_argument('--data-dir', type=Path)
    commands.add_parser('run')
    commands.add_parser('check')
    reset = commands.add_parser('reset-password', help='Host-local account recovery; revokes browser sessions')
    reset.add_argument('--username', help='Optional when exactly one account exists')
    commands.add_parser('shadow')
    cutover = commands.add_parser('cutover')
    cutover.add_argument('--legacy-stopped', action='store_true', help='Attest that legacy external-writer jobs are stopped')
    cutover.add_argument('--backup-dir', type=Path, required=True, help='New empty destination for the pre-cutover backup')
    token = commands.add_parser('token')
    token.add_argument('--name', default='local-agent')
    token.add_argument('--scopes', nargs='+', choices=['read', 'draft', 'sync', 'confirm', 'notify'], default=['read', 'draft', 'sync'])
    token.add_argument('--store-ref', help='Store bearer token in this Windows identity Credential Manager')
    revoke = commands.add_parser('token-revoke')
    revoke.add_argument('--name', required=True, help='Revoke all tokens issued with this name')
    secret = commands.add_parser('secret')
    secret.add_argument('name', help='Credential Manager reference; value is prompted invisibly')
    backup = commands.add_parser('backup')
    backup.add_argument('destination', type=Path)
    restore = commands.add_parser('restore')
    restore.add_argument('backup', type=Path)
    legacy = commands.add_parser('import-legacy')
    legacy.add_argument('root', type=Path)
    legacy.add_argument('--import-credentials', action='store_true')
    package = commands.add_parser('package')
    package.add_argument('output', type=Path)
    package.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[2])
    return result


def cutover(core, config, backup_dir, legacy_stopped):
    """Explicit, stopped-runtime cutover; no network calls or service activation."""
    if not legacy_stopped:
        raise ValueError('Cutover requires --legacy-stopped after stopping legacy calendar/notification jobs')
    if core.settings.mode != 'shadow':
        raise ValueError('Cutover requires shadow mode')
    with core.db.connect() as db:
        if not db.execute('SELECT 1 FROM users').fetchone():
            raise ValueError('Initialize an owner before cutover')
    accounts = core.settings.google_accounts
    if not any(c.get('write') for account in accounts for c in account.get('calendars', [])):
        raise ValueError('Configure a writable Google calendar before cutover')
    feishu = core.settings.feishu
    notices = core.settings.notifications
    mail_target = notices.get('email_recipients') and notices.get('google_account') in {a['id'] for a in accounts}
    if not ((feishu.get('chat_id') and feishu.get('secret_ref')) or mail_target):
        raise ValueError('Configure a Feishu or email notification target before cutover')
    secrets = SecretStore()
    for account in accounts:
        credential = json.loads(secrets.get(account['secret_ref']))
        if not all(credential.get(key) for key in ('client_id', 'client_secret', 'refresh_token')):
            raise ValueError('Google Credential Manager entry is incomplete')
    if feishu.get('secret_ref'):
        credential = json.loads(secrets.get(feishu['secret_ref']))
        if not all(credential.get(key) for key in ('app_id', 'app_secret')):
            raise ValueError('Feishu Credential Manager entry is incomplete')
    from .migration import backup_data
    manifest = backup_data(core, backup_dir)
    cutover_id = identifier('cutover')
    with core.db.transaction() as db:
        db.execute("UPDATE jobs SET status='superseded',lease_token=NULL,lease_until=NULL WHERE kind IN ('calendar_push','calendar_sync') AND status IN ('queued','failed','preview','running')")
        # Retain the audit history, release old dedup keys, and regenerate only CURRENT reminders on restart.
        db.execute("UPDATE notifications SET unique_key=unique_key || ':superseded:' || id,state='superseded',lease_token=NULL,lease_until=NULL WHERE channel!='workbench' AND state IN ('queued','failed','preview','sending','snoozed')")
        pushed = 0
        for row in db.execute('SELECT document FROM items').fetchall():
            item = json.loads(row[0])
            mapped = db.execute('SELECT 1 FROM calendar_mappings WHERE item_id=?', (item['id'],)).fetchone()
            if item.get('scheduled_start') or mapped:
                core._enqueue(db, 'calendar_push', {'item_id': item['id'], 'version': item['version']}, f"{cutover_id}:{item['id']}:{item['version']}")
                pushed += 1
        core._enqueue(db, 'calendar_sync', {}, cutover_id + ':calendar_sync')
        core.audit(db, 'owner:cli', 'runtime.cutover', cutover_id, {'backup': str(manifest), 'current_calendar_jobs': pushed, 'legacy_stopped_attested': True})
    core.settings.mode = 'live'
    core.settings.save(config)
    return {'mode': 'live', 'backup': str(manifest), 'current_calendar_jobs': pushed, 'runtime_started': False}


def main(argv=None):
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    try:
        if args.command == 'package':
            print(package_source(args.source, args.output))
            return 0
        if args.command == 'secret':
            value = getpass.getpass('Secret value (never logged): ')
            if not value:
                raise ValueError('Secret must not be empty')
            SecretStore().set(args.name, value)
            print('Credential stored for the current Windows identity.')
            return 0
        config = args.config.resolve()
        if args.command == 'init':
            if config.exists():
                raise ValueError('Configuration already exists; refusing to replace it')
            settings = Settings(data_dir=args.data_dir or config.parent / 'data')
            with InstanceLock(settings.data_dir):
                core = Core(Database(settings.database_path), settings)
                with core.db.connect() as db:
                    if db.execute('SELECT 1 FROM users').fetchone():
                        raise ValueError('Data directory already has an owner')
                password = getpass.getpass('Owner password (at least 8 characters): ')
                if password != getpass.getpass('Repeat owner password: '):
                    raise ValueError('Passwords do not match')
                Auth(core.db, settings).set_password(args.username, password)
                config.parent.mkdir(parents=True, exist_ok=True)
                settings.save(config)
            print(f'Initialized in shadow mode: {config}')
            return 0
        settings = Settings.load(config)
        if args.command == 'reset-password':
            # A single SQLite transaction safely serializes with running logins.
            password = getpass.getpass('New password (at least 8 characters): ')
            if password != getpass.getpass('Repeat new password: '):
                raise ValueError('Passwords do not match')
            username = Auth(Database(settings.database_path), settings).reset_password(args.username, password)
            print(f'Password reset for {username}. Local and remote browser sessions revoked.')
            return 0
        if args.command == 'run':
            stop = threading.Event()
            previous = {}
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, lambda *_: stop.set())
            try:
                # Database schema initialization is protected from a competing runtime.
                with InstanceLock(settings.data_dir):
                    core = Core(Database(settings.database_path), settings)
                Runtime(core, config).run(stop)
            finally:
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
            return 0
        with InstanceLock(settings.data_dir):
            core = Core(Database(settings.database_path), settings)
            if args.command == 'check':
                with core.db.connect() as db:
                    integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
                print(json.dumps({'integrity': integrity, 'mode': settings.mode, 'database': str(settings.database_path), 'model_enabled': False}, ensure_ascii=False))
                return 0 if integrity == 'ok' else 1
            if args.command == 'shadow':
                settings.mode = 'shadow'
                settings.save(config)
                with core.db.transaction() as db:
                    core.audit(db, 'owner:cli', 'runtime.shadow', None, {})
                print('Shadow mode saved. Runtime has not been started.')
                return 0
            if args.command == 'cutover':
                print(json.dumps(cutover(core, config, args.backup_dir, args.legacy_stopped), ensure_ascii=False))
                return 0
            if args.command == 'token-revoke':
                with core.db.transaction() as db:
                    count = db.execute('DELETE FROM tokens WHERE name=?', (args.name,)).rowcount
                    core.audit(db, 'owner:cli', 'token.revoke', args.name, {'count': count})
                print(json.dumps({'revoked': count}))
                return 0
            if args.command == 'token':
                value = Auth(core.db, settings).issue_token(args.name, args.scopes)
                if args.store_ref:
                    SecretStore().set(args.store_ref, value)
                    print('Agent token stored in Credential Manager.')
                else:
                    print(value)
                return 0
            from . import migration
            if args.command == 'backup':
                result = migration.backup_data(core, args.destination)
            elif args.command == 'restore':
                result = migration.restore_data(core, args.backup)
                core.settings.mode = 'shadow'
                core.settings.model['enabled'] = False
                core.settings.save(config)
            else:
                result = migration.import_legacy(core, args.root, import_credentials=args.import_credentials)
                core.settings.save(config)
            print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f'Academic OS: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
