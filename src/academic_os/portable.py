"""Portable Windows entry point; GUI and CLI share the existing runtime/core."""
from __future__ import annotations

import argparse
import logging
import json
import os
from pathlib import Path
import queue
import sys
import threading
import webbrowser

from .config import Settings
from .core import Core
from .db import Database
from .runtime import InstanceLock, Runtime
from .security import Auth


def startup_error_message(error, settings):
    if isinstance(error, OSError):
        code = getattr(error, 'winerror', None) or error.errno
        port = getattr(error, 'aos_port', None)
        if code in {48, 98, 10048, 10013}:
            ports = str(port) if port else f'{settings.port} / {settings.dashboard_port}'
            return f'本机端口 {ports} 无法使用，可能已有后台在运行。请退出另一个 Academic OS 后台后，关闭此窗口并重新打开。'
        if isinstance(error, PermissionError):
            return '无法读写程序数据文件夹。请完整解压到你有写入权限的文件夹后重新打开。'
    if isinstance(error, RuntimeError) and 'already running' in str(error):
        return '此数据文件夹已有后台在运行。请退出另一个 Academic OS 后台后再打开。'
    return f'后台未能启动或已停止（{type(error).__name__}）。请查看 data/desktop.log 中的诊断记录。'


def prepare(config: Path):
    config = config.resolve()
    settings = Settings.load(config) if config.exists() else Settings(data_dir=config.parent / "data")
    with InstanceLock(settings.data_dir):
        core = Core(Database(settings.database_path), settings)
        if not config.exists():
            settings.save(config)
    return core


def existing_instance_url(config):
    """Open only a locked instance that proves possession of its random nonce."""
    import httpx
    try:
        settings = Settings.load(config)
        try:
            with InstanceLock(settings.data_dir):
                return None
        except RuntimeError:
            pass
        metadata = json.loads((settings.data_dir / 'runtime.json').read_text(encoding='utf-8'))
        port, nonce = metadata['port'], metadata['nonce']
        if not isinstance(port, int) or not 1024 <= port <= 65535 or not isinstance(nonce, str):
            return None
        url = f'http://127.0.0.1:{port}'
        response = httpx.get(url + '/api/v1/instance', headers={'X-AOS-Instance': nonce}, trust_env=False, timeout=.5)
        return url if response.status_code == 200 and response.json() == {'ok': True} else None
    except (OSError, ValueError, KeyError, httpx.HTTPError):
        return None


def launch_gui(config: Path):
    existing = existing_instance_url(config)
    if existing:
        webbrowser.open(existing)
        return 0
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk

    root = tk.Tk()
    root.title("Academic OS 2.0")
    root.geometry("510x330")
    root.resizable(False, False)
    root.configure(bg="#f5f4eb")
    frame = ttk.Frame(root, padding=26)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Academic OS 2.0", font=("Segoe UI", 21)).pack(anchor="w")
    ttk.Label(frame, text="本机后台 · 浏览器工作台", font=("Microsoft YaHei", 11)).pack(anchor="w", pady=(8, 18))
    status = tk.StringVar(value="正在启动……")
    ttk.Label(frame, textvariable=status, wraplength=450).pack(anchor="w", pady=(0, 18))
    try:
        core = prepare(config)
    except Exception as exc:
        existing = existing_instance_url(config)
        if existing:
            webbrowser.open(existing)
            root.destroy()
            return 0
        messagebox.showerror("无法启动", f"请检查文件夹是否可写、已有后台是否运行。\n错误类型：{type(exc).__name__}", parent=root)
        root.destroy()
        return 1

    log_path = core.settings.data_dir / "desktop.log"
    if getattr(sys, "frozen", False):
        # Keep collector/CLI stdio intact; only the interactive GUI detaches its console.
        sys.stdout = sys.stderr = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stdin = open(os.devnull, "r")
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.FreeConsole()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    runtime = Runtime(core, config, allow_port_fallback=True)
    stop = threading.Event()
    errors = queue.SimpleQueue()

    def run():
        try:
            runtime.run(stop)
        except BaseException as exc:
            errors.put(startup_error_message(exc, core.settings))
            logging.error("Desktop runtime stopped: type=%s errno=%s winerror=%s port=%s", type(exc).__name__, getattr(exc, 'errno', None), getattr(exc, 'winerror', None), getattr(exc, 'aos_port', None))

    thread = threading.Thread(target=run, name="aos-desktop-runtime")
    thread.start()
    url = f"http://127.0.0.1:{core.settings.port}"
    opened = closing = reported = False

    def reset_password():
        password = simpledialog.askstring("忘记密码", "输入新密码（至少 8 位，不需要旧密码）：", show="*", parent=root)
        if password is None:
            return
        repeat = simpledialog.askstring("确认新密码", "再次输入新密码：", show="*", parent=root)
        if repeat is None:
            return
        if password != repeat:
            messagebox.showerror("未重置", "两次输入不一致。", parent=root)
            return
        try:
            username = Auth(core.db, core.settings).reset_password(None, password)
        except ValueError:
            messagebox.showerror("未重置", "密码需至少 8 位，且须已有唯一账号。首次使用请先在浏览器创建账号。", parent=root)
        else:
            messagebox.showinfo("重置成功", f"账号 {username} 已设置新密码，本地和远程浏览器需重新登录。", parent=root)

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x")
    open_button = ttk.Button(buttons, text="打开工作台", command=lambda: webbrowser.open(url), state="disabled")
    open_button.pack(side="left", padx=(0, 12))
    reset_button = ttk.Button(buttons, text="忘记密码 / 重置", command=reset_password, state="disabled")
    reset_button.pack(side="left")

    def close():
        nonlocal closing
        closing = True
        stop.set()
        status.set("正在安全停止后台，请稍候……")
        open_button.configure(state="disabled")
        reset_button.configure(state="disabled")

    ttk.Button(frame, text="停止并退出", command=close).pack(anchor="w", pady=(18, 10))
    ttk.Label(frame, text="关闭浏览器后后台继续运行；退出此窗口会停止后台。\n首次使用在浏览器设置账号。无需安装 Python。", wraplength=450).pack(anchor="w")
    root.protocol("WM_DELETE_WINDOW", close)

    def poll():
        nonlocal opened, reported, url
        if closing and not thread.is_alive():
            root.destroy()
            return
        if not errors.empty() and not reported:
            reported = True
            status.set(errors.get())
            open_button.configure(state="disabled")
            reset_button.configure(state="disabled")
        if not opened and not reported and not closing and len(runtime.servers) == 2 and all(server.started for server in runtime.servers):
            url = f'http://127.0.0.1:{core.settings.port}'
            opened = True
            status.set(f"后台运行中 · {core.settings.mode}\n{url}")
            open_button.configure(state="normal")
            reset_button.configure(state="normal")
            webbrowser.open(url)
        root.after(200, poll)

    root.after(200, poll)
    root.mainloop()
    stop.set()
    thread.join(timeout=180)
    return 1 if reported else 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ['--moodle-callback']:
        # OS callback contains a temporary token: never print argv or response bodies.
        if len(args) != 4 or args[2] != '--config':
            return 1
        from .moodle_callback import complete_callback
        return complete_callback(args[1], args[3])
    if args[:1] == ["--moodle-collector"]:
        from .moodle_process import main as collect
        collect()
        return 0
    if args[:1] == ["--diagnose"]:
        from .portable_check import diagnose
        return diagnose()
    if args[:1] == ["--cli"]:
        from .cli import main as cli
        return cli(args[1:])
    if args[:1] == ["--mcp"]:
        from .mcp import main as mcp
        return mcp(args[1:])
    parser = argparse.ArgumentParser(description="Academic OS desktop launcher")
    default_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
    parser.add_argument("--config", type=Path, default=default_root / "config.json")
    args = parser.parse_args(args)
    return launch_gui(args.config.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
