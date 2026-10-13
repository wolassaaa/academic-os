"""Open school SSO directly, independently of the user's proxy/browser profile."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit


def open_school_login(url: str, data_dir: Path):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('学校登录必须使用 HTTPS 地址。')
    if sys.platform != 'win32':
        raise ValueError('学校直连登录窗口需要 Windows 版。')
    roots = [Path(value) for key in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA') if (value := os.environ.get(key))]
    browser = next((root / relative for relative in (
        'Microsoft/Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe'
    ) for root in roots if (root / relative).is_file()), None)
    if browser is None:
        raise ValueError('未找到 Microsoft Edge 或 Chrome，无法打开学校直连登录窗口。')
    identity = hashlib.sha256(str(Path(data_dir).resolve()).casefold().encode()).hexdigest()[:16]
    profile = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'AcademicOS' / 'SchoolBrowser' / identity
    profile.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen([
            str(browser), '--user-data-dir=' + str(profile), '--no-proxy-server',
            '--no-first-run', '--no-default-browser-check', '--new-window', url,
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        raise ValueError('学校登录窗口未能启动，请重试。') from None
