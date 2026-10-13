from __future__ import annotations

import json
import os
import tempfile
import time as clock
from dataclasses import asdict, dataclass, field
from pathlib import Path
from datetime import time
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path("data").resolve())
    timezone: str = "Asia/Hong_Kong"
    mode: str = "shadow"
    host: str = "127.0.0.1"
    port: int = 8765
    dashboard_port: int = 8766
    secure_cookies: bool = True
    session_hours: int = 12
    sync_interval_seconds: int = 300
    lease_seconds: int = 1200
    google_proxy: str | None = None
    google_network_mode: str = 'auto'
    google_accounts: list[dict] = field(default_factory=list)
    feishu: dict = field(default_factory=dict)
    notifications: dict = field(default_factory=lambda: {"email_recipients": [], "email_on_failure": True})
    moodle: dict = field(default_factory=dict)
    local_sources: dict = field(default_factory=dict)
    availability: dict = field(default_factory=lambda: {"weekdays": [0, 1, 2, 3, 4, 5, 6], "start": "09:00", "end": "21:00", "block_minutes": 60})
    model: dict = field(default_factory=lambda: {"enabled": False, "provider": None, "timeout_seconds": 30})

    def __post_init__(self):
        self.data_dir = Path(self.data_dir).resolve()
        ZoneInfo(self.timezone)
        if self.mode not in {"shadow", "live"}:
            raise ValueError("mode must be shadow or live")
        if self.host != "127.0.0.1":
            raise ValueError("Management and dashboard must bind to 127.0.0.1; use the HTTPS gateway")
        if self.model.get("enabled"):
            raise ValueError("Model analysis is disabled in this release; see docs/MODEL-CONTRACT.md")
        if self.sync_interval_seconds < 30 or self.lease_seconds < 30:
            raise ValueError("intervals must be at least 30 seconds")
        if not (1024 <= self.port <= 65535 and 1024 <= self.dashboard_port <= 65535) or self.port == self.dashboard_port:
            raise ValueError("Use distinct ports in 1024–65535")
        if not 1 <= self.session_hours <= 168:
            raise ValueError("Session lifetime must be 1–168 hours")
        if time.fromisoformat(self.availability["start"]) >= time.fromisoformat(self.availability["end"]):
            raise ValueError("Availability must start before it ends on the same day")
        if not set(self.availability["weekdays"]) <= set(range(7)):
            raise ValueError("Weekdays must be 0–6")
        account_ids = [account["id"] for account in self.google_accounts]
        if len(account_ids) != len(set(account_ids)):
            raise ValueError("Google account IDs must be unique")
        if sum(bool(c.get("write")) for a in self.google_accounts for c in a.get("calendars", [])) > 1:
            raise ValueError("Select exactly one system-managed writable Google calendar")
        for account in self.google_accounts:
            if not account.get("secret_ref"):
                raise ValueError("Google accounts require a Credential Manager reference")
            ids = [c["id"] for c in account.get("calendars", [])]
            if len(ids) != len(set(ids)):
                raise ValueError("Calendar IDs must be unique per account")
        if self.google_proxy:
            proxy = urlparse(self.google_proxy)
            if proxy.scheme not in {"http", "https", "socks5", "socks5h"} or not proxy.hostname or proxy.username or proxy.password:
                raise ValueError("Proxy must be a URL without inline credentials")
        if self.google_network_mode not in {'auto', 'manual', 'direct'}:
            raise ValueError('Google network mode must be auto, manual or direct')
        if self.google_network_mode == 'manual' and not self.google_proxy:
            raise ValueError('Manual network mode requires a proxy URL')
        def check_secret(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"access_token", "refresh_token", "client_secret", "app_secret", "password", "api_key", "token"}:
                        raise ValueError("Store credentials in Credential Manager, not config")
                    check_secret(child)
            elif isinstance(value, list):
                for child in value:
                    check_secret(child)
        check_secret([self.google_accounts, self.feishu, self.moodle, self.model])

    @property
    def database_path(self) -> Path:
        return self.data_dir / "state.sqlite3"

    @classmethod
    def load(cls, path: str | Path) -> "Settings":
        path = Path(path).resolve()
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault('google_network_mode', 'manual' if data.get('google_proxy') else 'auto')
        data["data_dir"] = (path.parent / data.get("data_dir", "data")).resolve()
        return cls(**data)

    def save(self, path: str | Path):
        data = asdict(self)
        path = Path(path).resolve()
        data["data_dir"] = str(self.data_dir.relative_to(path.parent)) if self.data_dir.is_relative_to(path.parent) else str(self.data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            stream.write(json.dumps(data, ensure_ascii=False, indent=2))
            temporary = Path(stream.name)
        try:
            for attempt in range(10):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    clock.sleep(.02 * (attempt + 1))
        finally:
            temporary.unlink(missing_ok=True)
