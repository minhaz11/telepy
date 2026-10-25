import time

import pytest

from telepy import EntryType

celery = pytest.importorskip("celery")
from celery import Celery, group  # noqa: E402
from celery.contrib.testing.worker import start_worker  # noqa: E402

app = Celery("tests", broker="memory://", backend="cache+memory://")
app.conf.update(task_always_eager=False, worker_hijack_root_logger=False, broker_connection_retry_on_startup=False)


@app.task(name="tests.add")
def add(x, y):
    return x + y


@app.task(name="tests.fail")
def fail():
    raise ValueError("task blew up")


def wait_for(fetch, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = fetch()
        if predicate(rows):
            return rows
        time.sleep(0.05)
    return fetch()


@pytest.fixture
def worker(telepy):
    with start_worker(app, perform_ping_check=False, pool="solo", loglevel="WARNING"):
        yield


def test_dispatched_tasks_are_updated_by_the_worker(worker, entries, telepy):
    with telepy.batch() as request_batch:
        add.delay(2, 3)
        fail.delay()
    jobs = wait_for(lambda: entries(EntryType.JOB), lambda rows: {r["content"]["status"] for r in rows} == {"processed", "failed"})
    by_name = {r["content"]["name"]: r for r in jobs}
    assert by_name["tests.add"]["content"]["status"] == "processed"
    assert by_name["tests.add"]["content"]["data"]["args"] == [2, 3]
    assert by_name["tests.add"]["batch_id"] == request_batch.id
    failed = by_name["tests.fail"]["content"]
    assert failed["status"] == "failed"
    assert "task blew up" in failed["exception"]["message"]
    assert entries(EntryType.EXCEPTION)[0]["content"]["message"] == "task blew up"


def test_groups_are_recorded_as_batches(worker, entries):
    group(add.s(1, 1), add.s(2, 2)).apply_async()
    batches = wait_for(lambda: entries(EntryType.BATCH), lambda rows: rows and rows[0]["content"]["processedJobs"] == 2)
    content = batches[0]["content"]
    assert content["totalJobs"] == 2
    assert content["pendingJobs"] == 0
    assert content["processedJobs"] == 2
    assert len(entries(EntryType.JOB, tag=f"Batch:{batches[0]['id']}")) == 2
