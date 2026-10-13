"""One process owns both loopback applications and the durable worker."""
from __future__ import annotations

import logging
import json
import os
import secrets
import queue
import socket
import threading
import time
from pathlib import Path

import uvicorn

from .api import create_app
from .worker import Worker

log = logging.getLogger(__name__)
_unfinished_runtimes = []


class InstanceLock:
    """OS lock survives stale lock files and is released by process termination."""
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'runtime.lock'
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise RuntimeError('Academic OS is already running or being maintained for this data directory') from exc
        return self

    def __exit__(self, *args):
        if self.file:
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.file = None


class Runtime:
    def __init__(self, core, config_path=None, allow_port_fallback=False):
        self.core, self.config_path = core, config_path
        self.lock = InstanceLock(core.settings.data_dir)
        self.servers = []
        self.threads = []
        self.allow_port_fallback = allow_port_fallback

    def run(self, stop: threading.Event, startup_timeout=20):
        self.lock.__enter__()
        sockets = []
        errors = queue.Queue()
        failure = None
        try:
            # Bind BOTH ports before starting the worker or accepting requests.
            for attribute in ('port', 'dashboard_port'):
                port = getattr(self.core.settings, attribute)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sockets.append(sock)
                if os.name == 'nt':
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                try:
                    sock.bind(('127.0.0.1', port))
                except OSError as exc:
                    if not self.allow_port_fallback or (getattr(exc, 'winerror', None) or exc.errno) not in {13, 48, 98, 10048, 10013}:
                        exc.aos_port = port
                        raise
                    sock.bind(('127.0.0.1', 0))
                setattr(self.core.settings, attribute, sock.getsockname()[1])
                sock.listen(128)
                sock.setblocking(False)
            if self.allow_port_fallback and self.config_path:
                self.core.settings.save(self.config_path)
            self.core._instance_nonce = secrets.token_urlsafe(32)
            metadata = self.core.settings.data_dir / 'runtime.json'
            temporary = metadata.with_suffix('.tmp')
            temporary.write_text(json.dumps({'pid': os.getpid(), 'port': self.core.settings.port, 'nonce': self.core._instance_nonce}), encoding='utf-8')
            os.replace(temporary, metadata)
            def supervise(name, target):
                try:
                    target()
                    if not stop.is_set():
                        errors.put(RuntimeError(f'{name} stopped unexpectedly'))
                except BaseException as exc:
                    errors.put(RuntimeError(f'{name} failed: {type(exc).__name__}'))
                    log.error('%s failed (%s)', name, type(exc).__name__)
                finally:
                    stop.set()
            for remote, sock in zip((False, True), sockets):
                app = create_app(self.core, remote=remote, config_path=self.config_path)
                server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', log_level='warning', access_log=False, proxy_headers=False, timeout_graceful_shutdown=20))
                self.servers.append(server)
                thread = threading.Thread(target=supervise, args=('dashboard' if remote else 'management', lambda server=server, sock=sock: server.run(sockets=[sock])), name='aos-dashboard' if remote else 'aos-management')
                self.threads.append(thread)
                thread.start()
            deadline = time.monotonic() + startup_timeout
            while not all(server.started for server in self.servers):
                if stop.wait(.05):
                    raise errors.get_nowait() if not errors.empty() else RuntimeError('Startup interrupted')
                if time.monotonic() > deadline:
                    raise RuntimeError('HTTP server startup timed out')
            worker = Worker(self.core)
            thread = threading.Thread(target=supervise, args=('worker', lambda: worker.run(stop)), name='aos-worker')
            self.threads.append(thread)
            thread.start()
            while not stop.wait(.2):
                pass
            if not errors.empty():
                failure = errors.get_nowait()
        finally:
            stop.set()
            for server in self.servers:
                server.should_exit = True
            for thread in self.threads:
                thread.join(timeout=60)
            for sock in sockets:
                sock.close()
            if any(thread.is_alive() for thread in self.threads):
                # Keep the process lock while unfinished work still has access to state.
                _unfinished_runtimes.append(self)
                raise RuntimeError('Shutdown exceeded 60 seconds; process still owns the instance lock')
            (self.core.settings.data_dir / 'runtime.json').unlink(missing_ok=True)
            self.lock.__exit__()
        if failure:
            raise failure
