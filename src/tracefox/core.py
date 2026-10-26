from __future__ import annotations

import logging
import time
import weakref
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from .config import TracefoxConfig
from .context import Batch, current_batch, suspended
from .entry import EntryType, EntryUpdate, IncomingEntry
from .storage import DatabaseStorage, Storage
from .utils import hostname as _hostname
from .writer import Writer

logger = logging.getLogger("tracefox")

TagCallback = Callable[[IncomingEntry], Optional[List[str]]]
EntryFilter = Callable[[IncomingEntry], bool]
BatchFilter = Callable[[List[IncomingEntry]], bool]


class Tracefox:
    def __init__(self, config: Optional[TracefoxConfig] = None, storage: Optional[Storage] = None):
        self.config = config or TracefoxConfig()
        self._storage = storage
        self.watchers: Dict[str, Any] = {}
        self._tag_callbacks: List[TagCallback] = []
        self._filters: List[EntryFilter] = []
        self._batch_filters: List[BatchFilter] = []
        self._recording_cache: Tuple[float, bool] = (0.0, True)
        self._monitored_cache: Tuple[float, List[str]] = (0.0, [])
        self._hostname: Optional[str] = None
        self._unflushed: "weakref.WeakValueDictionary[str, IncomingEntry]" = weakref.WeakValueDictionary()
        self.writer = Writer(self)
        self.started = False

    # -- setup -------------------------------------------------------------------

    @property
    def storage(self) -> Storage:
        if self._storage is None:
            self._storage = DatabaseStorage(
                None if self.config.engine is not None else self.config.database_url,
                engine_options=self.config.database_engine_options,
                auto_migrate=self.config.auto_migrate,
                engine=self.config.engine,
            )
        return self._storage

    @storage.setter
    def storage(self, value: Storage) -> None:
        self._storage = value

    def configure(self, **options: Any) -> "Tracefox":
        old = (self.config.database_url, self.config.engine)
        self.config.update(**options)
        if (self.config.database_url, self.config.engine) != old and isinstance(self._storage, DatabaseStorage):
            self._storage = None
        return self

    def start(self, **options: Any) -> "Tracefox":
        """Apply options and install every enabled watcher. Safe to call more than once."""
        if options:
            self.configure(**options)
        if not self.config.enabled:
            return self
        from .watchers import WATCHERS

        for name, watcher_cls in WATCHERS.items():
            if name in self.watchers or not self.config.watcher_enabled(name):
                continue
            watcher = watcher_cls(self, self.config.watcher_options(name))
            try:
                watcher.register()
            except Exception:
                logger.exception("Tracefox could not register the %s watcher", name)
                continue
            self.watchers[name] = watcher
        self.started = True
        return self

    def stop(self) -> None:
        for watcher in list(self.watchers.values()):
            try:
                watcher.unregister()
            except Exception:
                logger.exception("Tracefox could not unregister %s", watcher.name)
        self.watchers.clear()
        self.started = False

    def watcher(self, name: str) -> Any:
        return self.watchers.get(name)

    @property
    def hostname(self) -> str:
        if self._hostname is None:
            self._hostname = self.config.hostname or _hostname()
        return self._hostname

    # -- recording state -----------------------------------------------------------

    def is_recording(self) -> bool:
        if not self.config.enabled:
            return False
        stamp, value = self._recording_cache
        if time.monotonic() - stamp > 2:
            token = suspended.set(True)
            try:
                value = self.storage.get_setting("recording", "1") != "0"
            except Exception:
                value = True
            finally:
                suspended.reset(token)
            self._recording_cache = (time.monotonic(), value)
        return value

    def pause_recording(self) -> None:
        self._set_recording(False)

    def resume_recording(self) -> None:
        self._set_recording(True)

    def _set_recording(self, value: bool) -> None:
        with self.without_recording():
            self.storage.set_setting("recording", "1" if value else "0")
        self._recording_cache = (time.monotonic(), value)

    def should_record(self) -> bool:
        return self.config.enabled and not suspended.get() and self.is_recording()

    def monitored_tags(self) -> List[str]:
        stamp, tags = self._monitored_cache
        if time.monotonic() - stamp > 5:
            token = suspended.set(True)
            try:
                tags = self.storage.monitored_tags()
            except Exception:
                tags = []
            finally:
                suspended.reset(token)
            self._monitored_cache = (time.monotonic(), tags)
        return tags

    def forget_monitored_cache(self) -> None:
        self._monitored_cache = (0.0, [])

    @contextmanager
    def without_recording(self) -> Iterator[None]:
        token = suspended.set(True)
        try:
            yield
        finally:
            suspended.reset(token)

    # -- batches -------------------------------------------------------------------

    def start_batch(self, user: Optional[Dict[str, Any]] = None) -> Tuple[Batch, Any]:
        batch = Batch(user=user)
        return batch, current_batch.set(batch)

    def end_batch(self, batch: Batch, token: Any = None) -> None:
        if token is not None:
            try:
                current_batch.reset(token)
            except ValueError:
                current_batch.set(None)
        self.store_batch(batch)

    @contextmanager
    def batch(self, user: Optional[Dict[str, Any]] = None) -> Iterator[Batch]:
        """Group every entry recorded inside the block (a request, a job, a command)."""
        batch, token = self.start_batch(user)
        try:
            yield batch
        finally:
            self.end_batch(batch, token)

    def current_batch(self) -> Optional[Batch]:
        return current_batch.get()

    # -- recording -------------------------------------------------------------------

    def record(self, entry: IncomingEntry) -> None:
        if not self.should_record():
            return
        entry.content = {"hostname": self.hostname, **entry.content}
        batch = current_batch.get()
        if batch is None:
            self.store_batch(Batch(entries=[entry]))
        else:
            self._unflushed[entry.uuid] = entry
            batch.entries.append(entry)

    def record_update(self, update: EntryUpdate) -> None:
        if not self.config.enabled or suspended.get():
            return
        pending = self._unflushed.get(update.uuid)
        if pending is not None and pending.type == update.type:
            # The entry's batch hasn't been written yet (e.g. a job finishing inside its dispatching request).
            pending.content.update(update.changes)
            for key, delta in update.increments.items():
                pending.content[key] = (pending.content.get(key) or 0) + delta
            pending.tags = [t for t in pending.tags if t not in update.tags_removed]
            pending.add_tags(update.tags_added)
            return
        batch = current_batch.get()
        if batch is None:
            self.store_batch(Batch(updates=[update]))
        else:
            batch.updates.append(update)

    def store_batch(self, batch: Batch) -> None:
        if not batch.entries and not batch.updates and not batch.finalizers:
            return
        for entry in batch.entries:
            self._unflushed.pop(entry.uuid, None)
        token = suspended.set(True)
        try:
            for finalize in batch.finalizers:
                try:
                    finalize(batch)
                except Exception:
                    logger.exception("Tracefox batch finalizer failed")
            entries = self._prepare(batch)
            if entries is None:
                return
            self.writer.submit(entries, list(batch.updates))
        finally:
            suspended.reset(token)

    def _prepare(self, batch: Batch) -> Optional[List[IncomingEntry]]:
        entries = batch.entries
        for entry in entries:
            entry.batch_id = batch.id
            if batch.user and "user" not in entry.content:
                entry.set_user(batch.user)
            for callback in self._tag_callbacks:
                try:
                    entry.add_tags(callback(entry) or [])
                except Exception:
                    logger.exception("Tracefox tag callback failed")
        if not entries:
            return []
        monitored = self.monitored_tags()
        if self.config.default_filter and not self.config.records_everything:
            entries = [
                e for e in entries
                if e.is_reportable_exception() or e.is_failed_request() or e.is_failed_job()
                or e.is_scheduled_task() or e.is_slow_query() or e.has_monitored_tag(monitored)
            ]
        for entry_filter in self._filters:
            entries = [e for e in entries if e.has_monitored_tag(monitored) or entry_filter(e)]
        for batch_filter in self._batch_filters:
            if not batch_filter(entries):
                return None
        return entries

    def flush(self, timeout: float = 10.0) -> None:
        """Block until queued entries have been written (useful in tests and CLIs)."""
        self.writer.flush(timeout)

    # -- customisation ---------------------------------------------------------------

    def tag(self, callback: TagCallback) -> TagCallback:
        self._tag_callbacks.append(callback)
        return callback

    def filter(self, callback: EntryFilter) -> EntryFilter:
        self._filters.append(callback)
        return callback

    def filter_batch(self, callback: BatchFilter) -> BatchFilter:
        self._batch_filters.append(callback)
        return callback

    def hide_request_headers(self, *headers: str) -> None:
        self.config.hidden_request_headers.extend(h.lower() for h in headers)

    def hide_request_parameters(self, *params: str) -> None:
        self.config.hidden_request_parameters.extend(params)

    def hide_response_parameters(self, *params: str) -> None:
        self.config.hidden_response_parameters.extend(params)

    # -- maintenance -----------------------------------------------------------------

    def prune(self, hours: int = 24, keep_exceptions: bool = False) -> int:
        with self.without_recording():
            return self.storage.prune(datetime.now(timezone.utc) - timedelta(hours=hours), keep_exceptions)

    def clear(self) -> None:
        with self.without_recording():
            self.storage.clear()

    # -- manual instrumentation ------------------------------------------------------

    def dump(self, *values: Any) -> Any:
        from .watchers.dump import record_dump

        record_dump(self, values)
        return values[0] if len(values) == 1 else values

    def record_event(self, name: str, payload: Any = None, listeners: Optional[List[str]] = None, broadcast: bool = False) -> None:
        from .watchers.event import EventWatcher

        EventWatcher.record_event(self, name, payload, listeners or [], broadcast)

    def record_notification(
        self, notification: Any, notifiable: Any, channel: str, response: Any = None, queued: bool = False,
    ) -> None:
        from .watchers.notification import NotificationWatcher

        NotificationWatcher.record_notification(self, notification, notifiable, channel, response, queued)

    def record_gate(self, ability: str, result: Any, arguments: Any = None) -> None:
        from .watchers.gate import GateWatcher

        GateWatcher.record_gate(self, ability, result, arguments)

    def record_cache(self, action: str, key: str, value: Any = None, expiration: Any = None) -> None:
        from .watchers.cache import CacheWatcher

        CacheWatcher.record_cache(self, action, key, value, expiration)

    def record_mail(self, **content: Any) -> None:
        from .watchers.mail import MailWatcher

        MailWatcher.record_content(self, content)

    def gate(self, ability: Optional[str] = None) -> Callable:
        """Decorator recording the boolean result of an authorization check."""
        from .watchers.gate import gate_decorator

        return gate_decorator(self, ability)

    def job(self, name: Optional[str] = None, queue: str = "default", connection: str = "sync") -> Callable:
        """Decorator recording a function call as a processed / failed job."""
        from .watchers.job import job_decorator

        return job_decorator(self, name, queue, connection)

    @contextmanager
    def command(self, name: str, arguments: Any = None, options: Any = None) -> Iterator[None]:
        from .watchers.command import record_command

        with record_command(self, name, arguments or {}, options or {}):
            yield

    def entry_types(self) -> Tuple[str, ...]:
        return EntryType.ALL


tracefox = Tracefox()
