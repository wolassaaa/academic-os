from pathlib import Path
import json
import shutil
import sys
import socket
import subprocess
import tempfile

import pytest

from academic_os.config import Settings


def test_startup_port_error_explains_conflict_and_affected_port(tmp_path):
    from academic_os.portable import startup_error_message
    error = OSError(10048, 'Only one usage of each socket address')
    error.aos_port = 8766
    message = startup_error_message(error, Settings(data_dir=tmp_path))
    assert '8766' in message and '端口' in message and '后台' in message
    assert 'OSError' not in message


def test_portable_first_run_has_no_account_or_model_and_preserves_settings(tmp_path):
    from academic_os.portable import prepare
    config = tmp_path / "中文 directory" / "config.json"
    core = prepare(config)
    assert core.settings.mode == "shadow"
    assert core.settings.model["enabled"] is False
    assert core.settings.data_dir == config.parent / "data"
    with core.db.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    settings = Settings.load(config)
    settings.port = 8875
    settings.save(config)
    assert prepare(config).settings.port == 8875
    assert json.loads(config.read_text())["data_dir"] == "data"
    moved = tmp_path / "new computer"
    shutil.copytree(config.parent, moved)
    assert prepare(moved / "config.json").settings.data_dir == moved / "data"


def test_moodle_subprocess_uses_frozen_dispatch_instead_of_python_module(monkeypatch):
    from academic_os.sources import moodle_command
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(Path("C:/Program Files/AcademicOS/AcademicOS.exe")))
    assert moodle_command() == [sys.executable, "--moodle-collector"]
    monkeypatch.setattr(sys, "frozen", False)
    assert moodle_command() == [sys.executable, "-m", "academic_os.moodle_process"]


def test_desktop_controls_reset_password_and_gracefully_stop(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tkinter import simpledialog, messagebox
    from academic_os import portable
    from academic_os.security import Auth
    settings = Settings(data_dir=tmp_path / "data")
    ports = []
    while len(ports) < 2:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port not in ports:
            ports.append(port)
    settings.port, settings.dashboard_port = ports
    config = tmp_path / "config.json"
    settings.save(config)
    core = portable.prepare(config)
    auth = Auth(core.db, settings)
    auth.set_password("owner", "previous-password")
    original_tk = tk.Tk
    opened, notices, errors = [], [], []
    monkeypatch.setattr(portable.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(simpledialog, "askstring", lambda *a, **k: "eight888")
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: notices.append(a))
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: errors.append(a))

    def factory():
        root = original_tk()
        root.withdraw()

        def buttons(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == "TButton":
                    yield child
                yield from buttons(child)

        def exercise(attempt=0):
            controls = {button.cget("text"): button for button in buttons(root)}
            if str(controls["打开工作台"].cget("state")) != "normal" and attempt < 100:
                root.after(100, lambda: exercise(attempt + 1))
                return
            if not opened:
                errors.append("runtime_not_ready")
            else:
                controls["忘记密码 / 重置"].invoke()
            controls["停止并退出"].invoke()

        root.after(300, exercise)
        return root

    monkeypatch.setattr(tk, "Tk", factory)
    assert portable.launch_gui(config) == 0
    assert opened and notices and not errors
    assert auth.login("owner", "eight888", "test", False)
    with core.db.connect() as db:
        assert db.execute("SELECT 1 FROM locks WHERE name='active-worker'").fetchone() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows packaging script")
def test_rebuild_refuses_to_overwrite_existing_user_data():
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell unavailable")
    project = Path(__file__).resolve().parents[1]
    dist = project / "dist"
    dist.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="build-guard-", dir=dist) as directory:
        target = Path(directory)
        marker = target / "AcademicOS" / "data" / "keep.txt"
        marker.parent.mkdir(parents=True)
        marker.write_text("existing user data")
        result = subprocess.run([shell, "-NoProfile", "-File", str(project / "scripts/build-portable.ps1"), "-OutputRoot", str(target), "-Python", "nonexistent-python"], capture_output=True, timeout=30)
        assert result.returncode != 0
        assert b"Refusing to overwrite" in result.stdout + result.stderr
        assert marker.read_text() == "existing user data"
