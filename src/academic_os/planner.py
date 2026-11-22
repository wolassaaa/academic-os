from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from .mail_triage import formal


def parsed(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Busy intervals must have timezone offsets")
    return dt.astimezone(timezone.utc)


def generate_plan(items, busy, settings, now=None, days=7):
    if not 1 <= days <= 30:
        raise ValueError("Plan horizon must be 1–30 days")
    now = now or datetime.now(timezone.utc)
    tz = ZoneInfo(settings.timezone)
    local_now = now.astimezone(tz)
    occupied = [(parsed(b["start"]), parsed(b["end"])) for b in busy]
    occupied.extend((parsed(i["scheduled_start"]), parsed(i["scheduled_end"])) for i in items if i.get("scheduled_start") and i.get("scheduled_end") and i["status"] != "cancelled")
    candidates = [i for i in items if formal(i) and i["status"] in {"pending", "in_progress", "unknown"} and not i.get("scheduled_start") and not i.get("calendar_deleted")]
    candidates.sort(key=lambda i: (-i["priority"], i.get("source_due_at") or "9999", -(i.get("weight_pct") or 0), i["id"]))
    changes, unplaced = [], []
    for item in candidates:
        if item["review_state"] in {"needs_analysis", "conflict"}:
            unplaced.append({"item_id": item["id"], "reason": "needs_review"})
            continue
        minutes = item.get("estimate_minutes")
        if not minutes:
            unplaced.append({"item_id": item["id"], "reason": "missing_estimate"})
            continue
        duration = timedelta(minutes=minutes)
        due = parsed(item["source_due_at"]) if item.get("source_due_at") else None
        slot = None
        for offset in range(days):
            day = local_now.date() + timedelta(days=offset)
            if day.weekday() not in settings.availability["weekdays"]:
                continue
            start = datetime.fromisoformat(f"{day}T{settings.availability['start']}").replace(tzinfo=tz).astimezone(timezone.utc)
            close = datetime.fromisoformat(f"{day}T{settings.availability['end']}").replace(tzinfo=tz).astimezone(timezone.utc)
            start = max(start, now.astimezone(timezone.utc))
            # Round forward to a 15-minute boundary.
            delta = (-int(start.timestamp())) % 900
            if start.microsecond:
                delta = delta or 900
            start = (start + timedelta(seconds=delta)).replace(microsecond=0)
            limit = min(close, due) if due else close
            while start + duration <= limit:
                end = start + duration
                overlap = next(((a, b) for a, b in sorted(occupied) if start < b and end > a), None)
                if overlap:
                    start = overlap[1]
                    continue
                slot = (start, end)
                break
            if slot:
                break
        if slot:
            occupied.append(slot)
            changes.append({"item_id": item["id"], "expected_version": item["version"], "start": slot[0].isoformat(), "end": slot[1].isoformat()})
        else:
            unplaced.append({"item_id": item["id"], "reason": "insufficient_time", "minutes": minutes})
    return {"changes": changes, "unplaced": unplaced, "generated_at": now.isoformat(), "model_enabled": False}
