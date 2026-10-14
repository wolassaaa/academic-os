"""Read-only mail search; date boundaries follow the configured workspace timezone."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from .mail_triage import linked_items, review_counts, review_state


def search_mail(core, *, limit=50, offset=0, q=None, account=None,
                date_mode="all", start=None, end=None, review='all'):
    if not 1 <= limit <= 300 or offset < 0:
        raise ValueError("Mail pagination requires limit 1–300 and offset >= 0")
    if date_mode not in {"all", "today", "next7", "custom", "undated"}:
        raise ValueError("Unknown mail date filter")
    clauses, parameters = [], []
    if review not in {'all', 'pending', 'ignored'}:
        raise ValueError('Unknown mail review filter')
    if review != 'all':
        clauses.append("EXISTS (SELECT 1 FROM items i,json_each(i.document,'$.mail_keys') k "
                       "WHERE k.value=mail.id AND mail_review_state(i.document)=?)")
        parameters.append(review)
    if q:
        if len(q) > 500:
            raise ValueError("Search query is too long")
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("(" + " OR ".join(
            f"COALESCE(json_extract(document,'$.{field}'),'') LIKE ? ESCAPE '\\'"
            for field in ("subject", "from", "body")) + ")")
        parameters.extend(["%" + escaped + "%"] * 3)
    if account:
        clauses.append("account=?")
        parameters.append(account)
    zone = ZoneInfo(core.settings.timezone)
    if date_mode == "undated":
        clauses.append("julianday(received_at) IS NULL")
    elif date_mode != "all":
        if date_mode == "custom":
            if not start or not end:
                raise ValueError("自定义日期需要开始和结束日期。")
            first, last = date.fromisoformat(start), date.fromisoformat(end)
            if last < first:
                raise ValueError("结束日期必须不早于开始日期。")
            if last == date.max:
                raise ValueError("结束日期超出支持范围。")
        else:
            first = datetime.now(zone).date()
            last = first + timedelta(days=6 if date_mode == "next7" else 0)
        lower = datetime.combine(first, time.min, zone).isoformat()
        upper = datetime.combine(last + timedelta(days=1), time.min, zone).isoformat()
        clauses.extend(["julianday(received_at)>=julianday(?)", "julianday(received_at)<julianday(?)"])
        parameters.extend([lower, upper])
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with core.db.connect() as db:
        db.create_function('mail_review_state', 1, lambda value: review_state(json.loads(value)), deterministic=True)
        # The count, page and account choices share a single read snapshot.
        db.execute("BEGIN")
        total = db.execute("SELECT COUNT(*) FROM mail" + where, parameters).fetchone()[0]
        rows = db.execute("SELECT json_remove(document,'$.body') FROM mail" + where +
                          " ORDER BY julianday(received_at) DESC,id DESC LIMIT ? OFFSET ?",
                          [*parameters, limit, offset])
        items = [json.loads(row[0]) for row in rows]
        for item in items:
            item['review_counts'] = review_counts(linked_items(db, item['key']))
        accounts = [row[0] for row in db.execute("SELECT DISTINCT account FROM mail ORDER BY account")]
    return {"items": items, "total": total, "limit": limit, "offset": offset,
            "accounts": accounts, "timezone": core.settings.timezone}
