"""Reproducible disposable-data benchmark; never accepts a live database path."""
from contextlib import contextmanager
from pathlib import Path
import argparse
import ctypes
import gzip
import json
import platform
import sqlite3
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from academic_os.config import Settings  # noqa: E402
from academic_os.core import Core  # noqa: E402
from academic_os.db import Database, dumps  # noqa: E402
from academic_os.mail_digest import digest  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument('--output', type=Path, required=True)
args = p.parse_args()


def memory():
    if sys.platform != 'win32':
        return None

    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [(n, ctypes.c_size_t) for n in ['peak', 'working', 'pool_peak', 'pool', 'nonpaged_peak', 'nonpaged', 'pagefile', 'pagefile_peak']]
    c = Counters()
    c.cb = ctypes.sizeof(c)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(c), c.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return {'working_set_bytes': c.working, 'peak_working_set_bytes': c.peak}


result = {'platform': platform.platform(), 'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version, 'measurement': '5s SQL connection budget, timeout recorded not hidden; direct Core calls, not HTTP latency; first-call not OS-cold; 5 warm samples, p95=nearest-rank maximum; no SLA claim', 'sizes': []}
for count in [1000, 10000]:
    with tempfile.TemporaryDirectory(prefix='academic-benchmark-') as directory:
        settings = Settings(data_dir=Path(directory), secure_cookies=False)
        core = Core(Database(settings.database_path), settings)
        template = core.create_item({'title': '虚构作业', 'estimate_minutes': 30}, 'benchmark', 'seed')
        with core.db.transaction() as db:
            db.execute('DELETE FROM items')
            for i in range(count):
                key = f'fixture:m{i}'
                doc = {'id': f'm{i}', 'key': key, 'account': 'fixture', 'subject': f'虚构课程通知 {i}', 'from': 'teacher@example.test', 'body': '仅用于性能测量，不是真实邮件。' * 200 + (' needle' if i % 10 == 0 else ''), 'received_at': '2026-10-17T00:00:00Z'}
                db.execute('INSERT INTO mail VALUES(?,?,?,?)', (key, 'fixture', dumps(doc), doc['received_at']))
                if i < 1000:
                    pending = i % 10 == 0
                    item = {**template, 'id': f'fixture-item-{i}', 'title': f'虚构事项 {i}', 'mail_keys': [key], 'manual_origin': not pending, 'evidence': [{'source': 'gmail:fixture', 'source_id': f'm{i}:date'}] if pending else [], 'mail_review': {'state': 'pending', 'revision': 0} if pending else {'state': 'confirmed', 'revision': 1}}
                    db.execute('INSERT INTO items VALUES(?,?,?,?)', (item['id'], dumps(item), item['version'], item['updated_at']))
        original_connect = core.db.connect

        @contextmanager
        def bounded_connect():
            with original_connect() as db:
                deadline = time.perf_counter() + 5
                db.set_progress_handler(lambda: int(time.perf_counter() > deadline), 1000)
                yield db
        core.db.connect = bounded_connect
        cases = {'digest_page_20': lambda: digest(core, limit=20, body_chars=500), 'digest_search': lambda: digest(core, limit=20, body_chars=500, q='needle'), 'digest_pending_filter': lambda: digest(core, limit=20, body_chars=500, review='pending'), 'detail': lambda: core.get_mail('fixture:m0'), 'formal_items_900': lambda: core.list_items(view='formal')}
        measurements = {}
        for name, call in cases.items():
            start = time.perf_counter()
            try:
                value = call()
            except sqlite3.OperationalError as error:
                if 'interrupted' not in str(error):
                    raise
                measurements[name] = {'status': 'SQL budget exceeded', 'sql_connection_budget_ms': 5000, 'elapsed_ms': round((time.perf_counter() - start) * 1000, 3), 'warm_samples': 0}
                continue
            first = (time.perf_counter() - start) * 1000
            samples = []
            for _ in range(5):
                start = time.perf_counter()
                value = call()
                samples.append((time.perf_counter() - start) * 1000)
            measurements[name] = {'first_call_ms': round(first, 3), 'p50_ms': round(statistics.median(samples), 3), 'p95_ms': round(max(samples), 3), 'samples_ms': [round(v, 3) for v in samples], 'response_bytes': len(dumps(value).encode())}
        with core.db.connect() as db:
            plan = [dict(r) for r in db.execute('EXPLAIN QUERY PLAN SELECT id FROM mail ORDER BY julianday(received_at) DESC,id DESC LIMIT 20')]
            indexes = [r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")]
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        result['sizes'].append({'mail_rows': count, 'item_rows': 1000, 'formal_rows': 900, 'pending_rows': 100, 'database_bytes': settings.database_path.stat().st_size, 'indexes': indexes, 'page_plan': plan, 'measurements': measurements, 'python_process_memory': memory()})
board = Path(__file__).resolve().parents[1] / 'src/academic_os/web/board'
files = [f for f in board.rglob('*') if f.is_file() and f.suffix in {'.js', '.css'}]
result['static_assets'] = {'note': 'all board JS/CSS assets, not one page download; gzip level9 for comparison only', 'files': len(files), 'raw_bytes': sum(f.stat().st_size for f in files), 'gzip_bytes': sum(len(gzip.compress(f.read_bytes(), mtime=0)) for f in files), 'gsap_raw_bytes': (board / 'vendor/gsap-3.15.0.min.js').stat().st_size}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print('BENCHMARK_OK: temporary 1000/10000-mail fixtures; 1000 items; 5 warm samples; no live database or provider; JSON saved.')
