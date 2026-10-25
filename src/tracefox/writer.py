from __future__ import annotations

import atexit
import logging
import os
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, List, Optional, Tuple

from .context import suspended
from .entry import EntryUpdate, IncomingEntry

if TYPE_CHECKING:
    from .core import Tracefox

logger = logging.getLogger("tracefox")

Job = Tuple[List[IncomingEntry], List[EntryUpdate]]


class Writer:
    """Persists batches off the request path on a daemon thread (re-spawned after fork)."""

    def __init__(self, tracefox: "Tracefox"):
        self.tracefox = tracefox
        self._queue: "queue.Queue[Optional[Job]]" = queue.Queue(maxsize=10000)
        self._thread: Optional[threading.Thread] = None
        self._pid: Optional[int] = None
        self._lock = threading.Lock()
        self._last_prune = 0.0
        # Updates that arrived before their entry was stored (cross-process jobs): (deadline, update).
        self._deferred: List[Tuple[float, EntryUpdate]] = []
        atexit.register(self.flush, 5.0)

    def submit(self, entries: List[IncomingEntry], updates: List[EntryUpdate]) -> None:
        if not entries and not updates:
            return
        if not self.tracefox.config.async_writes:
            self._write((entries, updates))
            return
        self._ensure_thread()
        try:
            self._queue.put_nowait((entries, updates))
        except queue.Full:
            logger.warning("Tracefox write queue is full; dropping %d entries", len(entries))

    def flush(self, timeout: float = 10.0) -> None:
        if self._thread is None or not self._thread.is_alive() or self._pid != os.getpid():
            return
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive() and self._pid == os.getpid():
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive() and self._pid == os.getpid():
                return
            if self._pid is not None and self._pid != os.getpid():
                self._queue = queue.Queue(maxsize=10000)
                self.tracefox.storage.reset_after_fork()
            self._pid = os.getpid()
            self._thread = threading.Thread(target=self._run, name="tracefox-writer", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        suspended.set(True)
        while True:
            try:
                job = self._queue.get(timeout=1.0)
            except queue.Empty:
                self._retry_deferred()
                continue
            try:
                prepare = getattr(self.tracefox.storage, "prepare_thread", None)
                if prepare is not None:
                    prepare()
                if job is not None:
                    self._write(job)
                self._maybe_prune()
            finally:
                self._queue.task_done()

    def _write(self, job: Job) -> None:
        token = suspended.set(True)
        try:
            entries, updates = job
            storage = self.tracefox.storage
            if entries:
                storage.store(entries)
            if updates:
                deadline = time.monotonic() + 120
                self._deferred.extend((deadline, u) for u in storage.update(updates) or [])
            if entries and self._deferred:
                self._retry_deferred()
        except Exception:
            logger.exception("Tracefox failed to store entries")
        finally:
            suspended.reset(token)

    def _retry_deferred(self) -> None:
        if not self._deferred:
            return
        now = time.monotonic()
        pending = [(d, u) for d, u in self._deferred if d > now]
        self._deferred = []
        if not pending:
            return
        deadlines = {id(u): d for d, u in pending}
        token = suspended.set(True)
        try:
            missing = self.tracefox.storage.update([u for _, u in pending]) or []
            self._deferred.extend((deadlines[id(u)], u) for u in missing)
        except Exception:
            logger.exception("Tracefox failed to apply deferred updates")
        finally:
            suspended.reset(token)

    def _maybe_prune(self) -> None:
        hours = self.tracefox.config.auto_prune_hours
        if not hours or time.monotonic() - self._last_prune < 3600:
            return
        self._last_prune = time.monotonic()
        try:
            self.tracefox.storage.prune(datetime.now(timezone.utc) - timedelta(hours=hours))
        except Exception:
            logger.exception("Tracefox auto-prune failed")
