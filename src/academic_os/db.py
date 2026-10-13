from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, document TEXT NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS item_dedup ON items(json_extract(document,'$.dedup_key'));
CREATE TABLE IF NOT EXISTS source_records(source TEXT NOT NULL, source_id TEXT NOT NULL, item_id TEXT NOT NULL, document TEXT NOT NULL, PRIMARY KEY(source,source_id));
CREATE INDEX IF NOT EXISTS source_items ON source_records(item_id);
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, status TEXT NOT NULL, last_success TEXT, last_attempt TEXT NOT NULL, error TEXT, detail TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, document TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS operations(key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, due_at REAL NOT NULL, lease_until REAL, lease_token TEXT, error TEXT, result TEXT, unique_key TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(status,due_at);
CREATE INDEX IF NOT EXISTS jobs_item ON jobs(kind,json_extract(payload,'$.item_id'),status);
CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY, item_id TEXT, channel TEXT NOT NULL, state TEXT NOT NULL, document TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, due_at REAL NOT NULL, lease_until REAL, lease_token TEXT, unique_key TEXT UNIQUE NOT NULL, receipt TEXT, last_error TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS notification_ready ON notifications(state,due_at);
CREATE INDEX IF NOT EXISTS notification_item ON notifications(item_id,state);
CREATE TABLE IF NOT EXISTS calendars(id TEXT PRIMARY KEY, document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calendar_mappings(provider TEXT NOT NULL, calendar_id TEXT NOT NULL, event_id TEXT NOT NULL, item_id TEXT NOT NULL, document TEXT NOT NULL, PRIMARY KEY(provider,calendar_id,event_id));
CREATE INDEX IF NOT EXISTS calendar_item ON calendar_mappings(item_id,provider);
CREATE TABLE IF NOT EXISTS calendar_events(provider TEXT NOT NULL, calendar_id TEXT NOT NULL, event_id TEXT NOT NULL, document TEXT NOT NULL, PRIMARY KEY(provider,calendar_id,event_id));
CREATE TABLE IF NOT EXISTS cursors(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mail(id TEXT PRIMARY KEY, account TEXT NOT NULL, document TEXT NOT NULL, received_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS mail_received ON mail(received_at DESC);
CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, kind TEXT NOT NULL, document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conflicts(id TEXT PRIMARY KEY, kind TEXT NOT NULL, item_id TEXT, document TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL, entity_id TEXT, document TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at REAL NOT NULL, remote INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS tokens(token_hash TEXT PRIMARY KEY, name TEXT NOT NULL, scopes TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts(key TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_start REAL NOT NULL);
CREATE TABLE IF NOT EXISTS locks(name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at REAL NOT NULL);
"""


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.execute("INSERT OR IGNORE INTO meta VALUES('schema_version','1')")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def backup(self, target: Path):
        target = Path(target).resolve()
        if target == self.path:
            raise ValueError("Backup target must differ from the live database")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(target) as destination:
            source.backup(destination)

    def get_meta(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, dumps(value)))
