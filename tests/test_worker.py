import time
import threading
from datetime import datetime, timedelta, timezone

from academic_os.config import Settings
from academic_os.core import Core
from academic_os.db import Database
from academic_os.worker import Worker


def test_partial_provider_failure_retries_job_and_preserves_actual_result(tmp_path):
    settings = Settings(data_dir=tmp_path)
    core = Core(Database(settings.database_path), settings)
    core.enqueue("sources_sync", {}, "partial")
    worker = Worker(core, {"sources_sync": lambda _: {"local": {"ok": True}, "moodle": {"complete": False, "records": 2}}})
    worker.execute(worker.claim())
    job = core.list_jobs()[0]
    assert job["status"] == "failed"
    assert job["result"]["moodle"]["records"] == 2


def test_job_claim_is_exclusive_recovers_expired_and_runs_without_provider(tmp_path):
    settings = Settings(data_dir=tmp_path)
    core = Core(Database(settings.database_path), settings)
    first = Worker(core, handlers={"example": lambda payload: {"ok": True}})
    second = Worker(core, handlers={"example": lambda payload: {"ok": True}})
    core.enqueue("example", {}, "one")
    job = first.claim()
    assert job is not None
    assert second.claim() is None
    with core.db.transaction() as db:
        db.execute("UPDATE jobs SET lease_until=?", (time.time() - 1,))
    recovered = second.claim()
    assert recovered["id"] == job["id"]
    second.execute(recovered)
    assert core.list_jobs()[0]["status"] == "done"


def test_single_writer_lease_blocks_other_instance(tmp_path):
    settings = Settings(data_dir=tmp_path)
    core = Core(Database(settings.database_path), settings)
    first, second = Worker(core, handlers={}), Worker(core, handlers={})
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()


def test_notifier_generates_and_delivers_while_source_handler_is_blocked(tmp_path):
    settings = Settings(data_dir=tmp_path, mode='live', feishu={'chat_id': 'test'})
    core = Core(Database(settings.database_path), settings)
    stop, entered, delivered = threading.Event(), threading.Event(), threading.Event()
    core.create_item({'title': 'Due soon', 'due_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
    def blocking_source(_):
        entered.set()
        assert stop.wait(10)
        return {'complete': True}
    worker = Worker(core, {'sources_sync': blocking_source})
    worker.notifications.senders['feishu'] = lambda *_: delivered.set() or 'receipt'
    failures = []
    def run():
        try:
            worker.run(stop)
        except BaseException as exc:
            failures.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(5)
        assert delivered.wait(5), 'Notification dispatch was blocked by source synchronization'
        until = time.monotonic() + 5
        while not any(n['state'] == 'delivered' and n['channel'] == 'feishu' for n in worker.notifications.list()):
            assert time.monotonic() < until, 'Delivery receipt was not persisted while the source was blocked'
            time.sleep(.01)
    finally:
        stop.set()
        thread.join(10)
    assert not thread.is_alive()
    assert not failures


def test_notifier_failure_stops_and_surfaces_worker_failure(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    core = Core(Database(settings.database_path), settings)
    worker = Worker(core, {})
    def fail():
        raise RuntimeError('private provider detail')
    monkeypatch.setattr(worker.notifications, 'generate', fail)
    import pytest
    stop = threading.Event()
    with pytest.raises(RuntimeError, match='notifier') as error:
        worker.run(stop)
    assert 'private provider detail' not in str(error.value)
    assert stop.is_set()
    with core.db.connect() as db:
        assert db.execute('SELECT count(*) FROM locks').fetchone()[0] == 0
