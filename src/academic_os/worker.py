from __future__ import annotations

import json
import queue
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .calendars import CalendarSync
from .core import identifier, utcnow
from .db import dumps
from .exports import write_summary
from .notifications import Notifications
from .providers import Feishu, Google
from .security import SecretStore
from .sources import collect_gmail, collect_local, collect_moodle
from .mail_triage import formal


def has_failure(value):
    if isinstance(value, dict):
        return bool(value.get("error") or value.get("errors") or value.get("complete") is False) or any(has_failure(v) for v in value.values())
    if isinstance(value, list):
        return any(has_failure(v) for v in value)
    return False


class Worker:
    def __init__(self, core, handlers=None):
        self.core, self.owner = core, identifier("worker")
        self.notifications = Notifications(core)
        if handlers is None:
            self.notifications = Notifications(core, {'email': self.send_email, 'feishu': self.send_feishu})
            self.handlers = {kind: (lambda payload, kind=kind: self.connected_job(kind, payload)) for kind in ('sources_sync', 'calendar_sync', 'calendar_push')}
            self.handlers['export'] = lambda _: {'path': str(write_summary(core))}
        else:
            self.handlers = handlers

    def connected_job(self, kind, payload):
        # Build from current settings at job execution, so login/reconnect needs no restart.
        with self.core.configuration_lock:
            store = SecretStore()
            accounts = list(self.core.settings.google_accounts)
            providers = {a['id']: Google(store, a['secret_ref'], self.core.settings.google_proxy, network_mode=self.core.settings.google_network_mode) for a in accounts}
            feishu = Feishu(store, self.core.settings.feishu) if self.core.settings.feishu.get('secret_ref') else None
            calendars = CalendarSync(self.core, providers, feishu)
        try:
            if kind == 'sources_sync':
                return self.collect(providers, accounts)
            if kind == 'calendar_sync':
                return self.sync_calendars(calendars)
            with self.core.configuration_lock:
                # A disconnect acknowledged to the user must prevent later writes.
                if set(providers) != {a['id'] for a in self.core.settings.google_accounts}:
                    raise RuntimeError('connections_changed_retry')
                return calendars.push(payload['item_id'], payload.get('version'))
        finally:
            for provider in [*providers.values(), *([feishu] if feishu else [])]:
                provider.http.close()

    def send_email(self, document, key):
        with self.core.configuration_lock:
            account = next((a for a in self.core.settings.google_accounts if a['id'] == self.core.settings.notifications.get('google_account')), None)
            if not account:
                raise ValueError('Email sender is not configured')
            provider = Google(SecretStore(), account['secret_ref'], self.core.settings.google_proxy, network_mode=self.core.settings.google_network_mode)
            try:
                return provider.send_mail(document, key, self.core.settings.notifications.get('email_recipients', []))
            finally:
                provider.http.close()

    def send_feishu(self, document, key):
        with self.core.configuration_lock:
            if not self.core.settings.feishu.get('secret_ref') or not self.core.settings.feishu.get('chat_id'):
                raise ValueError('Feishu sender is not configured')
            provider = Feishu(SecretStore(), self.core.settings.feishu)
            try:
                return provider.send_message(document, key)
            finally:
                provider.http.close()

    def acquire(self):
        now = time.time()
        with self.core.db.transaction() as db:
            row = db.execute("SELECT * FROM locks WHERE name='active-worker'").fetchone()
            if row and row["owner"] != self.owner and row["expires_at"] > now:
                return False
            db.execute("INSERT OR REPLACE INTO locks VALUES('active-worker',?,?)", (self.owner, now + self.core.settings.lease_seconds))
            return True

    def release(self):
        with self.core.db.transaction() as db:
            db.execute("DELETE FROM locks WHERE name='active-worker' AND owner=?", (self.owner,))

    def heartbeat(self):
        with self.core.db.transaction() as db:
            result = db.execute("UPDATE locks SET expires_at=? WHERE name='active-worker' AND owner=?", (time.time() + self.core.settings.lease_seconds, self.owner))
            if result.rowcount != 1:
                return False
            db.execute("INSERT OR REPLACE INTO meta VALUES('heartbeat',?)", (dumps(utcnow()),))
            db.execute("UPDATE jobs SET lease_until=? WHERE status='running' AND lease_token LIKE ?", (time.time() + self.core.settings.lease_seconds, self.owner + ":%"))
        return True

    def claim(self):
        now = time.time()
        with self.core.db.transaction() as db:
            db.execute("UPDATE jobs SET status='failed',lease_until=NULL,lease_token=NULL,error='interrupted' WHERE status='running' AND lease_until<?", (now,))
            row = db.execute("SELECT * FROM jobs WHERE status IN ('queued','failed') AND due_at<=? ORDER BY due_at LIMIT 1", (now,)).fetchone()
            if not row:
                return None
            lease = self.owner + ":" + identifier("lease")
            db.execute("UPDATE jobs SET status='running',attempts=attempts+1,lease_until=?,lease_token=? WHERE id=?", (now + self.core.settings.lease_seconds, lease, row["id"]))
            return {**dict(row), "lease_token": lease, "payload": json.loads(row["payload"])}

    def execute(self, job):
        try:
            if job["kind"] not in self.handlers:
                raise ValueError("unknown_job_kind")
            result = self.handlers[job["kind"]](job["payload"])
            if has_failure(result):
                with self.core.db.transaction() as db:
                    db.execute("UPDATE jobs SET status='failed',result=?,error='partial_failure',due_at=?,lease_until=NULL,lease_token=NULL WHERE id=? AND lease_token=?", (dumps(result), time.time() + min(3600, 30 * 2 ** min(job["attempts"], 7)), job["id"], job["lease_token"]))
                self.core.source_health("job:" + job["kind"], False, "partial_failure")
                return
            state = "preview" if isinstance(result, dict) and result.get("shadow") else "done"
            with self.core.db.transaction() as db:
                db.execute("UPDATE jobs SET status=?,result=?,error=NULL,lease_until=NULL,lease_token=NULL WHERE id=? AND lease_token=?", (state, dumps(result), job["id"], job["lease_token"]))
            self.core.source_health("job:" + job["kind"], True)
        except Exception as exc:
            error = type(exc).__name__
            with self.core.db.transaction() as db:
                db.execute("UPDATE jobs SET status='failed',error=?,due_at=?,lease_until=NULL,lease_token=NULL WHERE id=? AND lease_token=?", (error, time.time() + min(3600, 30 * 2 ** min(job["attempts"], 7)), job["id"], job["lease_token"]))
            self.core.source_health("job:" + job["kind"], False, error)

    def collect(self, providers, accounts=None):
        result = {"local": collect_local(self.core)}
        if getattr(self.core, 'stop_event', None) and self.core.stop_event.is_set():
            raise RuntimeError('source_collection_cancelled')
        if self.core.settings.moodle.get("enabled"):
            try:
                result["moodle"] = collect_moodle(self.core)
            except Exception as exc:
                self.core.source_health("moodle", False, type(exc).__name__)
                result["moodle"] = {"error": type(exc).__name__}
        for account in self.core.settings.google_accounts if accounts is None else accounts:
            if getattr(self.core, 'stop_event', None) and self.core.stop_event.is_set():
                raise RuntimeError('source_collection_cancelled')
            if not account.get("gmail", True):
                continue
            try:
                result[account["id"]] = collect_gmail(self.core, account, providers[account["id"]])
            except Exception as exc:
                self.core.source_health("gmail:" + account["id"], False, type(exc).__name__)
                result[account["id"]] = {"error": type(exc).__name__}
        return result

    def sync_calendars(self, calendars):
        result = {"google": calendars.pull()}
        try:
            result["feishu"] = calendars.mirror()
        except Exception as exc:
            self.core.source_health("calendar:feishu_mirror", False, type(exc).__name__)
            raise
        return result

    def periodic(self):
        now = time.time()
        for kind in ("sources_sync", "calendar_sync"):
            if kind in self.handlers:
                bucket = int(now // self.core.settings.sync_interval_seconds)
                # Keep just one pending periodic job of each kind during a long outage.
                with self.core.db.transaction() as db:
                    existing = db.execute("SELECT 1 FROM jobs WHERE kind=? AND status IN ('queued','running','failed')", (kind,)).fetchone()
                    if not existing:
                        self.core._enqueue(db, kind, {}, f"periodic:{kind}:{bucket}")

    def notification_periodic(self):
        # Notification generation scans current facts once per minute; delivery remains responsive.
        now = time.time()
        minute = int(now // 60)
        if self.core.db.get_meta("notice-minute") == minute:
            return
        self.notifications.generate()
        local_now = datetime.now(ZoneInfo(self.core.settings.timezone))
        if local_now.hour >= 9:
            active = [i for i in self.core.list_items() if formal(i) and i["status"] not in {"completed", "submitted", "cancelled"}]
            document = {"title": "Academic OS 今日简报", "body": "\n".join(f"• {i['title']}｜截止 {i['source_due_at'] or '待公布'}｜安排 {i['scheduled_start'] or '未安排'}" for i in active[:30]) or "暂无待处理事项。"}
            key = "daily:" + local_now.date().isoformat()
            self.notifications.queue("workbench", document, key + ":workbench")
            if self.core.settings.feishu.get("chat_id"):
                self.notifications.queue("feishu", document, key + ":feishu")
            if local_now.weekday() == 0:
                week = local_now.isocalendar()
                document = {**document, "title": "Academic OS 本周简报"}
                key = f"weekly:{week.year}:{week.week}"
                self.notifications.queue("workbench", document, key + ":workbench")
                if self.core.settings.feishu.get("chat_id"):
                    self.notifications.queue("feishu", document, key + ":feishu")
        self.core.db.set_meta("notice-minute", minute)

    def run(self, stop: threading.Event):
        if not self.acquire():
            raise RuntimeError("Another Academic OS worker is already active")
        self.core.stop_event = stop
        failures = queue.SimpleQueue()

        def supervise(name, function):
            try:
                function()
                if not stop.is_set():
                    failures.put(RuntimeError(f'{name} stopped unexpectedly'))
                    stop.set()
            except BaseException as exc:
                failures.put(RuntimeError(f'{name} failed: {type(exc).__name__}'))
                stop.set()

        def beat():
            while not stop.is_set():
                if not self.heartbeat():
                    raise RuntimeError('Worker lease lost')
                stop.wait(10)

        def notify():
            while not stop.is_set():
                self.notification_periodic()
                for _ in range(10):
                    if stop.is_set() or not self.notifications.dispatch_one():
                        break
                stop.wait(1)

        heartbeat = threading.Thread(target=supervise, args=('heartbeat', beat), name="aos-heartbeat")
        notifier = threading.Thread(target=supervise, args=('notifier', notify), name='aos-notifier')
        heartbeat.start()
        notifier.start()
        try:
            while not stop.is_set():
                self.periodic()
                if stop.is_set():
                    break
                job = self.claim()
                if job:
                    self.execute(job)
                write_summary(self.core)
                stop.wait(2)
        finally:
            stop.set()
            # Ownership is retained until BOTH background users of the backend have exited.
            # Provider calls are bounded, and source collectors observe this same stop event.
            heartbeat.join()
            notifier.join()
            self.release()
        if not failures.empty():
            raise failures.get()
