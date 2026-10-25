from tracefox import EntryType, EntryUpdate, IncomingEntry, dump
from tracefox.storage import EntryQueryOptions


def record(tracefox, type=EntryType.EVENT, **content):
    entry = IncomingEntry(type, {"name": "x", **content})
    tracefox.record(entry)
    return entry


def test_entries_in_a_batch_share_a_batch_id(tracefox, entries):
    with tracefox.batch() as batch:
        record(tracefox)
        record(tracefox, EntryType.LOG, level="info", message="hi")
    rows = entries()
    assert len(rows) == 2
    assert {r["batch_id"] for r in rows} == {batch.id}
    assert rows[0]["content"]["hostname"] == tracefox.hostname


def test_entries_outside_a_batch_are_stored_immediately(tracefox, entries):
    record(tracefox)
    record(tracefox)
    rows = entries()
    assert len({r["batch_id"] for r in rows}) == 2


def test_tag_callbacks_and_tag_search(tracefox, entries):
    tracefox.tag(lambda entry: ["vip"] if entry.content.get("name") == "special" else [])
    record(tracefox, name="special")
    record(tracefox, name="plain")
    tagged = entries(EntryType.EVENT, tag="vip")
    assert [r["content"]["name"] for r in tagged] == ["special"]
    assert tracefox.storage.find(tagged[0]["id"])["tags"] == ["vip"]


def test_filters_drop_entries_but_monitored_tags_survive(tracefox, entries):
    tracefox.storage.monitor(["keep-me"])
    tracefox.filter(lambda entry: False)
    record(tracefox, name="dropped")
    entry = IncomingEntry(EntryType.EVENT, {"name": "kept"}, tags=["keep-me"])
    tracefox.record(entry)
    assert [r["content"]["name"] for r in entries()] == ["kept"]


def test_filter_batch_drops_the_whole_batch(tracefox, entries):
    tracefox.filter_batch(lambda batch: any(e.is_exception() for e in batch))
    with tracefox.batch():
        record(tracefox)
    assert entries() == []


def test_default_filter_outside_local_keeps_only_important_entries(tracefox, entries):
    tracefox.config.environment = "production"
    with tracefox.batch():
        record(tracefox)
        record(tracefox, EntryType.REQUEST, response_status=200)
        record(tracefox, EntryType.REQUEST, response_status=503)
    rows = entries()
    assert [r["content"].get("response_status") for r in rows] == [503]


def test_pause_and_resume_recording(tracefox, entries):
    tracefox.pause_recording()
    assert tracefox.is_recording() is False
    record(tracefox)
    tracefox.resume_recording()
    record(tracefox)
    assert len(entries()) == 1


def test_exception_family_only_latest_is_on_the_index(tracefox, entries):
    for _ in range(3):
        tracefox.record(IncomingEntry(EntryType.EXCEPTION, {"class": "E", "message": "m"}, family_hash="abc"))
    index = entries(EntryType.EXCEPTION)
    assert len(index) == 1
    assert index[0]["content"]["occurrences"] == 3
    assert len(entries(EntryType.EXCEPTION, family_hash="abc")) == 3


def test_updates_apply_to_unflushed_entries_and_to_stored_entries(tracefox, entries):
    with tracefox.batch():
        pending = record(tracefox, EntryType.JOB, status="pending")
        tracefox.record_update(EntryUpdate(pending.uuid, EntryType.JOB, {"status": "processed"}, tags_added=["done"]))
    stored = record(tracefox, EntryType.BATCH, processedJobs=0)
    tracefox.record_update(EntryUpdate(stored.uuid, EntryType.BATCH, increments={"processedJobs": 2}))
    assert tracefox.storage.find(pending.uuid)["content"]["status"] == "processed"
    assert tracefox.storage.find(pending.uuid)["tags"] == ["done"]
    assert tracefox.storage.find(stored.uuid)["content"]["processedJobs"] == 2


def test_updates_for_missing_entries_are_retried(tracefox):
    update = EntryUpdate("not-there-yet", EntryType.JOB, {"status": "processed"})
    tracefox.writer._write(([], [update]))
    assert len(tracefox.writer._deferred) == 1
    entry = IncomingEntry(EntryType.JOB, {"status": "pending"}, uuid="not-there-yet")
    tracefox.writer._write(([entry], []))
    assert tracefox.writer._deferred == []
    assert tracefox.storage.find("not-there-yet")["content"]["status"] == "processed"


def test_prune_and_clear(tracefox, entries):
    from datetime import datetime, timedelta, timezone

    old = IncomingEntry(EntryType.EVENT, {"name": "old"}, recorded_at=datetime.now(timezone.utc) - timedelta(hours=48))
    old_exception = IncomingEntry(EntryType.EXCEPTION, {"class": "E"}, recorded_at=old.recorded_at)
    tracefox.record(old)
    tracefox.record(old_exception)
    record(tracefox, name="new")
    assert tracefox.prune(hours=24, keep_exceptions=True) == 1
    assert {r["type"] for r in entries()} == {EntryType.EVENT, EntryType.EXCEPTION}
    tracefox.clear()
    assert entries() == []


def test_dump_returns_its_value(tracefox, entries):
    assert dump({"a": 1}) == {"a": 1}
    row = entries(EntryType.DUMP)[0]
    assert row["content"]["value"] == {"a": 1}
    assert row["content"]["file"].endswith("test_core.py")


def test_disabled_tracefox_records_nothing(tracefox, entries):
    tracefox.config.enabled = False
    record(tracefox)
    tracefox.config.enabled = True
    assert tracefox.storage.get(None, EntryQueryOptions()) == []
