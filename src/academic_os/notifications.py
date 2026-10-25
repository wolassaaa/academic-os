from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone

from .core import Conflict, identifier, utcnow
from .db import dumps
from . import personal_status
from .mail_triage import formal, review_revision


class Notifications:
    def __init__(self, core, senders=None):
        self.core, self.senders = core, senders or {}

    def queue(self, channel, document, key, item_id=None):
        if channel not in {"workbench", "feishu", "email"}:
            raise ValueError("Invalid notification channel")
        with self.core.db.transaction() as db:
            if item_id and "source_due_at" in document:
                current = self.core._get(db, item_id)
                if not formal(current) or review_revision(current) != document.get('mail_review_revision', 0) or current["source_due_at"] != document["source_due_at"] or current.get("deadline_revision", 1) != document.get("deadline_revision", 1) or current["status"] in {"completed", "submitted", "cancelled"}:
                    return None
            previous = db.execute("SELECT id FROM notifications WHERE unique_key=?", (key,)).fetchone()
            if previous:
                return previous[0]
            nid = identifier("notice")
            db.execute("INSERT INTO notifications(id,item_id,channel,state,document,due_at,unique_key,created_at) VALUES(?,?,?,?,?,?,?,?)", (nid, item_id, channel, "delivered" if channel == "workbench" else "queued", dumps(document), time.time(), key, utcnow()))
            return nid

    def list(self):
        with self.core.db.connect() as db:
            return [{**dict(r), "document": json.loads(r["document"])} for r in db.execute("SELECT * FROM notifications ORDER BY created_at DESC LIMIT 300")]

    def dispatch_one(self, now=None):
        now = time.time() if now is None else now
        lease = identifier("lease")
        with self.core.db.transaction() as db:
            db.execute("UPDATE notifications SET state='failed',lease_token=NULL,lease_until=NULL WHERE state='sending' AND lease_until<?", (now,))
            row = db.execute("SELECT * FROM notifications WHERE state IN ('queued','failed','snoozed') AND due_at<=? ORDER BY due_at LIMIT 1", (now,)).fetchone()
            if not row:
                return None
            if row["item_id"]:
                item = self.core._get(db, row["item_id"])
                document = json.loads(row["document"])
                if "source_due_at" in document and (not formal(item) or review_revision(item) != document.get('mail_review_revision', 0) or item["source_due_at"] != document["source_due_at"] or item.get("deadline_revision", 1) != document.get("deadline_revision", 1)):
                    db.execute("UPDATE notifications SET state='superseded' WHERE id=?", (row["id"],))
                    return row["id"]
                if item["status"] in {"completed", "submitted", "cancelled"}:
                    db.execute("UPDATE notifications SET state='completed' WHERE id=?", (row["id"],))
                    return row["id"]
            if row["channel"] == "workbench":
                db.execute("UPDATE notifications SET state='delivered',last_error=NULL WHERE id=?", (row["id"],))
                return row["id"]
            if self.core.settings.mode == "shadow":
                db.execute("UPDATE notifications SET state='preview' WHERE id=?", (row["id"],))
                return row["id"]
            db.execute("UPDATE notifications SET state='sending',attempts=attempts+1,lease_until=?,lease_token=? WHERE id=?", (now + 90, lease, row["id"]))
        document = json.loads(row["document"])
        try:
            sender = self.senders.get(row["channel"])
            if not sender:
                raise RuntimeError("channel_not_configured")
            receipt = sender(document, row["id"])
            if not receipt:
                raise RuntimeError("missing_channel_receipt")
            with self.core.db.transaction() as db:
                db.execute("UPDATE notifications SET state='delivered',receipt=?,lease_token=NULL,lease_until=NULL,last_error=NULL WHERE id=? AND lease_token=? AND state='sending'", (str(receipt), row["id"], lease))
            self.core.source_health("notify:" + row["channel"], True)
        except Exception as exc:
            # Never log provider exception bodies; they can contain tokens or private mail.
            error = type(exc).__name__
            delay = min(3600, 30 * 2 ** min(row["attempts"], 7))
            with self.core.db.transaction() as db:
                db.execute("UPDATE notifications SET state='failed',last_error=?,due_at=?,lease_token=NULL,lease_until=NULL WHERE id=? AND lease_token=? AND state='sending'", (error, now + delay, row["id"], lease))
            self.core.source_health("notify:" + row["channel"], False, error)
            if row["channel"] == "feishu" and row["attempts"] >= 2 and self.core.settings.notifications.get("email_on_failure", True) and self.core.settings.notifications.get("email_recipients"):
                self.queue("email", {**document, "title": "飞书投递失败 · " + document["title"]}, "fallback:" + row["unique_key"], row["item_id"])
        return row["id"]

    def action(self, notification_id, action, minutes=60, actor="user"):
        states = {"acknowledge": "acknowledged", "complete": "completed", "snooze": "snoozed"}
        if action not in states or not 1 <= minutes <= 10080:
            raise ValueError("Invalid notification action or snooze duration")
        with self.core.db.transaction() as db:
            row = db.execute("SELECT * FROM notifications WHERE id=?", (notification_id,)).fetchone()
            if not row:
                raise KeyError(notification_id)
            if row['item_id'] and action in {'snooze', 'complete'} and not formal(self.core._get(db, row['item_id'])):
                raise Conflict('mail_candidate_requires_review')
            # A user action wins over an in-flight network delivery update.
            db.execute("UPDATE notifications SET state=?,due_at=?,lease_token=NULL,lease_until=NULL WHERE id=?", (states[action], time.time() + minutes * 60, notification_id))
            group = json.loads(row["document"]).get("group_key")
            if group and action in {"acknowledge", "snooze"}:
                db.execute("UPDATE notifications SET state=?,due_at=?,lease_token=NULL,lease_until=NULL WHERE json_extract(document,'$.group_key')=? AND state NOT IN ('completed','superseded')", (states[action], time.time() + minutes * 60, group))
            if row["item_id"] and action == "complete":
                item = self.core._get(db, row["item_id"])
                item.update(status="completed", version=item["version"] + 1, updated_at=utcnow())
                personal_status.record(item, "completed", actor, item["updated_at"])
                self.core._put(db, item)
                db.execute("UPDATE notifications SET state='completed' WHERE item_id=?", (row["item_id"],))
                self.core._enqueue(db, "calendar_push", {"item_id": item["id"], "version": item["version"]}, f"calendar:{item['id']}:{item['version']}")
            self.core.audit(db, actor, "notification." + action, notification_id, {})
        return {"id": notification_id, "state": states[action]}

    def health_alerts(self):
        """Push token trouble once per incident (and once on recovery) instead of leaving it inside health()."""
        settings = self.core.settings
        health = self.core.health()
        channels = ['workbench'] + (['feishu'] if settings.feishu.get('chat_id') else []) + (['email'] if settings.notifications.get('email_recipients') else [])
        for agent in health['consumers']:
            name, open_incident = agent['name'], self.core.db.get_meta('agent-incident:' + agent['name'])
            if agent['state'] in {'stale', 'stuck'}:
                kind = 'agent-' + agent['state']
                incident = f"{kind}:{agent['last_polled_at'] if agent['state'] == 'stale' else agent['cursor']}"
                if agent['state'] == 'stale':
                    document = {'title': f'{name.capitalize()} 已停止读取新邮件', 'body': f"最近一次读取：{agent['last_polled_at']}（超过 {agent['stale_after_minutes']} 分钟）。未读积压 {agent['backlog']} 封。\n请检查 Hermes 是否运行、MCP 网关/Tailscale 是否连通、令牌是否被撤销。", 'kind': 'agent'}
                else:
                    document = {'title': f'{name.capitalize()} 读取了邮件但一直没有确认', 'body': f"积压 {agent['backlog']} 封，最早一封采集于 {agent['oldest_unacked_collected_at']}。\nHermes 可能在反馈给你之前失败了；它会重复读取同一页，直到成功确认。", 'kind': 'agent'}
                for channel in channels:
                    self.queue(channel, document, f'{kind}:{channel}:{name}:{incident}')
                self.core.db.set_meta('agent-incident:' + name, incident)
            elif open_incident and agent['state'] == 'ok':
                for channel in channels:
                    self.queue(channel, {'title': f'{name.capitalize()} 已恢复读取', 'body': f"积压 {agent['backlog']} 封。", 'kind': 'agent'}, f'agent-recovered:{channel}:{name}:{open_incident}')
                self.core.db.set_meta('agent-incident:' + name, None)
        for state in health['authorizations']:
            aid, label = state['account_id'], state.get('email') or state['account_id']
            # Never route a token alert through the very mail account that just lost authorization.
            channels = ['workbench'] + (['feishu'] if settings.feishu.get('chat_id') else [])
            if settings.notifications.get('email_recipients') and settings.notifications.get('google_account') not in {None, aid}:
                channels.append('email')
            open_incident = self.core.db.get_meta('auth-incident:' + aid)
            # Alert on transitions only: core keeps an opened incident in reauthorization_required until a real success.
            if state['state'] == 'reauthorization_required' and not open_incident:
                document = {'title': f'需要重新授权 · {label}', 'body': f"Google 授权已失效（{', '.join(state['failing_sources'])}），邮件/日历已停止更新。\n请在 Academic OS 设置 → 账号连接中点击“修复授权”。" + ('\n当前 OAuth 应用仍为 Testing 状态，授权约 7 天失效；发布到 In production 后才能长期有效。' if state['publishing'] == 'testing' else ''), 'kind': 'auth'}
                for channel in channels:
                    self.queue(channel, document, f"auth-required:{channel}:{aid}:{state['incident']}")
                self.core.db.set_meta('auth-incident:' + aid, {'incident': state['incident'], 'opened_at': utcnow(), 'sources': state['failing_sources']})
            elif state['state'] != 'reauthorization_required' and open_incident:
                document = {'title': f'授权已恢复 · {label}', 'body': '邮件/日历采集已恢复。', 'kind': 'auth'}
                incident = open_incident.get('incident') if isinstance(open_incident, dict) else open_incident
                for channel in channels:
                    self.queue(channel, document, f'auth-recovered:{channel}:{aid}:{incident}')
                self.core.db.set_meta('auth-incident:' + aid, None)
            if state['state'] == 'expiring':
                document = {'title': f'Google 授权即将到期 · {label}', 'body': f"OAuth 应用为 Testing 状态，授权约 7 天失效，预计 {state['expires_estimate']} 到期。\n请提前在设置中“修复授权”，或将应用发布到 In production。", 'kind': 'auth'}
                for channel in channels:
                    self.queue(channel, document, f"auth-expiring:{channel}:{aid}:{state['authorized_at']}")

    def generate(self, now=None):
        now = now or datetime.now(timezone.utc)
        levels = [(168, "7d"), (120, "5d"), (72, "3d"), (24, "1d"), (3, "3h"), (0, "due")]
        count = 0
        for item in self.core.list_items():
            if not formal(item) or item["status"] in {"completed", "submitted", "cancelled"} or not item["source_due_at"]:
                continue
            hours = (datetime.fromisoformat(item["source_due_at"]) - now).total_seconds() / 3600
            if hours > 168:
                continue
            hit = [level for threshold, level in levels if hours <= threshold]
            if not hit:
                continue
            level = hit[-1] if hours >= 0 else "overdue:" + now.date().isoformat()
            revision = item.get("deadline_revision", 1)
            mail_revision = review_revision(item)
            suffix = f':mail:{mail_revision}' if mail_revision else ''
            key = hashlib.sha256(f"{item['id']}:{item['source_due_at']}:{revision}:{level}{suffix}".encode()).hexdigest()
            document = {"title": "学业提醒 · " + item["title"], "body": f"截止：{item['source_due_at']}\n状态：{item['status']}\n剩余：{hours:.1f} 小时\n请在工作台确认收到、稍后提醒或完成。", "level": level, "group_key": key, "source_due_at": item["source_due_at"], "deadline_revision": revision}
            document['mail_review_revision'] = mail_revision
            self.queue("workbench", document, "workbench:" + key, item["id"])
            if self.core.settings.feishu.get("chat_id"):
                self.queue("feishu", document, "feishu:" + key, item["id"])
            if hours <= 24 and self.core.settings.notifications.get("email_recipients"):
                self.queue("email", document, "email:" + key, item["id"])
            count += 1
        return {"considered": count}
