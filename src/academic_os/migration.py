"""Read-only legacy import and local, credential-free backup/restore."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from .config import Settings
from .core import utcnow
from .db import dumps
from .moodle_process import safe_evidence

# Explicit allowlist: no broad recursive copying of the Hermes workspace.
LEGACY_FILES = {
    "gcal_feishu_state.json": "legacy_feishu_mappings",
    "academic_os/grade/assessment_baseline.json": "assessment_baseline",
    "academic_os/grade/grade_data.json": "grades",
    "academic_os/grade/course_units.json": "course_units",
    "academic_os/grade/exam_calendar_map.json": "legacy_calendar_map",
    "grade_data.json": "grades",
    "scripts/grade_data.json": "grades",
    "academic_os/logs/records_cache.json": "legacy_records",
    "academic_os/logs/calendar_map.json": "legacy_calendar_map",
    "academic_os/deadlines_state.json": "legacy_calendar_map",
    "academic_os/logs/reminder_state.json": "legacy_reminder_state",
    "academic_os/logs/escalation_state.json": "legacy_reminder_state",
    "academic_os/logs/grades_state.json": "legacy_grade_state",
    "academic_os/logs/course_archive_state.json": "legacy_archive_state",
    "academic_os/logs/sles_activities.json": "legacy_activities",
    "scripts/mail_dates_state.json": "legacy_mail_snapshot",
    "scripts/mail_snapshot.json": "legacy_mail_snapshot",
    "scripts/gmail_snapshot.json": "legacy_mail_snapshot",
}


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source(root, relative):
    path = root / relative
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
        return None
    # Also reject symlinked parents even when their target remains within root.
    if any(p.is_symlink() for p in path.parents if p != root.parent):
        return None
    return path


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("unsafe_destination")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(dumps(value))
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _literal(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("literal_not_found")


def _known_calendar(core, root):
    """Only trust a literal legacy calendar ID matching configured account metadata."""
    for relative in ("scripts/academic_ddl_sync.py", "academic_os/gcal_sync.py"):
        path = _source(root, relative)
        if path is None:
            continue
        try:
            calendar_id = _literal(path, "CALENDAR_ID")
        except (ValueError, SyntaxError):
            continue
        for account in core.settings.google_accounts:
            for calendar in account.get("calendars", []):
                if calendar.get("id") == calendar_id:
                    return "google:" + account["id"], calendar_id
    return None, None


def _map_events(core, value, fallback):
    if not isinstance(value, dict):
        return 0
    count = 0
    with core.db.transaction() as db:
        for sid, raw in value.items():
            if not isinstance(sid, str):
                continue
            # A title similarity or an old event ID is insufficient evidence of identity.
            matches = db.execute("SELECT DISTINCT item_id FROM source_records WHERE source_id=?", (sid,)).fetchall()
            if len(matches) != 1:
                continue
            document = raw if isinstance(raw, dict) else {"event_id": raw}
            event = document.get("event_id") or document.get("google_event_id") or document.get("gcal_event_id")
            provider = document.get("provider") or fallback[0]
            calendar = document.get("calendar_id") or fallback[1]
            if not all(isinstance(v, str) and v for v in (provider, calendar, event)):
                continue
            mapping = {"purpose": "legacy_deadline", "source_id": sid, "imported_at": utcnow(), "legacy": safe_evidence(document)}
            existing = db.execute("SELECT item_id,document FROM calendar_mappings WHERE provider=? AND calendar_id=? AND event_id=?", (provider, calendar, event)).fetchone()
            if existing:
                # Never replace an existing execution mapping or another item's mapping.
                if existing["item_id"] != matches[0][0] or json.loads(existing["document"]).get("purpose") != "legacy_deadline":
                    continue
                count += 1
                continue
            db.execute("INSERT INTO calendar_mappings VALUES(?,?,?,?,?)", (provider, calendar, event, matches[0][0], dumps(mapping)))
            count += 1
    return count


def _legacy_feishu_target(root):
    path = _source(root, "scripts/gcal_to_feishu.py")
    if path is None:
        return {}
    try:
        value = _literal(path, "FEISHU_CAL_ID")
        if isinstance(value, str) and value:
            return {"verified_legacy_target": value, "target_verification": "literal_scripts/gcal_to_feishu.py", "target_source_sha256": _hash(path)}
    except (OSError, ValueError, SyntaxError):
        pass
    return {}


def adopt_legacy_feishu_mappings(core):
    """Bridge verified legacy mirror IDs against cached Google identities only."""
    # Lazy import avoids a migration/calendar import cycle and reuses its bounded window.
    from .calendars import event_time, mirror_instances

    target = core.settings.feishu.get("calendar_id")
    with core.db.connect() as db:
        artifacts = [json.loads(r[0]) for r in db.execute("SELECT document FROM artifacts WHERE kind='legacy_feishu_mappings'")]
        rows = [dict(r) for r in db.execute("SELECT * FROM calendar_events WHERE provider LIKE 'google:%'")]
    total = sum(len(a.get("data", {})) for a in artifacts if isinstance(a.get("data"), dict))
    verified = [a for a in artifacts if target and a.get("verified_legacy_target") == target and a.get("target_verification") == "literal_scripts/gcal_to_feishu.py"]
    if not verified:
        return {"adopted": 0, "unmatched": total, "verified": False}
    calendars = {}
    for account in core.settings.google_accounts:
        for calendar in account.get("calendars", []):
            calendars.setdefault(calendar["id"], set()).add("google:" + account["id"])
    selected = [r for r in rows if calendars.get(r["calendar_id"]) == {r["provider"]}]
    candidates = {}

    def add(row, legacy_id, materialized_id, managed=False):
        key = (row["calendar_id"], legacy_id)
        identity = f"{row['provider']}:{row['calendar_id']}:{materialized_id}"
        candidates.setdefault(key, set()).add((identity, managed))

    try:
        materialized = mirror_instances(selected, core.settings.timezone)
    except (ValueError, TypeError, OverflowError):
        # An invalid recurrence cannot authorize any guessed instance mapping.
        materialized = [r for r in selected if not json.loads(r["document"]).get("recurrence") and not json.loads(r["document"]).get("recurringEventId")]
    cache = {(r["provider"], r["calendar_id"], r["event_id"]): r for r in selected}
    for row in materialized:
        if row.get("managed_instance"):
            master, separator, original = row["event_id"].rpartition("@")
            if not separator:
                continue
            instant = event_time({"dateTime": original}, core.settings.timezone)
            if instant:
                master_row = cache.get((row["provider"], row["calendar_id"], master))
                master_start = json.loads(master_row["document"]).get("start", {}) if master_row else {}
                if master_start.get("date"):
                    from zoneinfo import ZoneInfo
                    suffix = instant.astimezone(ZoneInfo(master_start.get("timeZone", core.settings.timezone))).strftime("%Y%m%d")
                else:
                    suffix = instant.strftime("%Y%m%dT%H%M%SZ")
                add(row, master + "_" + suffix, row["event_id"], True)
        else:
            add(row, row["event_id"], row["event_id"])
    # A cancelled instance is absent from mirror_instances by design. Its cached
    # original start and cached master establish stable identity and deletion intent.
    for row in selected:
        event = json.loads(row["document"])
        master = event.get("recurringEventId")
        if not master or event.get("status") != "cancelled" or (row["provider"], row["calendar_id"], master) not in cache:
            continue
        original = event_time(event.get("originalStartTime", {}), core.settings.timezone)
        if original:
            original_date = event.get("originalStartTime", {}).get("date")
            suffix = original_date.replace("-", "") if original_date else original.strftime("%Y%m%dT%H%M%SZ")
            legacy_id = master + "_" + suffix
            add(row, legacy_id, master + "@" + original.isoformat(), True)
    adopted, matched = 0, set()
    with core.db.transaction() as db:
        for artifact in verified:
            data = artifact.get("data", {})
            if not isinstance(data, dict):
                continue
            for key, value in data.items():
                if not isinstance(key, str) or "|" not in key or not isinstance(value, dict):
                    continue
                calendar, event = key.split("|", 1)
                remote = value.get("feishu_event_id")
                choices = candidates.get((calendar, event), set())
                if len(choices) != 1 or not isinstance(remote, str) or not remote or value.get("gid", event) != event:
                    continue
                identity, managed = next(iter(choices))
                existing = db.execute("SELECT * FROM calendar_mappings WHERE provider='feishu' AND calendar_id=? AND (event_id=? OR item_id=?)", (target, remote, identity)).fetchall()
                if existing:
                    if len(existing) == 1 and existing[0]["event_id"] == remote and existing[0]["item_id"] == identity:
                        matched.add((artifact.get("id"), key))
                    continue
                document = {"hash": None, "deleted": False, "managed_instance": managed, "purpose": "legacy_feishu_mirror", "legacy_source_key": key, "imported_at": utcnow()}
                db.execute("INSERT INTO calendar_mappings VALUES('feishu',?,?,?,?)", (target, remote, identity, dumps(document)))
                adopted += 1
                matched.add((artifact.get("id"), key))
    return {"adopted": adopted, "unmatched": total - len(matched), "verified": True}


def _import_credentials(core, root):
    """Explicit opt-in: transfer selected secrets directly into the v2 keyring."""
    import keyring

    from .security import SecretStore
    store = SecretStore()
    imported, errors = [], []
    try:
        site = keyring.get_password("academic-os", "moodle-site-url")
        token = keyring.get_password("academic-os", "moodle-token")
        if site and token:
            store.set("moodle", dumps({"site_url": site, "token": token}))
            core.settings.moodle.update(secret_ref="moodle")
            imported.append("moodle")
    except Exception:  # noqa: BLE001 - keyring failures may contain credential context
        errors.append("moodle_credentials_unavailable")
    # File names are matched locally; credential contents never enter artifacts or return values.
    for path in sorted(root.glob("google_token*.json")):
        if _source(root, path.name) is None:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            selected = {key: raw[key] for key in ("client_id", "client_secret", "refresh_token")}
            if not all(isinstance(value, str) and value for value in selected.values()):
                raise ValueError()
            identity = "legacy-google-" + hashlib.sha256(path.name.encode()).hexdigest()[:12]
            store.set(identity, dumps(selected))
            if not any(account["id"] == identity for account in core.settings.google_accounts):
                core.settings.google_accounts.append({"id": identity, "secret_ref": identity, "calendars": []})
            imported.append(identity)
        except Exception:  # noqa: BLE001 - malformed credentials must never enter diagnostics
            errors.append("google_credentials_unavailable")
    # No .env execution or export; only the two documented Feishu application keys.
    env = _source(root, ".env")
    if env:
        try:
            selected = {}
            for line in env.read_text(encoding="utf-8-sig").splitlines():
                key, separator, value = line.strip().removeprefix("export ").partition("=")
                if separator and key in {"FEISHU_APP_ID", "FEISHU_APP_SECRET"}:
                    selected[key] = value.strip().strip("\"'")
            if all(selected.get(key) for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET")):
                store.set("legacy-feishu", dumps({"app_id": selected["FEISHU_APP_ID"], "app_secret": selected["FEISHU_APP_SECRET"]}))
                core.settings.feishu.update(secret_ref="legacy-feishu")
                imported.append("legacy-feishu")
        except Exception:  # noqa: BLE001 - private environment content stays out of diagnostics
            errors.append("feishu_credentials_unavailable")
    return imported, errors


def import_legacy(core, root: Path, import_credentials=False):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("legacy_root_missing")
    destination = core.settings.data_dir / "imports"
    if destination.resolve().is_relative_to(root):
        raise ValueError("import_destination_must_not_modify_legacy_root")
    artifacts, errors, mappings = [], [], 0
    fallback = _known_calendar(core, root)
    for relative, kind in LEGACY_FILES.items():
        source = _source(root, relative)
        if source is None:
            continue
        try:
            raw = json.loads(source.read_text(encoding="utf-8-sig"))
            data = safe_evidence(raw)
            target = destination / relative
            _atomic_json(target, data)
            entry = {"id": "legacy:" + relative, "kind": kind, "data": data, "path": str(target), "source_relative": relative, "sha256": _hash(source), "copied_sha256": _hash(target), "imported_at": utcnow()}
            if kind == "legacy_feishu_mappings":
                entry.update(_legacy_feishu_target(root))
            with core.db.transaction() as db:
                db.execute("INSERT OR REPLACE INTO artifacts VALUES(?,?,?)", (entry["id"], kind, dumps(entry)))
            artifacts.append({"path": str(target.relative_to(core.settings.data_dir)), "kind": kind, "sha256": entry["copied_sha256"]})
            if kind == "assessment_baseline":
                core.settings.local_sources["assessment_baseline"] = str(target)
            elif kind in {"grades", "course_units"}:
                core.settings.local_sources[kind] = str(target)
            elif kind == "legacy_calendar_map":
                mappings += _map_events(core, data, fallback)
        except Exception:  # noqa: BLE001 - isolate an unreadable legacy artifact
            errors.append(relative + ":import_failed")
    timetable = _source(root, "scripts/hkbu_timetable.py")
    if timetable:
        try:
            table = _literal(timetable, "TIMETABLE")
            entries = [{"weekday": day, "start": row[0], "end": row[1], "course": row[2], "title": row[4], "venue": row[5]} for day, rows in table.items() for row in rows]
            data = {"entries": entries, "valid_from": None, "valid_until": None, "needs_term_review": True}
            target = destination / "timetable.json"
            _atomic_json(target, data)
            with core.db.transaction() as db:
                db.execute("INSERT OR REPLACE INTO artifacts VALUES(?,?,?)", ("legacy:timetable", "timetable", dumps({"data": data, "path": str(target), "sha256": _hash(timetable), "imported_at": utcnow()})))
            # Review required: do not activate a literal timetable with unknown term validity.
            artifacts.append({"path": "imports/timetable.json", "kind": "timetable", "sha256": _hash(target)})
        except Exception:  # noqa: BLE001 - legacy Python is parsed, never executed
            errors.append("scripts/hkbu_timetable.py:import_failed")
    credentials = []
    if import_credentials:
        credentials, credential_errors = _import_credentials(core, root)
        errors.extend(credential_errors)
    core.settings.mode = "shadow"
    manifest = {"version": 1, "imported_at": utcnow(), "files": artifacts, "mappings": mappings, "credentials": credentials, "errors": errors}
    _atomic_json(destination / "manifest.json", manifest)
    return {"artifacts": len(artifacts), "mappings": mappings, "credentials": credentials, "errors": errors, "manifest": str(destination / "manifest.json")}


def backup_data(core, destination: Path):
    destination = Path(destination).resolve()
    if destination == core.settings.data_dir or destination.is_relative_to(core.settings.data_dir):
        raise ValueError("backup_destination_must_be_outside_data")
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("backup_destination_must_be_empty")
    core.db.backup(destination / "state.sqlite3")
    files = [{"path": "state.sqlite3", "sha256": _hash(destination / "state.sqlite3")}]
    imports = core.settings.data_dir / "imports"
    if imports.exists():
        for source in sorted(imports.rglob("*")):
            if source.is_file() and not source.is_symlink() and source.resolve().is_relative_to(imports.resolve()) and not any(p.is_symlink() for p in source.parents):
                relative = source.relative_to(core.settings.data_dir)
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                files.append({"path": relative.as_posix(), "sha256": _hash(target)})
    config = asdict(core.settings)
    config["data_dir"] = "."
    config["mode"] = "shadow"
    config["local_sources"] = {key: Path(value).relative_to(core.settings.data_dir).as_posix() for key, value in core.settings.local_sources.items() if Path(value).is_relative_to(core.settings.data_dir)}
    manifest = destination / "manifest.json"
    _atomic_json(manifest, {"version": 1, "created_at": utcnow(), "settings": config, "files": files, "credentials_included": False})
    return manifest


def restore_data(core, backup: Path):
    """Restore a directory backup after validation; caller must persist settings."""
    manifest = Path(backup).resolve()
    if manifest.is_dir():
        manifest = manifest / "manifest.json"
    root = manifest.parent
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("files"), list):
        raise ValueError("invalid_backup")
    sources = {}
    for entry in payload["files"]:
        relative = PurePosixPath(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or "\\" in entry["path"] or ":" in entry["path"]:
            raise ValueError("unsafe_backup_path")
        if entry["path"] != "state.sqlite3" and not entry["path"].startswith("imports/"):
            raise ValueError("unexpected_backup_path")
        source = _source(root, entry["path"])
        if source is None or _hash(source) != entry["sha256"] or entry["path"] in sources:
            raise ValueError("backup_integrity_failed")
        target = core.settings.data_dir / entry["path"]
        if not target.resolve().is_relative_to(core.settings.data_dir) or any(p.is_symlink() for p in target.parents) or target.is_symlink():
            raise ValueError("unsafe_restore_destination")
        sources[entry["path"]] = source
    if "state.sqlite3" not in sources:
        raise ValueError("backup_database_missing")
    config = payload.get("settings")
    if not isinstance(config, dict):
        raise TypeError("backup_settings_missing")
    config = dict(config)
    config.update(data_dir=core.settings.data_dir, host="127.0.0.1", mode="shadow")
    config["model"] = {**config.get("model", {}), "enabled": False}
    local = config.get("local_sources", {})
    config["local_sources"] = {key: str(core.settings.data_dir / value) for key, value in local.items() if isinstance(value, str) and value in sources and value.startswith("imports/")}
    # Validate every setting, including ports/account identities, before any DB mutation.
    restored_settings = Settings(**config)
    with sqlite3.connect(sources["state.sqlite3"].as_uri() + "?mode=ro", uri=True) as source:
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("backup_database_invalid")
    with core.db.transaction() as db:
        worker = db.execute("SELECT expires_at FROM locks WHERE name='active-worker'").fetchone()
        if worker and worker[0] > time.time():
            raise RuntimeError("stop_worker_before_restore")
        core.settings.mode = "shadow"
        db.execute("ATTACH DATABASE ? AS restored", (str(sources["state.sqlite3"]),))
        tables = [row[0] for row in db.execute("SELECT name FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        available = {row[0] for row in db.execute("SELECT name FROM restored.sqlite_master WHERE type='table'")}
        if not set(tables) <= available:
            raise ValueError("incompatible_backup_schema")
        for table in tables:
            db.execute(f'DELETE FROM main."{table}"')
            db.execute(f'INSERT INTO main."{table}" SELECT * FROM restored."{table}"')
        for table in ("sessions", "tokens", "locks"):
            db.execute(f'DELETE FROM "{table}"')
        for artifact in db.execute("SELECT id,document FROM artifacts").fetchall():
            document = json.loads(artifact["document"])
            relative = "imports/" + document["source_relative"] if document.get("source_relative") else None
            if artifact["id"] == "legacy:timetable":
                relative = "imports/timetable.json"
            if relative in sources:
                document["path"] = str(core.settings.data_dir / relative)
                db.execute("UPDATE artifacts SET document=? WHERE id=?", (dumps(document), artifact["id"]))
        db.execute("DELETE FROM meta WHERE key='heartbeat'")
        db.execute("UPDATE jobs SET status='failed',lease_token=NULL,lease_until=NULL,error='restored_interrupted' WHERE status='running'")
        db.execute("UPDATE notifications SET state='failed',lease_token=NULL,lease_until=NULL,last_error='restored_interrupted' WHERE state='sending'")
    for relative, source in sources.items():
        if relative == "state.sqlite3":
            continue
        target = core.settings.data_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    core.settings = restored_settings
    references = {account["secret_ref"] for account in core.settings.google_accounts}
    references.update(value["secret_ref"] for value in (core.settings.moodle, core.settings.feishu) if value.get("secret_ref"))
    missing = []
    from .security import SecretStore
    for reference in sorted(references):
        try:
            SecretStore().get(reference)
        except Exception:  # noqa: BLE001 - report handle only, never keyring diagnostics
            missing.append(re.sub(r"[^\w.:@-]", "_", reference)[:120])
    return {"restored": True, "files": len(sources), "mode": "shadow", "credentials_included": False, "sessions_invalidated": True, "missing_credential_refs": missing}
