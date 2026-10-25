from telepy import EntryType, EntryUpdate, IncomingEntry, dump
from telepy.storage import EntryQueryOptions


def record(telepy, type=EntryType.EVENT, **content):
    entry = IncomingEntry(type, {"name": "x", **content})
    telepy.record(entry)
    return entry


def test_entries_in_a_batch_share_a_batch_id(telepy, entries):
    with telepy.batch() as batch:
        record(telepy)
        record(telepy, EntryType.LOG, level="info", message="hi")
    rows = entries()
    assert len(rows) == 2
    assert {r["batch_id"] for r in rows} == {batch.id}
    assert rows[0]["content"]["hostname"] == telepy.hostname


def test_entries_outside_a_batch_are_stored_immediately(telepy, entries):
    record(telepy)
    record(telepy)
    rows = entries()
    assert len({r["batch_id"] for r in rows}) == 2


def test_tag_callbacks_and_tag_search(telepy, entries):
    telepy.tag(lambda entry: ["vip"] if entry.content.get("name") == "special" else [])
    record(telepy, name="special")
    record(telepy, name="plain")
    tagged = entries(EntryType.EVENT, tag="vip")
    assert [r["content"]["name"] for r in tagged] == ["special"]
    assert telepy.storage.find(tagged[0]["id"])["tags"] == ["vip"]


def test_filters_drop_entries_but_monitored_tags_survive(telepy, entries):
    telepy.storage.monitor(["keep-me"])
    telepy.filter(lambda entry: False)
    record(telepy, name="dropped")
    entry = IncomingEntry(EntryType.EVENT, {"name": "kept"}, tags=["keep-me"])
    telepy.record(entry)
    assert [r["content"]["name"] for r in entries()] == ["kept"]


def test_filter_batch_drops_the_whole_batch(telepy, entries):
    telepy.filter_batch(lambda batch: any(e.is_exception() for e in batch))
    with telepy.batch():
        record(telepy)
    assert entries() == []


def test_default_filter_outside_local_keeps_only_important_entries(telepy, entries):
    telepy.config.environment = "production"
    with telepy.batch():
        record(telepy)
        record(telepy, EntryType.REQUEST, response_status=200)
        record(telepy, EntryType.REQUEST, response_status=503)
    rows = entries()
    assert [r["content"].get("response_status") for r in rows] == [503]


def test_pause_and_resume_recording(telepy, entries):
    telepy.pause_recording()
    assert telepy.is_recording() is False
    record(telepy)
    telepy.resume_recording()
    record(telepy)
    assert len(entries()) == 1


def test_exception_family_only_latest_is_on_the_index(telepy, entries):
    for _ in range(3):
        telepy.record(IncomingEntry(EntryType.EXCEPTION, {"class": "E", "message": "m"}, family_hash="abc"))
    index = entries(EntryType.EXCEPTION)
    assert len(index) == 1
    assert index[0]["content"]["occurrences"] == 3
    assert len(entries(EntryType.EXCEPTION, family_hash="abc")) == 3


def test_updates_apply_to_unflushed_entries_and_to_stored_entries(telepy, entries):
    with telepy.batch():
        pending = record(telepy, EntryType.JOB, status="pending")
        telepy.record_update(EntryUpdate(pending.uuid, EntryType.JOB, {"status": "processed"}, tags_added=["done"]))
    stored = record(telepy, EntryType.BATCH, processedJobs=0)
    telepy.record_update(EntryUpdate(stored.uuid, EntryType.BATCH, increments={"processedJobs": 2}))
    assert telepy.storage.find(pending.uuid)["content"]["status"] == "processed"
    assert telepy.storage.find(pending.uuid)["tags"] == ["done"]
    assert telepy.storage.find(stored.uuid)["content"]["processedJobs"] == 2


def test_updates_for_missing_entries_are_retried(telepy):
    update = EntryUpdate("not-there-yet", EntryType.JOB, {"status": "processed"})
    telepy.writer._write(([], [update]))
    assert len(telepy.writer._deferred) == 1
    entry = IncomingEntry(EntryType.JOB, {"status": "pending"}, uuid="not-there-yet")
    telepy.writer._write(([entry], []))
    assert telepy.writer._deferred == []
    assert telepy.storage.find("not-there-yet")["content"]["status"] == "processed"


def test_prune_and_clear(telepy, entries):
    from datetime import datetime, timedelta, timezone

    old = IncomingEntry(EntryType.EVENT, {"name": "old"}, recorded_at=datetime.now(timezone.utc) - timedelta(hours=48))
    old_exception = IncomingEntry(EntryType.EXCEPTION, {"class": "E"}, recorded_at=old.recorded_at)
    telepy.record(old)
    telepy.record(old_exception)
    record(telepy, name="new")
    assert telepy.prune(hours=24, keep_exceptions=True) == 1
    assert {r["type"] for r in entries()} == {EntryType.EVENT, EntryType.EXCEPTION}
    telepy.clear()
    assert entries() == []


def test_dump_returns_its_value(telepy, entries):
    assert dump({"a": 1}) == {"a": 1}
    row = entries(EntryType.DUMP)[0]
    assert row["content"]["value"] == {"a": 1}
    assert row["content"]["file"].endswith("test_core.py")


def test_disabled_telepy_records_nothing(telepy, entries):
    telepy.config.enabled = False
    record(telepy)
    telepy.config.enabled = True
    assert telepy.storage.get(None, EntryQueryOptions()) == []
