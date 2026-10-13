from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Deadline:
    source_id: str
    course: str
    title: str
    kind: str
    due_at: datetime
    url: str | None = None
    source: str = "Moodle action event"
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class StoredDeadline:
    source_id: str
    google_event_id: str
    content_hash: str
    status: str
