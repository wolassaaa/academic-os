"""Conservative rule extraction. Dates are evidence candidates, never automatic calendar commands."""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

PATTERN = re.compile(r"(?<!\d)(?P<year>20\d{2})\s*(?:年|[-/])\s*(?P<month>\d{1,2})\s*(?:月|[-/])\s*(?P<day>\d{1,2})(?:日)?(?:[ T]*(?P<hour>\d{1,2}):(?P<minute>\d{2}))?(?!\d)")
ROLES = {"registration": ("报名", "registration", "register"), "submission": ("提交", "材料", "submission", "submit"), "event": ("活动", "讲座", "workshop", "event", "lecture"), "deadline": ("截止", "deadline", "due")}


def extract_date_nodes(text: str, timezone: str, now: datetime | None = None) -> list[dict]:
    nodes = []
    for match in PATTERN.finditer(text):
        try:
            dt = datetime(int(match["year"]), int(match["month"]), int(match["day"]), int(match["hour"] or 0), int(match["minute"] or 0), tzinfo=ZoneInfo(timezone))
        except ValueError:
            continue
        before = re.split(r"[。；;\n]", text[max(0, match.start() - 100):match.start()])[-1].casefold()
        hits = [(before.rfind(word), role) for role, words in ROLES.items() for word in words if word in before]
        # Registration/submission are more specific than generic 'deadline'.
        role = next((r for r in ("registration", "submission", "event") if any(h[1] == r for h in hits)), None)
        role = role or (max(hits)[1] if hits else "unknown")
        node = {"date": dt.date().isoformat(), "due_at": dt.isoformat() if match["hour"] is not None else None,
                "all_day": match["hour"] is None, "timezone": timezone, "role": role,
                "evidence": match.group(), "context": text[max(0, match.start() - 60):match.end() + 80],
                "offset": match.start(), "confidence": "explicit_date"}
        if not any(n["date"] == node["date"] and n["due_at"] == node["due_at"] and n["role"] == role for n in nodes):
            nodes.append(node)
    return nodes


def email_records(message, nodes):
    result = []
    for node in nodes:
        source_id = f"{message['id']}:{node['role']}:{node['date']}:{node.get('due_at') or 'date'}"
        result.append({"source_id": source_id, "title": f"{message.get('subject') or '(无主题)'} · {node['role']}",
                       "course": message.get("course", ""), "kind": "event" if node["role"] == "event" else "task",
                       "due_at": node["due_at"], "review_state": "needs_analysis", "evidence_text": node["context"],
                       "url": "https://mail.google.com/mail/u/0/#all/" + message["id"]})
    return result
