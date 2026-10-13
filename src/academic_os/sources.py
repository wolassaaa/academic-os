from __future__ import annotations

import ast
import base64
import html
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path
from zoneinfo import ZoneInfo

from .core import utcnow
from .db import dumps
from .extraction import email_records, extract_date_nodes


def message_text(part):
    text_parts, html_parts = [], []
    def walk(node):
        data = node.get("body", {}).get("data")
        if data and node.get("mimeType") in {"text/plain", "text/html"}:
            value = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
            (text_parts if node["mimeType"] == "text/plain" else html_parts).append(value)
        for child in node.get("parts", []):
            walk(child)
    walk(part)
    if text_parts:
        return "\n".join(text_parts)[:200000]
    return html.unescape(re.sub("<[^>]+>", " ", "\n".join(html_parts)))[:200000]


def collect_gmail(core, account, client, budget_seconds=300, max_pages=1000):
    deadline = time.monotonic() + budget_seconds

    def check_budget():
        if getattr(core, 'stop_event', None) and core.stop_event.is_set():
            raise RuntimeError('gmail_collection_cancelled')
        if time.monotonic() >= deadline:
            raise RuntimeError('gmail_collection_budget_exhausted')

    check_budget()
    account_id = account["id"]
    cursor_key = "gmail:" + account_id
    with core.db.connect() as db:
        row = db.execute("SELECT value FROM cursors WHERE key=?", (cursor_key,)).fetchone()
    # A rolling overlap handles delayed deliveries; IDs deduplicate persisted messages.
    since = int(json.loads(row[0])) - 86400 if row else int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())
    started = int(datetime.now(timezone.utc).timestamp())
    token = None
    count = 0
    pages, seen_tokens = 0, set()
    while True:
        check_budget()
        if pages >= max_pages:
            raise RuntimeError('gmail_pagination_limit_exceeded')
        pages += 1
        params = {"q": f"after:{since}", "maxResults": 100}
        if token:
            params["pageToken"] = token
        result = client.request("GET", "/gmail/v1/users/me/messages", params=params)
        for entry in result.get("messages", []):
            check_budget()
            mid = account_id + ":" + entry["id"]
            with core.db.connect() as db:
                exists = db.execute("SELECT document FROM mail WHERE id=?", (mid,)).fetchone()
            if exists and json.loads(exists[0]).get("ingested"):
                continue
            if exists:
                mail = json.loads(exists[0])
            else:
                raw = client.request("GET", "/gmail/v1/users/me/messages/" + entry["id"], params={"format": "full"})
                check_budget()
                headers = {h["name"].casefold(): h["value"] for h in raw.get("payload", {}).get("headers", [])}
                mail = {"id": entry["id"], "key": mid, "account": account_id, "subject": headers.get("subject", "(无主题)"),
                    "from": headers.get("from", ""), "body": message_text(raw.get("payload", {})),
                    "received_at": datetime.fromtimestamp(int(raw.get("internalDate", 0)) / 1000, timezone.utc).isoformat(), "analysis_status": "disabled", "labels": raw.get("labelIds", [])}
            mail["date_nodes"] = extract_date_nodes(mail["subject"] + "\n" + mail["body"], core.settings.timezone)
            with core.db.transaction() as db:
                db.execute("INSERT OR REPLACE INTO mail VALUES(?,?,?,?)", (mid, account_id, dumps(mail), mail["received_at"]))
            # Only school notifications become candidate academic items. Other mail stays searchable.
            sender_domain = parseaddr(mail["from"])[1].casefold().rpartition("@")[2]
            school_mail = any(sender_domain == domain.casefold() or sender_domain.endswith("." + domain.casefold()) for domain in account.get("school_domains", ["hkbu.edu.hk", "life.hkbu.edu.hk"]))
            if school_mail:
                core.ingest("gmail:" + account_id, email_records(mail, mail["date_nodes"]))
            if school_mail and core.settings.notifications.get("mail_alerts", True):
                from .notifications import Notifications
                notices = Notifications(core)
                document = {"title": "学校新邮件 · " + mail["subject"], "body": f"账号：{account_id}\n发件人：{mail['from']}\n候选日期节点：{len(mail['date_nodes'])}。模型分析关闭，请在邮件工作台核对。"}
                notices.queue("workbench", document, "mail:workbench:" + mid)
                if core.settings.feishu.get("chat_id"):
                    notices.queue("feishu", document, "mail:feishu:" + mid)
                if "IMPORTANT" in mail.get("labels", []) and core.settings.notifications.get("email_recipients"):
                    notices.queue("email", document, "mail:email:" + mid)
            mail["ingested"] = True
            with core.db.transaction() as db:
                db.execute("UPDATE mail SET document=? WHERE id=?", (dumps(mail), mid))
            count += 1
        token = result.get("nextPageToken")
        if not token:
            break
        if token in seen_tokens:
            raise RuntimeError('gmail_pagination_token_repeated')
        seen_tokens.add(token)
    check_budget()
    with core.db.transaction() as db:
        db.execute("INSERT OR REPLACE INTO cursors VALUES(?,?)", (cursor_key, dumps(started)))
    core.source_health(cursor_key, True, detail={"new_messages": count})
    return {"messages": count}


def moodle_command():
    if getattr(sys, "frozen", False):
        return [sys.executable, "--moodle-collector"]
    return [sys.executable, "-m", "academic_os.moodle_process"]


def collect_moodle(core):
    if getattr(core, 'stop_event', None) and core.stop_event.is_set():
        raise RuntimeError('moodle_collection_cancelled')
    env = dict(os.environ)
    for key in list(env):
        if key.casefold() in {"http_proxy", "https_proxy", "all_proxy"}:
            del env[key]
    env.update(NO_PROXY="*", no_proxy="*", PYTHONIOENCODING="utf-8")
    # Credential payload goes via stdin, never command arguments or logs.
    from .security import SecretStore
    secret = SecretStore().get(core.settings.moodle.get("secret_ref", "moodle"))
    config = {"credentials": json.loads(secret)}
    process = subprocess.Popen(moodle_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    pending_input = dumps(config)
    deadline = time.monotonic() + 900
    try:
        while True:
            if getattr(core, 'stop_event', None) and core.stop_event.is_set():
                raise RuntimeError('moodle_collection_cancelled')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('moodle_collection_budget_exhausted')
            try:
                output, _ = process.communicate(input=pending_input, timeout=min(2, remaining))
                break
            except subprocess.TimeoutExpired:
                # communicate resumes its buffered write/read on later calls; never resend credentials.
                pending_input = None
    finally:
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
    if process.returncode:
        raise RuntimeError("moodle_collection_failed; check authorization and network")
    if getattr(core, 'stop_event', None) and core.stop_event.is_set():
        raise RuntimeError('moodle_collection_cancelled')
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        raise RuntimeError('moodle_collection_invalid_response') from None
    core.ingest("moodle", payload["records"])
    if not payload.get("complete"):
        core.source_health("moodle", False, "moodle_incomplete_snapshot", {"records": len(payload["records"]), "errors": payload.get("errors", [])})
    return {"records": len(payload["records"]), "complete": bool(payload.get("complete"))}


def collect_local(core):
    results = {}
    for kind, filename in core.settings.local_sources.items():
        try:
            path = Path(filename)
            if not path.exists():
                core.source_health("local:" + kind, False, "file_missing")
                results[kind] = {"error": "file_missing"}
                continue
            if path.is_dir():
                documents = [{"path": str(p.relative_to(path)), "bytes": p.stat().st_size, "modified": p.stat().st_mtime} for p in path.rglob("*") if p.is_file()]
                value = {"files": documents, "root": str(path), "count": len(documents)}
            else:
                value = json.loads(path.read_text(encoding="utf-8"))
                if kind == "assessment_baseline":
                    from .moodle_process import baseline_records
                    core.ingest("assessment_baseline", baseline_records(path))
            with core.db.transaction() as db:
                db.execute("INSERT OR REPLACE INTO artifacts VALUES(?,?,?)", ("local:" + kind, kind, dumps({"data": value, "updated_at": utcnow()})))
            core.source_health("local:" + kind, True)
            results[kind] = "ok"
        except Exception as exc:
            # One private file's contents must neither leak nor block remote collectors.
            error = type(exc).__name__
            core.source_health("local:" + kind, False, error)
            results[kind] = {"error": error}
    return results


def local_busy(core, now=None, days=30):
    now = now or datetime.now(timezone.utc)
    tz = ZoneInfo(core.settings.timezone)
    result = []
    for artifact in core.list_artifacts():
        if artifact["kind"] not in {"timetable", "holidays"}:
            continue
        data = artifact.get("data", {})
        if data.get("needs_term_review"):
            continue
        if artifact["kind"] == "holidays":
            result.extend(data.get("events", []))
            continue
        for offset in range(days):
            day = now.astimezone(tz).date() + timedelta(days=offset)
            if data.get("valid_from") and day.isoformat() < data["valid_from"]:
                continue
            if data.get("valid_until") and day.isoformat() > data["valid_until"]:
                continue
            for entry in data.get("entries", []):
                if entry["weekday"] == day.weekday():
                    result.append({"start": datetime.fromisoformat(f"{day}T{entry['start']}").replace(tzinfo=tz).isoformat(), "end": datetime.fromisoformat(f"{day}T{entry['end']}").replace(tzinfo=tz).isoformat(), "title": entry.get("title", "课程")})
    return result


def import_timetable_python(source: Path, destination: Path):
    """Read literal configuration without executing legacy Python code."""
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    table = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TIMETABLE" for t in n.targets))
    entries = [{"weekday": day, "start": row[0], "end": row[1], "course": row[2], "title": row[4], "venue": row[5]} for day, rows in table.items() for row in rows]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(dumps({"entries": entries, "valid_from": None, "valid_until": None, "needs_term_review": True}), encoding="utf-8")
    return destination
